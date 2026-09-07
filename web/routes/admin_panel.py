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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
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
    """Страница управления администраторами.

    IDOR-защита:
    - Суперадмин видит всех админов и всех веб-пользователей.
    - Обычный админ — только тех, кто привязан к его отделу.
    """
    admins: list[Admin] = []
    web_users: list[WebUser] = []
    departments: list[Department] = []
    db_error: bool = False

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            dept_stmt = select(Department).order_by(Department.name)
            if not is_super:
                dept_stmt = dept_stmt.where(Department.id == dept_id)
            departments = list((await session.execute(dept_stmt)).scalars().all())

            admins_stmt = (
                select(Admin)
                .options(selectinload(Admin.user), selectinload(Admin.department))
                .order_by(Admin.id)
            )
            if not is_super:
                admins_stmt = admins_stmt.where(Admin.department_id == dept_id)
            admins = list((await session.execute(admins_stmt)).scalars().all())

            web_users_stmt = (
                select(WebUser)
                .options(selectinload(WebUser.department))
                .order_by(WebUser.id)
            )
            if not is_super:
                web_users_stmt = web_users_stmt.where(WebUser.department_id == dept_id)
            web_users = list((await session.execute(web_users_stmt)).scalars().all())
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить администраторов: %s", e)

    return templates.TemplateResponse(
        "admins.html",
        {
            "request": request,
            "user": user,
            "admins": admins,
            "web_users": web_users,
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



@router.post("/")
async def add_admin(request: Request, user=Depends(require_auth)):
    """Добавление нового администратора."""
    require_crud_rate_limit(request)
    form = await request.form()

    # === Шаг 1: Валидация входных данных ===
    errors = _validate_add_admin_form(form)
    if errors:
        request.session["flash_error"] = errors
        return RedirectResponse(url="/admin/admins/", status_code=302)

    vk_id: int = _parse_vk_id(form)
    full_name: str = sanitize_html(form.get("full_name", ""))
    department_id_raw: str | None = form.get("department_id")
    role: str = form.get("role", "admin")
    username: str = sanitize_html(str(form.get("username", "")).strip())
    password: str = str(form.get("password", ""))

    # === Шаг 2: Проверка прав и определение department_id ===
    try:
        async with async_session_maker() as session:
            allowed_roles = _check_role_permissions(user, role)
            if allowed_roles is not None:
                request.session["flash_error"] = allowed_roles
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Генерация учётных данных
            if not username:
                username = f"dept_admin_{vk_id}"
            if not password:
                password = secrets.token_urlsafe(12)
            if len(password) < 8:
                request.session["flash_error"] = "Пароль должен быть не короче 8 символов"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Проверка уникальности username
            if await session.scalar(select(WebUser).where(WebUser.username == username)):
                request.session["flash_error"] = "Такой логин уже занят"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # === Шаг 3: БД-логика ===
            await _create_admin_records(
                session=session,
                vk_id=vk_id,
                full_name=full_name,
                department_id_raw=department_id_raw,
                is_super=user_get_department_id(user),
                user_is_super=is_superadmin(user),
                role=role,
                username=username,
                password_hash=hash_password(password),
            )
    except Exception:
        logger.exception("Не удалось добавить администратора")
        request.session["flash_error"] = "Не удалось сохранить изменения. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    # === Шаг 4: Ответ ===
    request.session["flash_success"] = "Администратор успешно добавлен"
    request.session["created_credentials"] = {"username": username, "password": password}
    return RedirectResponse(url="/admin/admins/", status_code=302)


def _parse_vk_id(form) -> int:
    """Парсит vk_id из формы."""
    try:
        return int(form.get("vk_id", 0))
    except (TypeError, ValueError):
        raise ValueError("VK ID должен быть числом")


def _validate_add_admin_form(form) -> str | None:
    """Валидация формы. Возвращает сообщение об ошибке или None."""
    try:
        _parse_vk_id(form)
    except ValueError:
        return "VK ID должен быть числом"

    role: str = form.get("role", "admin")
    if role not in ("admin", "superadmin", "viewer"):
        return "Неизвестная роль администратора"

    password: str = str(form.get("password", ""))
    if len(password) < 8:
        return "Пароль должен быть не короче 8 символов"

    return None


def user_get_department_id(user: dict) -> int | None:
    """Извлекает department_id из сессии пользователя."""
    return user.get("department_id")


def _check_role_permissions(user: dict, role: str) -> str | None:
    """Проверяет права пользователя на назначение роли."""
    if role in ("superadmin", "viewer") and not is_superadmin(user):
        return "Только суперадмин может назначать эту роль"
    return None


async def _create_admin_records(
    session: "async_session_maker",
    vk_id: int,
    full_name: str,
    department_id_raw: str | None,
    is_super: int | None,
    user_is_super: bool,
    role: str,
    username: str,
    password_hash: str,
) -> None:
    """Создаёт записи Admin и WebUser в БД."""
    # Определяем department_id
    if not user_is_super:
        selected_department_id = is_super
        if selected_department_id is None:
            raise ValueError("У вашего аккаунта не указан отдел")
    else:
        selected_department_id: int | None = int(department_id_raw) if department_id_raw else None

    # Получаем или создаём пользователя VK (VIEWER — только веб-панель)
    db_user: User | None = None
    if role != "viewer":
        db_user = await session.scalar(select(User).where(User.vk_id == vk_id))
        if not db_user:
            db_user = User(vk_id=vk_id, full_name=full_name or None)
            session.add(db_user)
            await session.commit()
            await session.refresh(db_user)
        elif full_name:
            db_user.full_name = full_name
            await session.commit()

        # Проверяем, нет ли уже такого админа
        existing: Admin | None = await session.scalar(
            select(Admin).where(Admin.user_id == db_user.id)
        )
        if existing:
            raise ValueError("Этот пользователь уже является админом")

    web_role = {
        "superadmin": WebRole.SUPERADMIN,
        "viewer": WebRole.VIEWER,
    }.get(role, WebRole.DEPARTMENT_ADMIN)

    if role == "admin":
        admin = Admin(
            user_id=db_user.id,
            department_id=selected_department_id,
            role=UserRole(role),
        )
        session.add(admin)
        await session.flush()  # Получаем admin.id

        # Связываем WebUser с Admin через admin_id
        session.add(WebUser(
            username=username,
            password_hash=password_hash,
            role=web_role,
            admin_id=admin.id,
            department_id=selected_department_id,
        ))
    else:
        # superadmin/viewer — без записи Admin
        session.add(WebUser(
            username=username,
            password_hash=password_hash,
            role=web_role,
            admin_id=None,
            department_id=selected_department_id,
        ))

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
    require_crud_rate_limit(request)
    if not is_superadmin(user):
        request.session["flash_error"] = "Только суперадмин может удалять администраторов"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            admin = await session.get(Admin, admin_id)
            if not admin:
                request.session["flash_error"] = "Администратор не найден"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Нельзя удалить самого себя (для legacy-админов с user_id в сессии)
            if user.get("user_id") == admin.id:
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
            web_user = await session.scalar(
                select(WebUser).where(WebUser.admin_id == admin.id)
            )
            if web_user and user.get("web_user_id") != web_user.id:
                await session.delete(web_user)

            # Логируем действие удаления (сохраняем для аудита)
            deletion_log = Log(
                user_id=admin.user_id,
                action="admin_deleted",
                details=f"Удалён администратор id={admin_id}, роль={admin.role.value}, отдел={admin.department_id}"
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
    require_crud_rate_limit(request)
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
