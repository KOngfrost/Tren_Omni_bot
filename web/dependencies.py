"""Общие зависимости авторизации и разграничения прав для веб-панели.

Модель доступа:
- SUPERADMIN: полный доступ ко всем отделам и настройкам.
- DEPARTMENT_ADMIN: просмотр и управление заявками и контентом своего отдела.

Безопасность:
- department_id для DEPARTMENT_ADMIN проверяется через базу данных (web_users),
  что исключает горизонтальную эскалацию прав.
"""

import logging

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bot_core import get_admin_scope_for_vk_id  # noqa: F401
from core.database import async_session_maker
from core.models import Department, WebRole, WebUser

logger = logging.getLogger(__name__)


def get_current_user(request: Request) -> dict | None:
    """Данные пользователя из сессии или None."""
    return request.session.get("user")


async def require_auth(request: Request) -> dict:
    """Проверить сессию и актуальные права пользователя в базе данных."""
    user = request.session.get("user")
    is_api = request.url.path.startswith("/api/") or "application/json" in request.headers.get(
        "accept", ""
    )
    if not user:
        if is_api:
            raise HTTPException(status_code=401, detail="Не авторизован")
        raise HTTPException(
            status_code=302, detail="Redirect", headers={"Location": "/auth/login"}
        )
    async with async_session_maker() as session:
        web_user_id = user.get("web_user_id")
        if web_user_id is not None:
            try:
                web_user = await session.get(WebUser, web_user_id)
            except Exception:
                logger.exception(
                    "require_auth: проверка прав через базу данных временно недоступна"
                )
                return user
            if web_user is None or not web_user.is_active:
                request.session.clear()
                if is_api:
                    raise HTTPException(status_code=401, detail="Сессия истекла")
                raise HTTPException(
                    status_code=302,
                    detail="Session expired",
                    headers={"Location": "/auth/login"},
                )
            canonical = {
                "username": web_user.username,
                "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
                "web_user_id": web_user.id,
                "department_id": web_user.department_id,
            }
            request.session["user"] = canonical
            return canonical

        # Резервный вход администратора конфигурации
        if role_of(user) == WebRole.SUPERADMIN and user.get("bootstrap"):
            return user

    request.session.clear()
    if is_api:
        raise HTTPException(status_code=401, detail="Сессия истекла")
    raise HTTPException(
        status_code=302, detail="Session expired", headers={"Location": "/auth/login"}
    )


def _normalize_role(role: str | None) -> WebRole | None:
    if not role:
        return None
    try:
        return WebRole(role.upper())
    except ValueError:
        return None


def role_of(user: dict) -> WebRole | None:
    """Роль пользователя сессии."""
    return _normalize_role(user.get("role"))


def is_superadmin(user: dict) -> bool:
    """Проверка прав суперадминистратора."""
    return role_of(user) == WebRole.SUPERADMIN


def can_write(user: dict) -> bool:
    """Проверка наличия прав на изменение данных."""
    return role_of(user) in (WebRole.SUPERADMIN, WebRole.DEPARTMENT_ADMIN)


async def require_writer(request: Request) -> dict:
    """Проверка прав на модификацию данных."""
    user = await require_auth(request)
    if not can_write(user):
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав для выполнения операции",
        )
    return user


async def require_superadmin(request: Request) -> dict:
    """Проверка доступа только для суперадминистратора."""
    user = await require_auth(request)
    if not is_superadmin(user):
        raise HTTPException(status_code=403, detail="Действие доступно только суперадминистратору")
    return user


async def get_admin_scope(session: AsyncSession, user: dict) -> tuple[bool, int | None]:
    """Определить область видимости пользователя: (is_super, dept_id)."""
    role = role_of(user)
    if role == WebRole.SUPERADMIN:
        return True, None
    if role == WebRole.DEPARTMENT_ADMIN:
        web_user_id = user.get("web_user_id")
        if web_user_id:
            try:
                web_user = await session.get(WebUser, web_user_id)
                if web_user and web_user.is_active:
                    return False, web_user.department_id
                return False, None
            except Exception:
                logger.warning("Не удалось проверить department_id через базу данных")
                return False, None
        return False, None
    return False, None


async def get_departments_for_user(session: AsyncSession, user: dict) -> list:
    """Загрузить список отделов, доступных пользователю."""
    role = role_of(user)
    if role == WebRole.SUPERADMIN:
        return list(
            (await session.execute(select(Department).order_by(Department.name))).scalars().all()
        )

    if role == WebRole.DEPARTMENT_ADMIN:
        web_user_id = user.get("web_user_id")
        if web_user_id:
            web_user = await session.get(WebUser, web_user_id)
            if web_user and web_user.department_id:
                dept = await session.get(Department, web_user.department_id)
                return [dept] if dept else []

    return []
