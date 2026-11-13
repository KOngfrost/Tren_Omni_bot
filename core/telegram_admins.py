"""управление доверенными администраторами telegram-бота (динамический acl)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

TELEGRAM_ADMINS_REDIS_KEY = "oss_bot:telegram:admin_ids"
TELEGRAM_ADMINS_FALLBACK_FILE = os.path.join(
    tempfile.gettempdir(), "oss_bot_telegram_admins.json"
)

_cached_admins: dict[str, Any] | None = None
_cached_expires_at: float = 0.0
_CACHE_TTL_SECONDS: float = 2.0


def _read_local_fallback() -> dict[str, Any] | None:
    """прочитать состояние из резервного файла на диске."""
    try:
        if os.path.exists(TELEGRAM_ADMINS_FALLBACK_FILE):
            with open(TELEGRAM_ADMINS_FALLBACK_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("не удалось прочитать локальный файл администраторов: %s", exc)
    return None


def _write_fallback_admin_ids(data: dict[str, Any]) -> None:
    """атомарно записать список администраторов в резервный файл."""
    try:
        tmp_path = TELEGRAM_ADMINS_FALLBACK_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, TELEGRAM_ADMINS_FALLBACK_FILE)
    except Exception as exc:
        logger.debug("не удалось записать локальный файл администраторов: %s", exc)


async def _read_redis_admin_ids() -> set[int]:
    """прочитать набор id дополнительных администраторов из redis."""
    try:
        redis = await get_redis_client()
        if redis is not None:
            raw = await redis.get(TELEGRAM_ADMINS_REDIS_KEY)
            if raw:
                try:
                    payload = json.loads(raw)
                    return {int(uid) for uid in payload.get("admin_ids", [])}
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("ошибка при чтении администраторов из redis: %s", exc)
    return set()


async def _write_redis_admin_ids(data: dict[str, Any]) -> bool:
    """сохранить состояние администраторов в redis."""
    try:
        redis = await get_redis_client()
        if redis is not None:
            await redis.set(
                TELEGRAM_ADMINS_REDIS_KEY,
                json.dumps(data, ensure_ascii=False),
            )
            return True
    except Exception as exc:
        logger.error("ошибка при сохранении администраторов в redis: %s", exc)
    return False


async def get_authorized_telegram_admin_ids(root_id: int) -> set[int]:
    """получить полный набор id всех авторизованных администраторов."""
    global _cached_admins, _cached_expires_at

    now = time.time()
    if _cached_admins is not None and now < _cached_expires_at:
        ids = {int(uid) for uid in _cached_admins.get("admin_ids", [])}
        if root_id > 0:
            ids.add(root_id)
        return ids

    # 1. Читаем из Redis
    redis_ids = await _read_redis_admin_ids()
    if redis_ids:
        ids = set(redis_ids)
        if root_id > 0:
            ids.add(root_id)
        _cached_admins = {"admin_ids": list(ids)}
        _cached_expires_at = now + _CACHE_TTL_SECONDS
        return ids

    # 2. Фолбэк на диск
    fallback = _read_local_fallback()
    if fallback is not None:
        ids = {int(uid) for uid in fallback.get("admin_ids", [])}
        if root_id > 0:
            ids.add(root_id)
        _cached_admins = fallback
        _cached_expires_at = now + _CACHE_TTL_SECONDS
        return ids

    ids = {root_id} if root_id > 0 else set()
    _cached_admins = {"admin_ids": list(ids)}
    _cached_expires_at = now + _CACHE_TTL_SECONDS
    return ids


async def is_authorized_telegram_admin(user_id: int, root_id: int) -> bool:
    """проверить, имеет ли пользователь права администратора."""
    if user_id <= 0:
        return False
    if root_id > 0 and user_id == root_id:
        return True
    authorized_ids = await get_authorized_telegram_admin_ids(root_id)
    return user_id in authorized_ids


async def get_admin_details(root_id: int) -> dict[str, Any]:
    """получить подробную информацию об администраторах для отображения."""
    fallback = _read_local_fallback() or {}
    meta = fallback.get("metadata", {})

    all_ids = await get_authorized_telegram_admin_ids(root_id)
    details: list[dict[str, Any]] = []

    for aid in sorted(all_ids):
        is_root = (aid == root_id)
        m = meta.get(str(aid), {})
        details.append({
            "id": aid,
            "is_root": is_root,
            "role": "Главный тех-админ" if is_root else "Дополнительный админ",
            "added_at": m.get("added_at", "конфигурация .env" if is_root else ""),
            "added_by": m.get("added_by", ""),
            "note": m.get("note", ""),
        })

    return {
        "root_id": root_id,
        "admins": details,
        "total": len(details),
    }


def _reset_cache() -> None:
    """сбросить локальный кеш в оперативной памяти (для тестов и принудительного обновления)."""
    global _cached_admins, _cached_expires_at
    _cached_admins = None
    _cached_expires_at = 0.0


async def add_telegram_admin_id(
    admin_id: int, added_by: str, root_id: int = 0, note: str = ""
) -> tuple[bool, str]:
    """добавить нового доверенного администратора."""
    global _cached_admins, _cached_expires_at

    if admin_id <= 0:
        return False, "некорректный telegram id (должен быть положительным целым числом)"

    if root_id > 0 and admin_id == root_id:
        return False, f"пользователь с id {admin_id} уже является главным администратором"

    current_ids = await _read_redis_admin_ids()
    fallback = _read_local_fallback() or {}
    meta = fallback.get("metadata", {})

    if admin_id in current_ids or (fallback.get("admin_ids") and admin_id in fallback["admin_ids"]):
        return False, f"пользователь с id {admin_id} уже является администратором"

    updated_ids = set(current_ids)
    updated_ids.add(admin_id)

    meta[str(admin_id)] = {
        "added_at": datetime.now(UTC).isoformat(),
        "added_by": added_by,
        "note": note.strip(),
    }

    data = {
        "admin_ids": sorted(list(updated_ids)),
        "metadata": meta,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    await _write_redis_admin_ids(data)
    _write_fallback_admin_ids(data)

    _cached_admins = data
    _cached_expires_at = time.time() + _CACHE_TTL_SECONDS
    logger.info("добавлен telegram администратор id=%s (инициатор=%s)", admin_id, added_by)
    return True, f"администратор {admin_id} успешно добавлен"


async def remove_telegram_admin_id(
    admin_id: int, root_id: int, removed_by: str
) -> tuple[bool, str]:
    """удалить администратора из белого списка."""
    global _cached_admins, _cached_expires_at

    if root_id > 0 and admin_id == root_id:
        return False, "запрещено удалять главного администратора системы"

    current_ids = await _read_redis_admin_ids()
    fallback = _read_local_fallback() or {}
    meta = fallback.get("metadata", {})

    all_stored = set(current_ids) | {int(uid) for uid in fallback.get("admin_ids", [])}
    if admin_id not in all_stored:
        return False, f"администратор с id {admin_id} не найден в списке"

    all_stored.discard(admin_id)
    meta.pop(str(admin_id), None)

    data = {
        "admin_ids": sorted(list(all_stored)),
        "metadata": meta,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    await _write_redis_admin_ids(data)
    _write_fallback_admin_ids(data)

    _cached_admins = data
    _cached_expires_at = time.time() + _CACHE_TTL_SECONDS
    logger.info("удален telegram администратор id=%s (инициатор=%s)", admin_id, removed_by)
    return True, f"администратор {admin_id} успешно удален из системы"
