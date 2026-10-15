"""Централизованная система оповещений информационной безопасности.

Отправляет экстренные предупреждения администратору в Telegram (и VK при наличии)
при обнаружении подозрительной активности:
- Брутфорс и превышение лимитов входа (/auth/login)
- Серия неудачных попыток аутентификации
- Попытки несанкционированного доступа к чужим данным (IDOR)
- Зондирование системных файлов и эксплойтов (RCE, .env, .git, phpunit)

Включает защиту от спама (Anti-Spam Cooldown):
При массовой атаке сканера отправляется одно предупреждение в 5 минут на IP,
а последующие попытки подсчитываются и включаются в итоговый отчёт.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.time_utils import format_app_datetime, now_app_tz

logger = logging.getLogger(__name__)

# Память для fallback cooldown, если Redis недоступен
_memory_cooldowns: dict[str, float] = {}
_memory_suppressed: dict[str, int] = {}

COOLDOWN_SECONDS = 300  # 5 минут на один IP и тип инцидента


async def _is_in_cooldown(cooldown_key: str, suppressed_key: str) -> tuple[bool, int]:
    """Проверить, находится ли событие на кулдауне, и увеличить счётчик подавленных алертов."""
    now = time.time()
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            is_cool = await redis.get(cooldown_key)
            if is_cool:
                suppressed = await redis.incr(suppressed_key)
                await redis.expire(suppressed_key, COOLDOWN_SECONDS * 2)
                return True, int(suppressed)

            # Кулдаун не активен: забираем накопленные подавленные события
            suppressed_val = await redis.get(suppressed_key)
            suppressed_count = int(suppressed_val) if suppressed_val else 0
            if suppressed_count > 0:
                await redis.delete(suppressed_key)

            await redis.set(cooldown_key, "1", ex=COOLDOWN_SECONDS)
            return False, suppressed_count
    except Exception:
        pass

    # Fallback на in-memory
    last_time = _memory_cooldowns.get(cooldown_key, 0.0)
    if now - last_time < COOLDOWN_SECONDS:
        _memory_suppressed[suppressed_key] = _memory_suppressed.get(suppressed_key, 0) + 1
        return True, _memory_suppressed[suppressed_key]

    suppressed_count = _memory_suppressed.pop(suppressed_key, 0)
    _memory_cooldowns[cooldown_key] = now
    return False, suppressed_count


async def send_security_alert(
    event_type: str,
    title: str,
    details: dict[str, Any],
) -> bool:
    """Отправить предупреждение о безопасности в Telegram администратора.

    Args:
        event_type: Идентификатор типа события (например: 'login_bruteforce', 'idor_attempt', 'exploit_probe')
        title: Человекочитаемый заголовок
        details: Словарь с подробностями (ip, username, path, reason и т.д.)

    Returns:
        True если уведомление отправлено, False если пропущено (кулдаун или ненастроенный канал).
    """
    ip = str(details.get("ip") or "unknown")
    cooldown_key = f"sec_alert_cd:{event_type}:{ip}"
    suppressed_key = f"sec_alert_sup:{event_type}:{ip}"

    in_cooldown, suppressed_count = await _is_in_cooldown(cooldown_key, suppressed_key)
    if in_cooldown:
        logger.debug(
            "Security alert %s для IP %s пропущен из-за cooldown (подавлено всего: %d)",
            event_type,
            ip,
            suppressed_count,
        )
        return False

    now_str = format_app_datetime(now_app_tz(), "%d.%m.%Y %H:%M:%S") + " МСК"
    username = str(details.get("username") or "не указан")
    path = str(details.get("path") or "не указан")
    reason = str(details.get("reason") or "Подозрительное действие")

    suppressed_note = ""
    if suppressed_count > 0:
        suppressed_note = (
            f"\nℹ️ <i>Подавлено повторных инцидентов за время кулдауна: {suppressed_count}</i>\n"
        )

    text = (
        "🚨 <b>СИСТЕМА БЕЗОПАСНОСТИ: ПОДОЗРИТЕЛЬНАЯ АКТИВНОСТЬ</b>\n\n"
        f"<b>Событие:</b> {title}\n"
        f"<b>IP-адрес:</b> <code>{ip}</code>\n"
        f"<b>Пользователь / логин:</b> <code>{username}</code>\n"
        f"<b>Путь:</b> <code>{path}</code>\n"
        f"<b>Детали:</b> {reason}\n"
        f"{suppressed_note}"
        f"<b>Время:</b> {now_str}"
    )

    sent = False

    # 1. Отправка в Telegram
    if (
        settings.TELEGRAM_ALERTS_ENABLED
        and settings.TELEGRAM_BOT_TOKEN
        and settings.TELEGRAM_ADMIN_ID > 0
    ):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": settings.TELEGRAM_ADMIN_ID,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
                if resp.status_code == 200:
                    sent = True
                else:
                    logger.warning(
                        "Telegram alert returned status %s: %s", resp.status_code, resp.text
                    )
        except Exception as exc:
            logger.error("Не удалось отправить security-алерт в Telegram: %s", exc)

    # 2. Дублирование в VK суперадмину, если настроен VK_REPORT_ADMIN_ID
    if settings.VK_REPORT_ADMIN_ID > 0:
        try:
            from core.vk_client import send_vk_message

            vk_text = (
                f"🚨 СИСТЕМА БЕЗОПАСНОСТИ\n"
                f"Событие: {title}\n"
                f"IP: {ip}\n"
                f"Логин: {username}\n"
                f"Путь: {path}\n"
                f"Детали: {reason}\n"
                f"Время: {now_str}"
            )
            await send_vk_message(settings.VK_REPORT_ADMIN_ID, vk_text)
            sent = True
        except Exception as exc:
            logger.debug("Не удалось продублировать security-алерт в VK: %s", exc)

    logger.warning(
        "SECURITY ALERT DISPATCHED [%s] IP=%s user=%s: %s", event_type, ip, username, title
    )
    return sent
