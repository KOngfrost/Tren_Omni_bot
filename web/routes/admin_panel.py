"""
Маршруты управления администраторами.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- require_auth проверяет сессию + роль (суперадмин/админ/наблюдатель)
- IDOR: админ видит только администраторов своего отдела (суперадмин — всех)
- Назначение суперадминов: только действующий суперадмин
"""

from datetime import datetime, timedelta, timezone
import logging
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Admin, Department, Log, User, UserRole, WebRole, WebUser
from web.dependencies import get_admin_scope, is_superadmin, require_auth
from web.routes.auth import require_crud_rate_limit
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.security.passwords import hash_password
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def admins_page(request: Request, user=Depends(require_auth)):
    """Страница управления администраторами с иерархической сортировкой.

    Иерархия отображения:
    1. Суперадминистраторы (role=SUPERADMIN) — без привязки к отделу, полный доступ
    2. Администраторы отделов (role=ADMIN) — привязаны к конкретному отделу

    IDOR-защита:
    - Суперадмин видит всех админов.
    - Обычный админ — только тех, кто привязан к его отделу.
    """
    admins: list[Admin] = []
    departments: list[Department] = []
    db_error: bool = False

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
            "created_credentials": request.session.pop("created_credentials", None),
            "session_id": request.state.session_id,
        },
    )


@router.post("/qa")
async def add_qa_admin(request: Request, user=Depends(require_auth)):
    """Создание тестового QA-администратора без VK ID (обратная совместимость)."""
    return await add_admin(request, user)


@router.post("/")
async def add_admin(request: Request, user=Depends(require_auth)):
    """Добавление нового администратора (с VK ID или временного/QA с настраиваемым сроком)."""
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
    expires_at = None
    if admin_type == "temp":
        preset = str(form.get("duration_preset", "24")).strip()
        try:
            if preset == "custom":
                custom_hours = int(form.get("custom_hours", 0))
                if custom_hours < 1:
                    request.session["flash_error"] = "Количество часов должно быть положительным числом"
                    return RedirectResponse(url="/admin/admins/", status_code=302)
                hours = custom_hours
            else:
                hours = int(preset)
            if hours > 0:
                expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        except (ValueError, TypeError):
            request.session["flash_error"] = "Некорректное значение срока действия"
            return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            if admin_type == "temp":
                if not username:
                    username = f"qa_{secrets.token_hex(4)}"
                if not password:
                    password = secrets.token_urlsafe(12)

                existing = await session.scalar(select(WebUser).where(WebUser.username == username))
                if existing:
                    request.session["flash_error"] = f"Логин '{username}' уже занят"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

                web_user = WebUser(
                    username=username,
                    password_hash=hash_password(password),
                    role=web_role,
                    department_id=dept_id,
                    admin_id=None,
                    is_active=True,
                    expires_at=expires_at,
                )
                session.add(web_user)
                await session.commit()

                if expires_at:
                    duration_str = f"действует до {expires_at.strftime('%d.%m.%Y %H:%M UTC')}"
                else:
                    duration_str = "бессрочный доступ"
                request.session["flash_success"] = f"Временный/QA администратор '{username}' успешно создан ({duration_str})."
                request.session["created_credentials"] = {"username": username, "password": password}
                return RedirectResponse(url="/admin/admins/", status_code=302)
            else:
                # Постоянный администратор с VK ID
                try:
                    vk_id = _parse_vk_id(form)
                except ValueError:
                    request.session["flash_error"] = "VK ID должен быть числом"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

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
                request.session["created_credentials"] = {"username": username, "password": password}
                return RedirectResponse(url="/admin/admins/", status_code=302)
    except ValueError as e:
        request.session["flash_error"] = str(e)
        return RedirectResponse(url="/admin/admins/", status_code=302)
    except Exception:
        logger.exception("Не удалось добавить администратора")
        request.session["flash_error"] = "Не удалось сохранить администратора. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)


def _parse_vk_id(form) -> int:
    """Парсит vk_id из формы."""
    try:
        return int(form.get("vk_id", 0))
    except (TypeError, ValueError):
        raise ValueError("VK ID должен быть числом") from None


def _validate_add_admin_form(form) -> str | None:
    """Валидация формы. Возвращает сообщение об ошибке или None."""
    try:
        _parse_vk_id(form)
    except ValueError:
        return "VK ID должен быть числом"

    role: str = form.get("role", "admin")
    if role not in ("admin", "superadmin"):
        return "Неизвестная роль администратора"

    password: str = str(form.get("password", "")).strip()
    if password and len(password) < 8:
        return "Пароль должен быть не короче 8 символов"

    return None


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
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может удалять администраторов
    - Нельзя удалить самого себя или последнего суперадмина
    - При удалении Admin также удаляется связанный WebUser (если есть)
    """
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
