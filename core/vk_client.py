"""Единая точка отправки сообщений в VK через HTTP API.

Раньше сырой вызов messages.send жил в ticket_service, а бот отправлял
сообщения через собственный vk_bot.api — получалось два пути уведомлений.
Теперь все НЕ-ботовые уведомления идут через send_vk_message(),
а текстовые сообщения студентам — через outbox-воркер (core/outbox.py).
"""

import asyncio
import logging
import random

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"

# Для outbox-доставки: сколько попыток и пауза между ними (секунды)
OUTBOX_MAX_ATTEMPTS = 5

# Единый HTTP-клиент для всех VK-запросов. Переиспользует TCP-соединения
# (keep-alive), что заметно ускоряет доставку при ответах на сотни заявок.
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_vk_client() -> httpx.AsyncClient:
    """Вернуть общий HTTP-клиент (создаётся один раз, переиспользует пул)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
            ),
        )
        _client_loop = loop
    return _client


async def close_vk_client() -> None:
    """Закрыть общий HTTP-клиент при завершении процесса."""
    global _client, _client_loop
    if _client is not None:
        await _client.aclose()
        _client = None
        _client_loop = None


async def send_vk_message(vk_id: int, text: str) -> bool:
    """Отправить сообщение студенту через VK API (HTTP).

    Возвращает True при успехе. Ошибки не бросаются и не прерывают
    бизнес-процесс — сообщение либо повторно доставит outbox-воркер,
    либо останется зафиксированным в БД (лучше, чем наоборот).
    """
    if not vk_id:
        return False
    if not settings.VK_BOT_TOKEN:
        logger.warning("VK_BOT_TOKEN не задан: сообщение vk_id=%s не отправлено", vk_id)
        return False

    try:
        client = get_vk_client()
        response = await client.post(
            VK_API_URL,
            data={
                "access_token": settings.VK_BOT_TOKEN,
                "v": VK_API_VERSION,
                "peer_id": vk_id,
                "random_id": random.randint(1, 2**31 - 1),
                "message": text[:4000],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            logger.error(
                "VK API error при отправке сообщения vk_id=%s: %s",
                vk_id,
                payload["error"].get("error_msg"),
            )
            return False
        return True
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Не удалось отправить VK-сообщение vk_id=%s: %s", vk_id, error)
        return False
