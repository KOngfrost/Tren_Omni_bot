"""Heartbeat-файл бота: timestamp последней активности процесса.

Используется docker healthcheck: скрипт scripts/healthcheck_bot.py
проверяет возраст файла и считает контейнер нездоровым, если бот
давно не обновлял heartbeat.
"""

import os
import tempfile
import time
from datetime import UTC, datetime

HEARTBEAT_FILE = os.getenv(
    "HEARTBEAT_FILE",
    os.path.join(tempfile.gettempdir(), "oss_bot_heartbeat"),
)
MAX_AGE_SECONDS = int(os.getenv("HEARTBEAT_MAX_AGE", "600"))  # 10 минут


def touch_heartbeat() -> None:
    """Обновить heartbeat-файл (текущий timestamp)."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        # Heartbeat не должен ломать работу бота
        pass


def heartbeat_age_seconds() -> float | None:
    """Возраст heartbeat-файла в секундах или None, если файла нет."""
    try:
        mtime = os.path.getmtime(HEARTBEAT_FILE)
    except OSError:
        return None
    return time.time() - mtime


def is_healthy() -> bool:
    """Здоров ли бот: heartbeat свежий."""
    age = heartbeat_age_seconds()
    return age is not None and age <= MAX_AGE_SECONDS


def heartbeat_timestamp() -> str:
    """Человекочитаемое время последнего heartbeat."""
    age = heartbeat_age_seconds()
    if age is None:
        return "нет данных"
    dt = datetime.fromtimestamp(time.time() - age, tz=UTC)
    return dt.isoformat()
