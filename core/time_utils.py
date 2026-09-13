"""
Утилиты для контроля синхронизации системного времени.

Зачем это нужно:
Если часы на сервере приложения и в СУБД PostgreSQL расходятся (дрейф времени),
это приводит к неверной сортировке тикетов, сбоям в расписании ежедневных отчётов
и некорректным таймстампом аудита.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import text

from core.config import settings

logger = logging.getLogger(__name__)

# Максимально допустимый дрейф времени между приложением и БД (в секундах)
MAX_ALLOWED_DRIFT_SECONDS = 5.0


async def check_time_sync(session) -> dict:
    """Проверяет синхронизацию системного времени приложения и сервера PostgreSQL.

    Выполняет быстрый запрос к БД (CURRENT_TIMESTAMP), вычисляет абсолютную
    разницу в секундах и возвращает диагностический словарь для /health.
    """
    try:
        # Запрашиваем точное текущее время со стороны СУБД
        db_res = await session.execute(text("SELECT CURRENT_TIMESTAMP"))
        db_raw = db_res.scalar()
        if db_raw is None:
            return {"status": "error", "detail": "failed_to_query_time", "synchronized": False}

        # Приводим время БД к UTC для корректного сравнения
        db_time = db_raw.replace(tzinfo=UTC) if db_raw.tzinfo is None else db_raw.astimezone(UTC)

        # Локальное время приложения в UTC
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
