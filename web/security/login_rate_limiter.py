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
    """Проверить, превышен ли лимит неудачных попыток для IP."""
    return await _db_recent_failed_count(session, ip) >= LOGIN_MAX_ATTEMPTS


async def record_failed_attempt(ip: str) -> None:
    """Записать неудачную попытку в Redis и БД."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            key = f"failed_login_count:{ip}"
            await redis.incr(key)
            await redis.expire(key, LOGIN_WINDOW_SECONDS)
    except Exception:
        pass

    try:
        async with core_db.async_session_maker() as session:
            session.add(LoginAttempt(ip=ip, success=False))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось записать попытку входа в БД")


async def clear_attempts(ip: str) -> None:
    """Сбросить лимит после успешного входа."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            await redis.delete(f"failed_login_count:{ip}")
    except Exception:
        pass

    try:
        async with core_db.async_session_maker() as session:
            await session.execute(delete(LoginAttempt).where(LoginAttempt.ip == ip))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось очистить попытки входа")
