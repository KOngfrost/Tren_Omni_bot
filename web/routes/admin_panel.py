"""
Маршруты управления администраторами.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- require_auth проверяет сессию + роль (суперадмин/админ/наблюдатель)
- IDOR: админ видит только администраторов своего отдела (суперадмин — всех)
- Назначение суперадминов: только действующий суперадмин
"""

import logging
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.admin_presence import get_online_ids
from core.database import async_session_maker
from core.models import Admin, Department, Log, User, UserRole, WebRole, WebUser
from core.time_utils import format_app_datetime, humanize_last_seen, now_app_tz
from web.dependencies import (
    get_admin_scope,
    is_superadmin,
    is_temporary,
    require_auth,
)
from web.routes.auth import require_crud_rate_limit
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.security.passwords import hash_password
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Временный администратор ведёт диалог с отделом, но не управляет учётными
# записями. Сообщение единое для всех защищаемых эндпоинтов.
TEMPORARY_ADMIN_FORBIDDEN = "Временный администратор не имеет прав на создание или удаление учетных записей."


def _deny_temporary_admin(request: Request, user: dict):
    """Отклонить запрос временного администратора на управление учётными записями.

    Возвращает RedirectResponse либо None, если ограничение не применимо.
    Вызывается ПЕРВЫМ в эндпоинтах, до любых действий с БД.
    """
    if not is_temporary(user):
        return None
    request.session["flash_error"] = TEMPORARY_ADMIN_FORBIDDEN
    return RedirectResponse(url="/admin/admins/", status_code=302)


@router.get("/")
async def admins_page(request: Request, user=Depends(require_auth)):
    """Страница управления администраторами с иерархической сортировкой.

    Иерархия отображения:
    1. Суперадминистраторы (role=SUPERADMIN) — без привязки к отделу, полный доступ
    2. Администраторы отделов (role=ADMIN) — привязаны к конкретному отделу

    IDOR-защита:
    - Суперадмин видит всех админов.
    - Обычный админ — только тех, кто привязан к его отделу.

    Каждая карточка получает статус присутствия (Онлайн/Офлайн/Истёк) на основе
    ключей admin:heartbeat:* в Redis и полей WebUser.last_login_at/expires_at.
    """
    admins: list[Admin] = []
    departments: list[Department] = []
    db_error: bool = False

    # Присутствие читается ВНЕ транзакции с БД: сбой Redis не должен ломать
    # страницу целиком — в худшем случае все карточки покажут «Офлайн».
    try:
        online_ids = await get_online_ids()
    except Exception as exc:  # pragma: no cover - защитный контур
        logger.debug("Не удалось прочитать индикатор активности: %s", exc)
        online_ids = set()

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            dept_stmt = select(Department).order_by(Department.name)
            if not is_super:
                dept_stmt = dept_stmt.where(Department.id == dept_id)
            departments = list((await session.execute(dept_stmt)).scalars().all())

            # Загружаем админов с данными VK-профиля и веб-аккаунта
            admins_stmt = (
                select(Admin)
                .options(
                    selectinload(Admin.user),
                    selectinload(Admin.department),
                    selectinload(Admin.web_user),
                )
                # Иерархическая сортировка: суперадмины первыми, затем по ID
                .order_by(
                    # SUPERADMIN (значение "superadmin") должен быть первым
                    # Используем case для явного порядка: 0 для суперадмина, 1 для остальных
                    case(
                        (Admin.role == UserRole.SUPERADMIN, 0),
                        else_=1,
                    ),
                    Admin.id,
                )
            )
            if not is_super:
                admins_stmt = admins_stmt.where(Admin.department_id == dept_id)
            admins = list((await session.execute(admins_stmt)).scalars().all())

            # Загружаем также веб-пользователей без привязки к VK ID (временные / QA)
            qa_stmt = (
                select(WebUser)
                .options(selectinload(WebUser.department))
                .where(WebUser.admin_id.is_(None))
                .order_by(WebUser.id)
            )
            if not is_super:
                qa_stmt = qa_stmt.where(WebUser.department_id == dept_id)
            qa_users = list((await session.execute(qa_stmt)).scalars().all())
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить администраторов: %s", e)

    return templates.TemplateResponse(
        "admins.html",
        {
            "request": request,
            "user": user,
            "admins": admins,
            "qa_users": qa_users,
            "departments": departments,
            "db_error": db_error,
            "active": "admins",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "created_credentials": _pop_fresh_created_credentials(request.session),
            "session_id": request.state.session_id,
            # Индикатор активности и права временного администратора.
            "presence_of": lambda wu: _presence_status(wu, online_ids),
            "viewer_is_temporary": is_temporary(user),
            "online_count": len(online_ids),
        },
    )


