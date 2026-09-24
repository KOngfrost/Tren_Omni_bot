"""Управление статусом двухфакторной аутентификации (2FA).

Позволяет динамически включать и отключать двухфакторную аутентификацию
в веб-панели через настройки или Telegram-бота мониторинга.
Статус хранится в распределённом Redis-хранилище (персистентно через AOF)
с локальным in-memory кешированием, файловым fallback-хранилищем
и значением по умолчанию из конфигурации (TWO_FACTOR_ENABLED).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

TWO_FACTOR_REDIS_KEY = "oss_bot:system:2fa_enabled"
TWO_FACTOR_FALLBACK_FILE = os.path.join(tempfile.gettempdir(), "oss_bot_2fa_state.json")

# Локальный кеш в оперативной памяти процесса (TTL 2 секунды)
_cached_state: dict[str, Any] | None = None
_cached_expires_at: float = 0.0
_CACHE_TTL_SECONDS: float = 2.0


def _read_local_fallback() -> dict[str, Any] | None:
    """Прочитать состояние из резервного файла на диске."""
    try:
        if os.path.exists(TWO_FACTOR_FALLBACK_FILE):
            with open(TWO_FACTOR_FALLBACK_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Не удалось прочитать локальный fallback-файл 2FA: %s", e)
    return None


def _write_local_fallback(data: dict[str, Any]) -> None:
    """Атомарно записать состояние в резервный файл на диске."""
    try:
        tmp_path = TWO_FACTOR_FALLBACK_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, TWO_FACTOR_FALLBACK_FILE)
    except Exception as e:
        logger.debug("Не удалось записать локальный fallback-файл 2FA: %s", e)


async def get_two_factor_info() -> dict[str, Any]:
    """Получить подробную информацию о статусе 2FA."""
    global _cached_state, _cached_expires_at

    now = time.time()
    if _cached_state is not None and now < _cached_expires_at:
        return _cached_state

    # 1. Запрос в Redis
    try:
        redis = await get_redis_client()
        if redis is not None:
            raw = await redis.get(TWO_FACTOR_REDIS_KEY)
            if raw:
                try:
                    data = json.loads(raw)
                    _cached_state = data
                    _cached_expires_at = now + _CACHE_TTL_SECONDS
                    return data
                except Exception:
                    pass
            # Ключ отсутствует в Redis — берём значение из конфигурации .env
            default_data = {
                "enabled": bool(settings.TWO_FACTOR_ENABLED),
                "source": "config",
                "updated_at": "",
                "updated_by": "system",
            }
            _cached_state = default_data
            _cached_expires_at = now + _CACHE_TTL_SECONDS
            return default_data
    except Exception as e:
        logger.debug("Ошибка получения статуса 2FA из Redis (%s), проверка fallback...", e)

    # 2. Локальный fallback-файл на диске
    disk_data = _read_local_fallback()
    if disk_data is not None:
        _cached_state = disk_data
        _cached_expires_at = now + _CACHE_TTL_SECONDS
        return disk_data

    # 3. Базовое значение из конфигурации
    fallback_data = {
        "enabled": bool(settings.TWO_FACTOR_ENABLED),
        "source": "config_fallback",
        "updated_at": "",
        "updated_by": "system",
    }
    _cached_state = fallback_data
    _cached_expires_at = now + _CACHE_TTL_SECONDS
    return fallback_data


async def is_two_factor_enabled() -> bool:
    """Проверить, активна ли в данный момент двухфакторная аутентификация."""
    info = await get_two_factor_info()
    return bool(info.get("enabled", True))


async def set_two_factor_mode(enabled: bool, updated_by: str = "admin") -> dict[str, Any]:
    """Включить или отключить двухфакторную аутентификацию (2FA)."""
    global _cached_state, _cached_expires_at

    now_iso = datetime.now(UTC).isoformat()
    data: dict[str, Any] = {
        "enabled": bool(enabled),
        "source": "override",
        "updated_at": now_iso,
        "updated_by": updated_by or "admin",
    }

    # Синхронизируем флаг в памяти конфигурации
    settings.TWO_FACTOR_ENABLED = bool(enabled)

    # 1. Сохраняем в Redis
    try:
        redis = await get_redis_client()
        if redis is not None:
            await redis.set(TWO_FACTOR_REDIS_KEY, json.dumps(data, ensure_ascii=False))
            logger.info("2FA статус успешно обновлен в Redis: enabled=%s (by %s)", enabled, updated_by)
    except Exception as e:
        logger.error("Не удалось сохранить статус 2FA в Redis: %s", e)

    # 2. Сохраняем в локальный fallback-файл
    _write_local_fallback(data)

    # 3. Обновляем in-memory кеш
    _cached_state = data
    _cached_expires_at = time.time() + _CACHE_TTL_SECONDS

    logger.info("SECURITY AUDIT: 2FA_MODE_CHANGED | enabled=%s | updated_by=%s", enabled, updated_by)
    return data
