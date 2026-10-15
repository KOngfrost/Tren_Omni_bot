"""DB/Redis-backed rate limiting для входа в веб-панель."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import core.database as core_db
from core.models import LoginAttempt

logger = logging.getLogger(__name__)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

# Минутный лимит частоты запросов к форме входа (не более 5 в минуту с одного IP)
LOGIN_VELOCITY_MAX_ATTEMPTS = 5
LOGIN_VELOCITY_WINDOW_SECONDS = 60


async def record_login_request_velocity(ip: str) -> int:
    """Зафиксировать обращение к форме входа в минутом окне."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            key = f"login_vel:{ip}"
            val = await redis.incr(key)
            if val == 1:
                await redis.expire(key, LOGIN_VELOCITY_WINDOW_SECONDS)
            return int(val)
    except Exception:
        pass
    return 1


async def _db_recent_failed_count(session: AsyncSession, ip: str) -> int:
    """Подсчитать неудачные попытки в БД с Redis fallback при ошибке."""
    cutoff = datetime.now(UTC) - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    try:
        count: int | None = await session.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.ip == ip,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= cutoff,
            )
        )
        return int(count or 0)
    except Exception:
        logger.warning(
            "Rate-limit: не удалось прочитать попытки входа из БД, проверяем Redis fallback"
        )
        try:
            from core.redis_client import get_redis_client

            redis = await get_redis_client()
            if redis:
                value = await redis.get(f"failed_login_count:{ip}")
                if value:
                    return int(value)
        except Exception:
            pass
        return 0


async def is_rate_limited(session: AsyncSession, ip: str) -> bool:
    """Проверить, превышен ли лимит неудачных попыток для IP (15 минут) или минутный флуд."""
    # 1. Проверяем минутный всплеск (скорость обращений)
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            vel = await redis.get(f"login_vel:{ip}")
            if vel and int(vel) > LOGIN_VELOCITY_MAX_ATTEMPTS:
                return True
    except Exception:
        pass

    # 2. Проверяем блокировку по неудачным попыткам
    return await _db_recent_failed_count(session, ip) >= LOGIN_MAX_ATTEMPTS


async def get_failed_attempts_count(session: AsyncSession, ip: str) -> int:
    """Получить количество недавних неудачных попыток для IP."""
    return await _db_recent_failed_count(session, ip)


async def record_failed_attempt(ip: str) -> int:
    """Записать неудачную попытку в Redis и БД. Возвращает текущее число неудач."""
    current_count = 1
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            key = f"failed_login_count:{ip}"
            current_count = int(await redis.incr(key))
            await redis.expire(key, LOGIN_WINDOW_SECONDS)
    except Exception:
        pass

    try:
        async with core_db.async_session_maker() as session:
            session.add(LoginAttempt(ip=ip, success=False))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось записать попытку входа в БД")

    return current_count


async def clear_attempts(ip: str) -> None:
    """Сбросить лимит после успешного входа."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            await redis.delete(f"failed_login_count:{ip}")
            await redis.delete(f"login_vel:{ip}")
    except Exception:
        pass

    try:
        async with core_db.async_session_maker() as session:
            await session.execute(delete(LoginAttempt).where(LoginAttempt.ip == ip))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось очистить попытки входа")