def _presence_status(web_user: WebUser | None, online_ids: set[int]) -> dict:
    """Собрать данные индикатора активности для карточки администратора.

    Логика приоритетов:
    1. Учётка отключена или срок истёк -> «Истёк» (красный), даже если ключ
       heartbeat ещё жив: доступ всё равно заблокирован на входе.
    2. Активен менее 15 минут (живой ключ в Redis) -> «Онлайн» (зелёный).
    3. Иначе -> «Офлайн» с временем последнего визита по МСК.
    """
    if web_user is None:
        return {
            "state": "offline",
            "label": "Офлайн",
            "icon": "⚪",
            "css": "status-dot-offline",
            "badge": "badge-secondary",
            "last_seen": "Веб-доступ не выдан",
            "expires_label": "",
            "has_expiry": False,
        }

    expires_label = ""
    if web_user.expires_at:
        expires_label = format_app_datetime(web_user.expires_at, "%d.%m.%Y %H:%M")

    base = {
        "expires_label": expires_label,
        "has_expiry": bool(expires_label),
        "last_seen": humanize_last_seen(web_user.last_login_at),
    }

    if not web_user.is_active or web_user.is_expired:
        return {
            **base,
            "state": "expired",
            "label": "Истёк",
            "icon": "🔴",
            "css": "status-dot-expired",
            "badge": "badge-danger",
        }

    if web_user.id in online_ids:
        return {
            **base,
            "state": "online",
            "label": "Онлайн",
            "icon": "🟢",
            "css": "status-dot-online",
            "badge": "badge-completed",
        }

    return {
        **base,
        "state": "offline",
        "label": "Офлайн",
        "icon": "⚪",
        "css": "status-dot-offline",
        "badge": "badge-secondary",
    }


def _pop_fresh_created_credentials(session: dict) -> dict | None:
    """Извлечь одноразовые учетные данные нового администратора с ограничением по времени (TTL 5 мин)."""
    creds = session.pop("created_credentials", None)
    if not isinstance(creds, dict):
        return None
    created_at = creds.get("created_at", 0)
    if time.time() - created_at > 300:
        return None
    return creds


@router.post("/qa")
async def add_qa_admin(request: Request, user=Depends(require_auth)):
    """Создание тестового QA-администратора без VK ID (обратная совместимость)."""
    return await add_admin(request, user)


def _resolve_expiry(form, admin_type: str):
    """Рассчитать срок действия для временного администратора.

    Возвращает (expires_at, error). error — готовый текст для flash_error,
    если значение некорректно. Для постоянных админов срок не задаётся.
    """
    if admin_type != "temp":
        return None, None

    preset = str(form.get("duration_preset", "24")).strip()
    try:
        if preset == "custom":
            hours = int(form.get("custom_hours", 0))
            if hours < 1:
                return None, "Количество часов должно быть положительным числом"
        else:
            hours = int(preset)
    except (ValueError, TypeError):
        return None, "Некорректное значение срока действия"

    if hours <= 0:
        return None, None  # 0 = бессрочно
    return now_app_tz() + timedelta(hours=hours), None


@router.post("/")
async def add_admin(request: Request, user=Depends(require_auth)):
    """Добавление нового администратора (с VK ID или временного/QA с настраиваемым сроком).

    Безопасность: временный администратор не может создавать учётные записи
    даже с ролью superadmin — проверка is_temporary выполняется до разбора формы.
    """
    denied = _deny_temporary_admin(request, user)
    if denied is not None:
        return denied

    await require_crud_rate_limit(request)
    form = await request.form()

    # Определение типа: vk или temp (qa)
    admin_type = str(form.get("admin_type", "vk")).strip().lower()
    # Если путь запроса был /qa, считаем admin_type = "temp"
    if request.url.path.rstrip("/").endswith("/qa"):
        admin_type = "temp"

    role_str = str(form.get("role", "admin")).strip().lower()
    if role_str not in ("admin", "superadmin"):
        request.session["flash_error"] = "Неизвестная роль администратора"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    allowed_roles = _check_role_permissions(user, role_str)
    if allowed_roles is not None:
        request.session["flash_error"] = allowed_roles
        return RedirectResponse(url="/admin/admins/", status_code=302)

    raw_dept_id = form.get("department_id")
    dept_id = int(raw_dept_id) if raw_dept_id and str(raw_dept_id).strip() else None
    if not is_superadmin(user):
        dept_id = user_get_department_id(user)

    username = sanitize_html(str(form.get("username", "")).strip())
    password = str(form.get("password", "")).strip()
    if password and len(password) < 8:
        request.session["flash_error"] = "Пароль должен быть не короче 8 символов"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    web_role = WebRole.SUPERADMIN if role_str == "superadmin" else WebRole.DEPARTMENT_ADMIN

    # Расчёт срока действия для временного администратора
    expires_at, expiry_error = _resolve_expiry(form, admin_type)
    if expiry_error:
        request.session["flash_error"] = expiry_error
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            if admin_type == "temp":
                return await _create_temp_admin(
                    request,
                    session,
                    username=username,
                    password=password,
                    web_role=web_role,
                    dept_id=dept_id,
                    expires_at=expires_at,
                )
            return await _create_permanent_admin(
                request,
                session,
                form,
                role_str=role_str,
                dept_id=dept_id,
                username=username,
                password=password,
                user=user,
            )
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse(url="/admin/admins/", status_code=302)
    except Exception:
        logger.exception("Не удалось добавить администратора")
        request.session["flash_error"] = "Не удалось сохранить администратора. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)


