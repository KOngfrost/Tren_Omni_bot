"""Управление режимом технических работ (Maintenance Mode).

Позволяет переводить веб-панель и ботов в режим технического обслуживания.
Статус хранится в распределённом Redis-хранилище (персистентно через AOF)
с локальным in-memory кешированием и файловым fallback-хранилищем.
Управление режимом осуществляется исключительно доверенным администратором через Telegram-бота.
"""

from __future__ import annotations

import json
import logging
import os
import time
import tempfile
from datetime import UTC, datetime
from typing import Any

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

MAINTENANCE_REDIS_KEY = "oss_bot:system:maintenance"
MAINTENANCE_FALLBACK_FILE = os.path.join(tempfile.gettempdir(), "oss_bot_maintenance_state.json")

DEFAULT_MAINTENANCE_MESSAGE = (
    "В данный момент проводятся плановые технические работы. "
    "Приём и обработка обращений временно приостановлены. "
    "Мы скоро вернёмся!"
)

DEFAULT_BOT_MAINTENANCE_MESSAGE = (
    "🛠 <b>Ведутся технические работы</b>\n\n"
    "В данный момент проводятся плановые технические работы. "
    "Приём и обработка обращений студентов временно приостановлены.\n\n"
    "Приносим извинения за временные неудобства. Пожалуйста, повторите попытку позже."
)

# Локальный кеш в оперативной памяти процесса (TTL 2 секунды)
# исключает избыточную нагрузку на Redis при высоких RPS в веб-панели и поллинге.
_cached_state: dict[str, Any] | None = None
_cached_expires_at: float = 0.0
_CACHE_TTL_SECONDS: float = 2.0


def _read_local_fallback() -> dict[str, Any] | None:
    """Прочитать состояние из резервного файла на диске."""
    try:
        if os.path.exists(MAINTENANCE_FALLBACK_FILE):
            with open(MAINTENANCE_FALLBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Не удалось прочитать локальный файл техработ: %s", e)
    return None


def _write_local_fallback(data: dict[str, Any]) -> None:
    """Атомарно записать состояние в резервный файл на диске."""
    try:
        tmp_path = MAINTENANCE_FALLBACK_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, MAINTENANCE_FALLBACK_FILE)
    except Exception as e:
        logger.debug("Не удалось записать локальный файл техработ: %s", e)


async def get_maintenance_info() -> dict[str, Any]:
    """Получить информацию о режиме техработ (активность, сообщение, время включения)."""
    global _cached_state, _cached_expires_at

    now = time.time()
    if _cached_state is not None and now < _cached_expires_at:
        return _cached_state

    # 1. Запрос в Redis
    try:
        redis = await get_redis_client()
        if redis is not None:
            raw = await redis.get(MAINTENANCE_REDIS_KEY)
            if raw:
                try:
                    data = json.loads(raw)
                    _cached_state = data
                    _cached_expires_at = now + _CACHE_TTL_SECONDS
                    return data
                except Exception:
                    pass
            # Ключ отсутствует — техработы выключены
            default_data = {
                "enabled": False,
                "message": DEFAULT_MAINTENANCE_MESSAGE,
                "bot_message": DEFAULT_BOT_MAINTENANCE_MESSAGE,
                "updated_at": "",
                "updated_by": "",
            }
            _cached_state = default_data
            _cached_expires_at = now + _CACHE_TTL_SECONDS
            return default_data
    except Exception as e:
        logger.debug("Ошибка при обращении к Redis для проверки техработ: %s", e)

    # 2. Фолбэк на резервный файл на диске
    fallback = _read_local_fallback()
    if fallback is not None:
        _cached_state = fallback
        _cached_expires_at = now + _CACHE_TTL_SECONDS
        return fallback

    default_data = {
        "enabled": False,
        "message": DEFAULT_MAINTENANCE_MESSAGE,
        "bot_message": DEFAULT_BOT_MAINTENANCE_MESSAGE,
        "updated_at": "",
        "updated_by": "",
    }
    _cached_state = default_data
    _cached_expires_at = now + _CACHE_TTL_SECONDS
    return default_data


async def is_maintenance_mode() -> bool:
    """Проверить, активен ли в данный момент режим техработ."""
    info = await get_maintenance_info()
    return bool(info.get("enabled", False))


async def set_maintenance_mode(
    enabled: bool,
    message: str = "",
    bot_message: str = "",
    updated_by: str = "telegram_admin",
) -> dict[str, Any]:
    """Включить или выключить режим техработ.

    Args:
        enabled: True для активации техработ, False для отключения.
        message: Сообщение для веб-панели (отображается на странице 503).
        bot_message: Сообщение для автоответа в VK-боте.
        updated_by: Идентификатор инициатора (например, "telegram:6665679072").

    Returns:
        dict с актуальным состоянием.
    """
    global _cached_state, _cached_expires_at

    now_iso = datetime.now(UTC).isoformat()
    msg = message.strip() if message else DEFAULT_MAINTENANCE_MESSAGE
    bot_msg = bot_message.strip() if bot_message else DEFAULT_BOT_MAINTENANCE_MESSAGE

    data = {
        "enabled": bool(enabled),
        "message": msg,
        "bot_message": bot_msg,
        "updated_at": now_iso,
        "updated_by": updated_by,
    }

    # 1. Запись в Redis
    try:
        redis = await get_redis_client()
        if redis is not None:
            await redis.set(MAINTENANCE_REDIS_KEY, json.dumps(data, ensure_ascii=False))
            logger.info(
                "Режим техработ изменён в Redis: enabled=%s, initiator=%s",
                enabled,
                updated_by,
            )
    except Exception as e:
        logger.error("Ошибка при сохранении статуса техработ в Redis: %s", e)

    # 2. Локальный резервный файл
    _write_local_fallback(data)

    # 3. Инвалидация локального кеша
    _cached_state = data
    _cached_expires_at = time.time() + _CACHE_TTL_SECONDS

    return data
