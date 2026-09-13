import logging
from datetime import UTC, datetime

from sqlalchemy import text

from core.config import settings

logger = logging.getLogger(__name__)

MAX_ALLOWED_DRIFT_SECONDS = 5.0


async def check_time_sync(session) -> dict:
    try:
        db_res = await session.execute(text("SELECT CURRENT_TIMESTAMP"))
        db_raw = db_res.scalar()
        if db_raw is None:
            return {"status": "error", "detail": "failed_to_query_time", "synchronized": False}

        db_time = db_raw.replace(tzinfo=UTC) if db_raw.tzinfo is None else db_raw.astimezone(UTC)

        app_time = datetime.now(UTC)
        drift = abs((app_time - db_time).total_seconds())
        is_synced = drift <= MAX_ALLOWED_DRIFT_SECONDS

        if not is_synced:
            logger.warning(
                "Обнаружен дрейф времени: app=%s, db=%s, drift=%.2fs > %.1fs",
                app_time.isoformat(),
                db_time.isoformat(),
                drift,
                MAX_ALLOWED_DRIFT_SECONDS,
            )

        return {
            "status": "ok" if is_synced else "warning",
            "synchronized": is_synced,
            "drift_seconds": round(drift, 3),
            "app_time_utc": app_time.isoformat(),
            "db_time_utc": db_time.isoformat(),
            "timezone": settings.APP_TIMEZONE,
        }
    except Exception as e:
        logger.error("Ошибка при проверке синхронизации времени: %s", e)
        return {
            "status": "error",
            "detail": str(e),
            "synchronized": False,
        }
