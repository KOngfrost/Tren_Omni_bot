"""Общие зависимости авторизации и разграничения прав для веб-панели.

Модель доступа:
- SUPERADMIN: видит все отделы, может всё (включая передачу заявок).
- DEPARTMENT_ADMIN: видит и изменяет заявки/контент только своего отдела.
- VIEWER: видит всё, но не может изменять данные.

Безопасность:
- department_id для DEPARTMENT_ADMIN проверяется через БД (web_users),
  а не из сессии, чтобы предотвратить горизонтальную эскалацию прав.
"""

import logging

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from core.models import Department, WebRole, WebUser

logger = logging.getLogger(__name__)


def get_current_user(request: Request) -> dict | None:
    """Данные пользователя из сессии или None."""
    return request.session.get("user")


async def require_auth(request: Request) -> dict:
    """Проверить сессию и перечитать актуальные права из БД."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=302, detail="Redirect", headers={"Location": "/auth/login"}
        )
    async with async_session_maker() as session:
        web_user_id = user.get("web_user_id")
        if web_user_id is not None:
            web_user = await session.get(WebUser, web_user_id)
            if web_user is None or not web_user.is_active:
                request.session.clear()
                raise HTTPException(
                    status_code=302, detail="Session expired",
                    headers={"Location": "/auth/login"},
                )
            canonical = {
                "username": web_user.username,
                "role": web_user.role.value,
                "web_user_id": web_user.id,
                "department_id": web_user.department_id,
            }
            request.session["user"] = canonical
            return canonical

        # Bootstrap-сессии (суперадмин из .env, пока web_users не заведены)
        if role_of(user) == WebRole.SUPERADMIN and user.get("bootstrap"):
            return user

    request.session.clear()
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
    """Суперадмин видит и может всё."""
    return role_of(user) == WebRole.SUPERADMIN


def can_write(user: dict) -> bool:
    """Может ли пользователь изменять данные (не VIEWER)."""
    return role_of(user) in (WebRole.SUPERADMIN, WebRole.DEPARTMENT_ADMIN)


async def require_writer(request: Request) -> dict:
    """Проверка авторизации + права на изменение (403 для VIEWER)."""
    user = await require_auth(request)
    if not can_write(user):
        raise HTTPException(
            status_code=403,
            detail="Просмотр без права изменения: обратитесь к суперадминистратору",
        )
    return user


async def require_superadmin(request: Request) -> dict:
    """Только для суперадминов."""
    user = await require_auth(request)
    if not is_superadmin(user):
        raise HTTPException(
            status_code=403, detail="Действие доступно только суперадминистратору"
        )
    return user


async def get_admin_scope(session: AsyncSession, user: dict) -> tuple[bool, int | None]:
    """Определить область видимости пользователя.

    Возвращает (is_super, dept_id):
    - суперадмин/VIEWER: (True, None) — видят все отделы;
    - админ отдела: (False, department_id) — видят только свой отдел.

    БЕЗОПАСНОСТЬ: department_id для DEPARTMENT_ADMIN проверяется через БД
    (таблица web_users), а не из сессии. Это предотвращает горизонтальную
    эскалацию прав при компрометации сессионного cookie.
    """
    role = role_of(user)
    if role == WebRole.SUPERADMIN or role == WebRole.VIEWER:
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
                logger.warning(
                    "Не удалось проверить department_id через БД, "
                    "возвращаю None (ограниченный доступ)"
                )
                return False, None
        logger.warning(
            "DEPARTMENT_ADMIN без подтверждённой идентичности (user_id=%s)",
            user.get("user_id", "unknown"),
        )
        return False, None
    return False, None


async def get_departments_for_user(session: AsyncSession, user: dict) -> list:
    """Загрузить список отделов, видимых пользователю.

    Возвращает список Department:
    - суперадмин: все отделы
    - админ отдела: только свой отдел
    - VIEWER: все отделы
    """
    role = role_of(user)
    if role == WebRole.SUPERADMIN or role == WebRole.VIEWER:
        return (await session.execute(
            select(Department).order_by(Department.name)
        )).scalars().all()

    if role == WebRole.DEPARTMENT_ADMIN:
        web_user_id = user.get("web_user_id")
        if web_user_id:
            web_user = await session.get(WebUser, web_user_id)
            if web_user and web_user.department_id:
                dept = await session.get(Department, web_user.department_id)
                return [dept] if dept else []

    return []


async def get_admin_scope_for_vk_id(session: AsyncSession, vk_id: int) -> tuple[bool, int | None]:
    """Определить область видимости пользователя по VK ID (для VK-бота).

    Возвращает (is_super, dept_id):
    - суперадмин: (True, None) — видят все отделы;
    - админ отдела: (False, department_id) — видят только свой отдел;
    - обычный пользователь: (False, None) — нет доступа.

    БЕЗОПАСНОСТЬ: department_id проверяется через БД (таблица admins).
    """
    admin = await session.scalar(
        select(Admin).join(User, Admin.user_id == User.id).where(User.vk_id == vk_id)
    )
    if admin is None:
        return False, None
    is_super = admin.role == UserRole.SUPERADMIN
    return is_super, admin.department_id if not is_super else None
