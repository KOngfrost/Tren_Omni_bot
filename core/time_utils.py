"""
Утилиты для контроля синхронизации системного времени и единого формата дат.

Зачем это нужно:
Если часы на сервере приложения и в СУБД PostgreSQL расходятся (дрейф времени),
это приводит к неверной сортировке тикетов, сбоям в расписании ежедневных отчётов
и некорректным таймстампом аудита.

Модуль также служит ЕДИНЫМ источником времени для всего проекта: `now_app_tz()`
и `format_app_datetime()` гарантируют, что веб-панель, API и Telegram-мониторинг
показывают одно и то же время в часовом поясе из .env (APP_TIMEZONE).
"""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from core.config import settings

logger = logging.getLogger(__name__)

# Максимально допустимый дрейф времени между приложением и БД (в секундах)
MAX_ALLOWED_DRIFT_SECONDS = 5.0


def get_app_tz() -> ZoneInfo:
    """Часовой пояс приложения (APP_TIMEZONE, по умолчанию Europe/Moscow)."""
    return ZoneInfo(settings.APP_TIMEZONE)


def now_app_tz() -> datetime:
    """Текущее время СТРОГО в часовом поясе приложения (APP_TIMEZONE).

    Единая точка получения «сейчас» для всего проекта. Заменяет разрозненные
    `datetime.now(UTC)` и локальные `strftime` без таймзоны, из-за которых
    интерфейсы показывали разное время для одних и тех же событий.
    """
    return datetime.now(get_app_tz())


def format_app_datetime(dt: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Единый форматтер дат проекта с гарантированной конвертацией в APP_TIMEZONE.

    Наивные datetime (например, из SQLite в тестах) считаются UTC — именно в
    этом поясе приложение пишет все колонки `DateTime(timezone=True)`.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(get_app_tz()).strftime(fmt)
    except Exception:  # pragma: no cover - защита от битых данных в БД
        return dt.strftime(fmt)


_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Понедельник = 0 (совпадает с datetime.date.weekday()).
_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def ensure_aware(dt: datetime | None) -> datetime | None:
    """Привести datetime к timezone-aware, если он «naive».

    Зачем: разные драйверы отдают даты по-разному. PostgreSQL возвращает
    datetime с tzinfo, а SQLite (и, например, MySQL) — без него. Сравнение
    naive и aware значений бросает TypeError, поэтому перед любым сравнением
    с now_app_tz() нужно привести значение к одному виду.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=get_app_tz())
    return dt


def day_start_app_tz() -> datetime:
    """Начало сегодняшних суток в часовом поясе приложения.

    Граница суток считается по МСК (APP_TIMEZONE), а не по UTC: «сегодня»
    для администратора должно совпадать с его календарём, а не со смещением
    на 3 часа. Используется в метриках и отчётах за день.
    """
    return now_app_tz().replace(hour=0, minute=0, second=0, microsecond=0)


def humanize_last_seen(dt: datetime | None) -> str:
    """Человекочитаемое время последнего визита по МСК.

    «Только что», «12 мин назад», «сегодня в 15:42», «вчера в 09:10»,
    «12 сентября в 18:03» или «Не входил», если визита не было.

    Названия дней и месяцев заданы явно, а не через strftime('%A'), чтобы вывод
    не зависел от локали системы (в контейнерах её обычно нет).
    """
    if dt is None:
        return "Не входил"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(get_app_tz())
    now = now_app_tz()

    seconds = (now - local).total_seconds()

    # Отрицательное значение = часы БД/хоста спешат; не показываем «-3 мин назад».
    if seconds < 150:
        return "Только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"

    day_shift = (now.date() - local.date()).days
    if day_shift == 0:
        return f"сегодня в {local:%H:%M}"
    if day_shift == 1:
        return f"вчера в {local:%H:%M}"
    if 1 < day_shift < 7:
        return f"{_WEEKDAYS[local.weekday()]} в {local:%H:%M}"

    return f"{local.day} {_MONTHS_GENITIVE[local.month - 1]} в {local:%H:%M}"


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

        # Некоторые драйверы (SQLite в тестах) отдают CURRENT_TIMESTAMP строкой,
        # а не datetime — приводим к типу явно, иначе сравнение падает.
        if isinstance(db_raw, str):
            db_raw = datetime.fromisoformat(db_raw)

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
