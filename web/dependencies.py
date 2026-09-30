"""Общие зависимости авторизации и разграничения прав для веб-панели.

Модель доступа:
- SUPERADMIN: полный доступ ко всем отделам и настройкам.
- DEPARTMENT_ADMIN: просмотр и управление заявками и контентом своего отдела.

Временный администратор (срок expires_at или созданный без привязки к VK Admin):
- может просматривать дашборд/заявки, отвечать студентам, менять статусы,
  читать базу знаний и FAQ;
- НЕ может создавать и удалять учётные записи (проверка в admin_panel.py).
  Флаг is_temporary вычисляется ЗДЕСЬ, на основе БД, а не из строки роли в
  сессии: роль у временного админа может быть любой, доверять ей нельзя.

Безопасность:
- department_id для DEPARTMENT_ADMIN проверяется через базу данных (web_users),
  что исключает горизонтальную эскалацию прав.
"""

import logging
from typing import NoReturn

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.admin_presence import touch_presence
from core.bot_core import get_admin_scope_for_vk_id  # noqa: F401
from core.database import async_session_maker
from core.models import Department, WebRole, WebUser
from web.security import otp_store

logger = logging.getLogger(__name__)


def get_current_user(request: Request) -> dict | None:
    """Данные пользователя из сессии или None."""
    return request.session.get("user")


def is_temporary_user(web_user: WebUser | None) -> bool:
    """Временный ли это администратор.

    Временными считаются:
    - учётные записи с ограниченным сроком (expires_at) — временные и QA;
    - учётные записи без привязки к VK-админу (admin_id IS NULL) — созданные
      из веб-панели без VK ID.

    Функция принимает ОБЪЕКТ БД, а не словарь сессии: источник истины — база.
    """
    if web_user is None:
        return False
    return web_user.expires_at is not None or web_user.admin_id is None


def _is_api_request(request: Request) -> bool:
    """Запрос от API (ожидает JSON, а не редирект на страницу входа)."""
    return request.url.path.startswith("/api/") or "application/json" in request.headers.get(
        "accept", ""
    )


def _service_unavailable() -> HTTPException:
    """Fail-Closed (#3): 503 вместо доступа «по доверию» к устаревшей сессии."""
    return HTTPException(
        status_code=503,
        detail="Сервер авторизации временно недоступен. Попробуйте позже.",
        headers={"Cache-Control": "no-store"},
    )


def _deny_session(request: Request, is_api: bool) -> NoReturn:
    """Отозвать сессию и отклонить запрос (401 для API, редирект для страниц)."""
    request.session.clear()
    if is_api:
        raise HTTPException(status_code=401, detail="Сессия истекла")
    raise HTTPException(
        status_code=302, detail="Session expired", headers={"Location": "/auth/login"}
    )


async def _verify_web_user(
    session: AsyncSession, user: dict, is_api: bool, request: Request
) -> dict:
    """Проверить постоянного пользователя по БД (источник истины — база).

    Fail-Closed: без подтверждения активности учётки доступ запрещён, а сессия
    НЕ очищается — сетевой сбой БД не должен разлогинивать администратора.
    """
    try:
        web_user = await session.get(WebUser, user["web_user_id"])
    except Exception as exc:
        logger.error(
            "require_auth: проверка прав недоступна, доступ запрещён (fail-closed): %s",
            exc,
        )
        raise _service_unavailable() from exc

    if web_user is None or not web_user.is_active:
        _deny_session(request, is_api)

    # Отметка присутствия: ключ admin:heartbeat:{id} с TTL 15 минут.
    # Ошибка записи не должна ломать запрос — индикатор «Онлайн» важен,
    # но не настолько, чтобы отказывать администратору в доступе.
    try:
        await touch_presence(web_user.id)
    except Exception as exc:  # pragma: no cover - защитный контур
        logger.debug("Не удалось обновить индикатор активности: %s", exc)

    canonical = {
        "username": web_user.username,
        "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
        "web_user_id": web_user.id,
        "department_id": web_user.department_id,
        "admin_id": web_user.admin_id,
        # Флаг вычисляется из БД, а не из роли: временный админ может иметь
        # роль superadmin, но прав на управление учётными записями не получает.
        "is_temporary": is_temporary_user(web_user),
        "expires_at": web_user.expires_at,
        # Личный переключатель 2FA и признак применимости: без привязки
        # к VK код подтверждения доставлять некуда.
        "two_factor_enabled": bool(web_user.two_factor_enabled),
        "two_factor_available": web_user.two_factor_available,
    }
    request.session["user"] = canonical
    return canonical


