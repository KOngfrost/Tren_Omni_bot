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
            "success": request.session.pop("success", None),
            "error": request.session.pop("error", None),
            "csrf_token": get_csrf_token(request),
            "created_credentials": request.session.pop("created_credentials", None),
            "session_id": request.state.session_id,
        },
    )



@router.post("/")
async def add_admin(request: Request, user=Depends(require_auth)):  # noqa: C901 — длинная цепочка валидации формы
    """Добавление нового администратора.

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware (токен из сессии)
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может назначать роль superadmin
    - Обычный админ может назначать только роль admin
    - Данные из форм санитизируются
    """
    require_crud_rate_limit(request)
    form = await request.form()
    try:
        vk_id: int = int(form.get("vk_id", 0))
    except (TypeError, ValueError):
        request.session["error"] = "VK ID должен быть числом"
        return RedirectResponse(url="/admin/admins/", status_code=302)
    full_name: str = sanitize_html(form.get("full_name", ""))
    department_id: str | None = form.get("department_id")
    role: str = form.get("role", "admin")
    username: str = sanitize_html(str(form.get("username", "")).strip())
    password: str = str(form.get("password", ""))

    # Проверка прав: только суперадмин может назначать роль superadmin/viewer
    try:
        async with async_session_maker() as session:
            if role not in ("admin", "superadmin", "viewer"):
                request.session["error"] = "Неизвестная роль администратора"
                return RedirectResponse(url="/admin/admins/", status_code=302)
            if role in ("superadmin", "viewer") and not is_superadmin(user):
                request.session["error"] = "Только суперадмин может назначать эту роль"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            selected_department_id: int | None = int(department_id) if department_id else None
            if not is_superadmin(user):
                # Обычный админ назначает админов только в свой отдел
                selected_department_id = user.get("department_id")
                if selected_department_id is None:
                    request.session["error"] = "У вашего аккаунта не указан отдел"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

            if not username:
                username = f"dept_admin_{vk_id}"
            if not password:
                password = secrets.token_urlsafe(12)
            if len(password) < 8:
                request.session["error"] = "Пароль должен быть не короче 8 символов"
                return RedirectResponse(url="/admin/admins/", status_code=302)
            if await session.scalar(select(WebUser).where(WebUser.username == username)):
                request.session["error"] = "Такой логин уже занят"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Получаем или создаём пользователя VK (наблюдателю он не нужен:
            # VIEWER существует только в веб-панели)
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
                    request.session["error"] = "Этот пользователь уже является админом"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

            web_role = {
                "superadmin": WebRole.SUPERADMIN,
                "viewer": WebRole.VIEWER,
            }.get(role, WebRole.DEPARTMENT_ADMIN)

            if role == "admin":
                # Создаём запись админа (VK + веб-панель)
                session.add(Admin(
                    user_id=db_user.id,
                    department_id=selected_department_id,
                    role=UserRole(role),
                ))
            session.add(WebUser(
                username=username,
                password_hash=hash_password(password),
                role=web_role,
                department_id=selected_department_id,
            ))
            await session.commit()
    except Exception:
        logger.exception("Не удалось добавить администратора")
        request.session["error"] = "Не удалось сохранить изменения. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    request.session["success"] = "Администратор успешно добавлен"
    request.session["created_credentials"] = {"username": username, "password": password}
    return RedirectResponse(url="/admin/admins/", status_code=302)


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
        request.session["error"] = "Только суперадмин может удалять администраторов"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            admin = await session.get(Admin, admin_id)
            if not admin:
                request.session["error"] = "Администратор не найден"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Нельзя удалить самого себя (для legacy-админов с user_id в сессии)
            if user.get("user_id") == admin.id:
                request.session["error"] = "Нельзя удалить себя"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            if admin.role == UserRole.SUPERADMIN:
                superadmin_count: int | None = await session.scalar(
                    select(func.count(Admin.id)).where(Admin.role == UserRole.SUPERADMIN)
                )
                if superadmin_count is not None and superadmin_count <= 1:
                    request.session["error"] = "Нельзя удалить последнего суперадмина"
                    return RedirectResponse(url="/admin/admins/", status_code=302)

            # Находим и удаляем связанного WebUser (если есть)
            # Ищем по department_id и роли, так как прямой связи между Admin и WebUser нет
            web_user_stmt = select(WebUser).where(
                WebUser.department_id == admin.department_id,
                WebUser.role.in_([WebRole.DEPARTMENT_ADMIN, WebRole.SUPERADMIN])
            )
            web_users = (await session.execute(web_user_stmt)).scalars().all()

            # Удаляем связанных веб-пользователей (обычно это один пользователь)
            # Это очищает логин и пароль удалённого админа
            deleted_web_users_count = 0
            for web_user in web_users:
                # Проверяем, что это не текущий пользователь (для web_user_id в сессии)
                if user.get("web_user_id") == web_user.id:
                    continue
                await session.delete(web_user)
                deleted_web_users_count += 1

            # Логируем действие удаления (сохраняем для аудита)
            # Действие привязываем к пользователю, чтобы сохранить историю
            deletion_log = Log(
                user_id=admin.user_id,
                action="admin_deleted",
                details=f"Удалён администратор id={admin_id}, роль={admin.role.value}, отдел={admin.department_id}"
            )
            session.add(deletion_log)

            # Удаляем самого админа (очищаем admin id)
            # Действия пользователя (логи) сохраняются, так как они привязаны к User
            await session.delete(admin)
            await session.commit()

            logger.info(
                "Удалён администратор id=%s, department_id=%s, связанных web_users: %d, лог сохранён",
                admin_id,
                admin.department_id,
                deleted_web_users_count,
            )
    except Exception:
        logger.exception("Не удалось удалить администратора")
        request.session["error"] = "Не удалось удалить администратора. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    request.session["success"] = "Администратор удалён"
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
        request.session["error"] = "Только суперадмин может удалять веб-пользователей"
        return RedirectResponse(url="/admin/admins/", status_code=302)

    try:
        async with async_session_maker() as session:
            web_user = await session.get(WebUser, web_user_id)
            if not web_user:
                request.session["error"] = "Веб-пользователь не найден"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Нельзя удалить самого себя
            if user.get("web_user_id") == web_user.id:
                request.session["error"] = "Нельзя удалить себя"
                return RedirectResponse(url="/admin/admins/", status_code=302)

            # Проверяем, что это не последний суперадмин
            if web_user.role == WebRole.SUPERADMIN:
                superadmin_count: int | None = await session.scalar(
                    select(func.count(WebUser.id)).where(WebUser.role == WebRole.SUPERADMIN)
                )
                if superadmin_count is not None and superadmin_count <= 1:
                    request.session["error"] = "Нельзя удалить последнего суперадмина"
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
        request.session["error"] = "Не удалось удалить веб-пользователя. Попробуйте позже."
        return RedirectResponse(url="/admin/admins/", status_code=302)

    request.session["success"] = "Веб-пользователь удалён"
    return RedirectResponse(url="/admin/admins/", status_code=302)
