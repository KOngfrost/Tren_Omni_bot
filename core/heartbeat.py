"""Heartbeat-файл бота: timestamp последней активности процесса.

Используется docker healthcheck: скрипт scripts/healthcheck_bot.py
проверяет возраст файла и считает контейнер нездоровым, если бот
давно не обновлял heartbeat.
"""

import logging
import os
import tempfile
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = os.getenv(
    "HEARTBEAT_FILE",
    os.path.join(tempfile.gettempdir(), "oss_bot_heartbeat"),
)
try:
    MAX_AGE_SECONDS = int(os.getenv("HEARTBEAT_MAX_AGE", "600"))  # 10 минут
except (ValueError, TypeError):
    MAX_AGE_SECONDS = 600


def touch_heartbeat() -> None:
    """Обновить heartbeat-файл (текущий timestamp) атомарно."""
    tmp_file = f"{HEARTBEAT_FILE}.tmp.{os.getpid()}"
    try:
        with open(tmp_file, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
        os.replace(tmp_file, HEARTBEAT_FILE)
    except OSError as e:
        logger.warning("Не удалось обновить heartbeat-файл %s: %s", HEARTBEAT_FILE, e)
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
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