def _verify_bootstrap_session(user: dict, is_api: bool, request: Request) -> dict:
    """Проверить, что резервный вход из .env всё ещё разрешён (Ошибка #2).

    Bootstrap-сессия не должна доверять устаревшим данным: резервный вход
    могли отключить (BOOTSTRAP_ALLOWED=False), креды удалить/изменить в .env
    либо снять привязку 2FA-канала уже после выдачи сессии.
    """
    from web.routes.auth import bootstrap_session_still_valid

    if bootstrap_session_still_valid(user):
        return user

    logger.warning(
        "SECURITY AUDIT: BOOTSTRAP SESSION INVALIDATED | user=%s | "
        "reason=bootstrap login disabled or reconfigured",
        user.get("username"),
    )
    _deny_session(request, is_api)


async def require_auth(request: Request) -> dict:
    """Проверить сессию и актуальные права пользователя в базе данных."""
    user = request.session.get("user")
    is_api = _is_api_request(request)

    if not user:
        if is_api:
            raise HTTPException(status_code=401, detail="Не авторизован")
        from web.routes.auth import login_url_with_next

        raise HTTPException(
            status_code=302,
            detail="Redirect",
            headers={"Location": login_url_with_next(request)},
        )

    # Fail-Closed: пока в этом браузере есть незавершённая 2FA-попытка,
    # привилегированный доступ по прежней сессии приостанавливается.
    # Иначе вход с неподтверждённым вторым фактором соседствовал бы
    # с активной рабочей сессией администратора.
    if otp_store.SESSION_KEY in request.session and await otp_store.peek(request):
        if is_api:
            raise HTTPException(status_code=401, detail="Требуется подтверждение второго фактора")
        raise HTTPException(status_code=302, detail="Redirect", headers={"Location": "/auth/2fa"})

    # Ошибка #3 (Fail-Closed): любые сетевые/таймаут-ошибки БД — включая
    # установку соединения sessionmaker'ом — отклоняют запрос с 503,
    # а не позволяют доступ «по доверию» к устаревшим данным cookie.
    try:
        async with async_session_maker() as session:
            web_user_id = user.get("web_user_id")
            if web_user_id is not None:
                return await _verify_web_user(session, user, is_api, request)
            if role_of(user) == WebRole.SUPERADMIN and user.get("bootstrap"):
                return _verify_bootstrap_session(user, is_api, request)
    except HTTPException:
        # Оборонительные отказы (302/401/503) проходят как есть
        raise
    except Exception as exc:
        logger.error(
            "require_auth: недоступна БД (в т.ч. установка соединения) — доступ запрещён "
            "(fail-closed): %s",
            exc,
        )
        raise _service_unavailable() from exc

    # Неизвестный формат сессии (ни web_user_id, ни корректного bootstrap)
    _deny_session(request, is_api)


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


def is_temporary(user: dict) -> bool:
    """Временный ли администратор (по каноническому объекту сессии).

    Основной источник — флаг is_temporary, вычисленный из БД в _verify_web_user.
    Дополнительно перепроверяем expires_at напрямую, чтобы корректно обработать
    сессии, созданные до обновления кода (в них флага ещё нет).

    Сессия bootstrap-суперадмина из .env считается постоянной: у неё нет ни
    web_user_id, ни срока действия, и она является аварийным входом.
    """
    if user.get("is_temporary"):
        return True
    return user.get("expires_at") is not None


def can_manage_accounts(user: dict) -> bool:
    """Может ли администратор создавать/удалять учётные записи.

    Временный администратор сохраняет работу с заявками, но управлять
    учётными записями не может — даже если ему назначена роль SUPERADMIN.
    """
    return not is_temporary(user)


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