async def _create_temp_admin(
    request: Request,
    session: AsyncSession,
    *,
    username: str,
    password: str,
    web_role: WebRole,
    dept_id: int | None,
    expires_at,
) -> RedirectResponse:
    """Создать временного / QA-администратора (без привязки к VK Admin)."""
    if not username:
        username = f"qa_{secrets.token_hex(4)}"
    if not password:
        password = secrets.token_urlsafe(12)

    existing = await session.scalar(select(WebUser).where(WebUser.username == username))
    if existing:
        request.session["flash_error"] = f"Логин '{username}' уже занят"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    session.add(
        WebUser(
            username=username,
            password_hash=hash_password(password),
            role=web_role,
            department_id=dept_id,
            admin_id=None,
            is_active=True,
            expires_at=expires_at,
        )
    )
    await session.commit()

    # Срок показываем в МСК — он вычислен в now_app_tz().
    duration_str = (
        f"действует до {format_app_datetime(expires_at, '%d.%m.%Y %H:%M')} МСК"
        if expires_at
        else "бессрочный доступ"
    )
    request.session["flash_success"] = (
        f"Временный/QA администратор '{username}' успешно создан ({duration_str})."
    )
    request.session["created_credentials"] = {
        "username": username,
        "password": password,
        "created_at": time.time(),
    }
    return RedirectResponse(url="/admin/admins/", status_code=302)


async def _create_permanent_admin(
    request: Request,
    session: AsyncSession,
    form,
    *,
    role_str: str,
    dept_id: int | None,
    username: str,
    password: str,
    user: dict,
) -> RedirectResponse:
    """Создать постоянного администратора, привязанного к VK ID."""
    vk_id = _parse_vk_id(form)

    full_name = sanitize_html(str(form.get("full_name", "")).strip())
    if not username:
        username = f"dept_admin_{vk_id}"
    if not password:
        password = secrets.token_urlsafe(12)

    existing = await session.scalar(select(WebUser).where(WebUser.username == username))
    if existing:
        request.session["flash_error"] = f"Логин '{username}' уже занят"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    await _create_admin_records(
        session=session,
        vk_id=vk_id,
        full_name=full_name,
        department_id_raw=str(dept_id) if dept_id is not None else None,
        admin_department_id=user_get_department_id(user),
        user_is_super=is_superadmin(user),
        role=role_str,
        username=username,
        password_hash=hash_password(password),
    )
    request.session["flash_success"] = "Администратор успешно добавлен"
    request.session["created_credentials"] = {
        "username": username,
        "password": password,
        "created_at": time.time(),
    }
    return RedirectResponse(url="/admin/admins/", status_code=302)


def _parse_vk_id(form) -> int:
    """Парсит vk_id из формы."""
    try:
        return int(form.get("vk_id", 0))
    except (TypeError, ValueError):
        raise ValueError("VK ID должен быть числом") from None




def user_get_department_id(user: dict) -> int | None:
    """Извлекает department_id из сессии пользователя."""
    return user.get("department_id")


def _check_role_permissions(user: dict, role: str) -> str | None:
    """Проверяет права пользователя на назначение роли."""
    if role == "superadmin" and not is_superadmin(user):
        return "Только суперадмин может назначать эту роль"
    return None


async def _create_admin_records(
    session: AsyncSession,
    vk_id: int,
    full_name: str,
    department_id_raw: str | None,
    admin_department_id: int | None,
    user_is_super: bool,
    role: str,
    username: str,
    password_hash: str,
) -> None:
    """Создаёт записи Admin и WebUser в БД."""
    # Определяем отдел в зависимости от прав вызывающего пользователя
    if not user_is_super:
        selected_department_id = admin_department_id
        if selected_department_id is None:
            raise ValueError("У вашего аккаунта не указан отдел")
    else:
        selected_department_id = int(department_id_raw) if department_id_raw else None

    # Создаём или получаем связанный профиль пользователя VK
    db_user = await session.scalar(select(User).where(User.vk_id == vk_id))
    if not db_user:
        db_user = User(vk_id=vk_id, full_name=full_name or None)
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    elif full_name:
        db_user.full_name = full_name
        await session.commit()

    # Проверяем уникальность администратора
    existing: Admin | None = await session.scalar(select(Admin).where(Admin.user_id == db_user.id))
    if existing:
        raise ValueError("Этот пользователь уже является админом")

    web_role = WebRole.SUPERADMIN if role == "superadmin" else WebRole.DEPARTMENT_ADMIN

    admin = Admin(
        user_id=db_user.id,
        department_id=selected_department_id,
        role=UserRole(role),
    )
    session.add(admin)
    await session.flush()

    session.add(
        WebUser(
            username=username,
            password_hash=password_hash,
            role=web_role,
            admin_id=admin.id,
            department_id=selected_department_id,
        )
    )

    await session.commit()


@router.post("/{admin_id}/delete")
async def delete_admin(request: Request, admin_id: int, user=Depends(require_auth)):
    """Удаление администратора (POST с CSRF-токеном и подтверждением).

    Безопасность:
    - Временный администратор не может удалять учётные записи
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может удалять администраторов
    - Нельзя удалить самого себя или последнего суперадмина
    - При удалении Admin также удаляется связанный WebUser (если есть)
    """
    denied = _deny_temporary_admin(request, user)
    if denied is not None:
        return denied

    await require_crud_rate_limit(request)
    if not is_superadmin(user):
        request.session["flash_error"] = "Только суперадмин может удалять администраторов"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            admin = await session.get(Admin, admin_id)
            if not admin:
                request.session["flash_error"] = "Администратор не найден"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            current_web_user = await session.get(WebUser, user.get("web_user_id"))
            if current_web_user and current_web_user.admin_id == admin.id:
                request.session["flash_error"] = "Нельзя удалить себя"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            if admin.role == UserRole.SUPERADMIN:
                superadmin_count: int | None = await session.scalar(
                    select(func.count(Admin.id)).where(Admin.role == UserRole.SUPERADMIN)
                )
                if superadmin_count is not None and superadmin_count <= 1:
                    request.session["flash_error"] = "Нельзя удалить последнего суперадмина"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

            # Находим и удаляем связанного WebUser по прямой ссылке admin_id
            web_user = await session.scalar(select(WebUser).where(WebUser.admin_id == admin.id))
            if web_user and user.get("web_user_id") != web_user.id:
                await session.delete(web_user)

            # Логируем действие удаления (сохраняем для аудита)
            deletion_log = Log(
                user_id=admin.user_id,
                action="admin_deleted",
                details=f"Удалён администратор id={admin_id}, роль={admin.role.value if admin.role else 'unknown'}, отдел={admin.department_id}",
            )
            session.add(deletion_log)

            # Удаляем самого админа (действия пользователя сохраняются через User)
            await session.delete(admin)
            await session.commit()

            logger.info(
                "Удалён администратор id=%s, department_id=%s",
                admin_id,
                admin.department_id,
            )
    except Exception:
        logger.exception("Не удалось удалить администратора")
        request.session["flash_error"] = "Не удалось удалить администратора. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    request.session["flash_success"] = "Администратор удалён"
    return RedirectResponse(url="/admin/admins/", status_code=302)


@router.post("/web-users/{web_user_id}/delete")
async def delete_web_user(request: Request, web_user_id: int, user=Depends(require_auth)):
    """Удаление веб-пользователя (POST с CSRF-токеном и подтверждением).

    Безопасность:
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может удалять веб-пользователей
    - Нельзя удалить самого себя
    - Нельзя удалить последнего суперадмина
    """
    denied = _deny_temporary_admin(request, user)
    if denied is not None:
        return denied

    await require_crud_rate_limit(request)
    if not is_superadmin(user):
        request.session["flash_error"] = "Только суперадмин может удалять веб-пользователей"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            web_user = await session.get(WebUser, web_user_id)
            if not web_user:
                request.session["flash_error"] = "Веб-пользователь не найден"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Нельзя удалить самого себя
            if user.get("web_user_id") == web_user.id:
                request.session["flash_error"] = "Нельзя удалить себя"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Проверяем, что это не последний суперадмин
            if web_user.role == WebRole.SUPERADMIN:
                superadmin_count: int | None = await session.scalar(
                    select(func.count(WebUser.id)).where(WebUser.role == WebRole.SUPERADMIN)
                )
                if superadmin_count is not None and superadmin_count <= 1:
                    request.session["flash_error"] = "Нельзя удалить последнего суперадмина"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

            await session.delete(web_user)
            await session.commit()

            logger.info(
                "Удалён веб-пользователь id=%s, username=%s",
                web_user_id,
                web_user.username,
            )
    except Exception:
        logger.exception("Не удалось удалить веб-пользователя")
        request.session["flash_error"] = "Не удалось удалить веб-пользователя. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    request.session["flash_success"] = "Веб-пользователь удалён"
    return RedirectResponse(url="/admin/admins/", status_code=302)
