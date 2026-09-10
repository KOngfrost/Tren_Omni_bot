"""Маршрутизатор входящих вебхуков VK Callback API.

Принимает события от серверов ВКонтакте через HTTP POST, производит
валидацию секретного ключа сообщества, рукопожатие confirmation,
и передаёт обработку событий в диспетчер бота в фоновом режиме.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request, Response

from bots.vk.bot import vk_bot
from core.config import settings
from core.heartbeat import touch_heartbeat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["VK Callback API"])

# Удержание ссылок на активные фоновые задачи обработки событий
_callback_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> asyncio.Task:
    _callback_tasks.add(task)
    task.add_done_callback(_callback_tasks.discard)
    return task


async def _handle_vk_event_async(event: dict[str, Any]) -> None:
    """Асинхронная обработка события через пайплайн правил vkbottle."""
    try:
        touch_heartbeat()
        await vk_bot.process_event(event)
    except Exception as exc:
        logger.exception("Ошибка при асинхронной обработке события Callback API: %s", exc)


@router.post("/api/v1/vk/callback", response_class=Response)
@router.post("/webhooks/vk", response_class=Response)
async def vk_callback_webhook(request: Request) -> Response:
    """Эндпоинт для приёма Callback API уведомлений от ВКонтакте."""
    try:
        data = await request.json()
    except Exception:
        logger.warning("Некорректный JSON в запросе VK Callback API")
        return Response(content="invalid json", status_code=400, media_type="text/plain")

    if not isinstance(data, dict):
        return Response(content="invalid payload", status_code=400, media_type="text/plain")

    event_type = data.get("type")

    # 1. Проверка рукопожатия подтверждения сервера VK (confirmation)
    if event_type == "confirmation":
        token = settings.VK_CONFIRMATION_TOKEN
        if not token:
            logger.error(
                "Получен запрос confirmation от VK, но VK_CONFIRMATION_TOKEN не задан в .env"
            )
            return Response(
                content="VK_CONFIRMATION_TOKEN not configured",
                status_code=500,
                media_type="text/plain",
            )
        logger.info("Успешное подтверждение Callback API адреса серверам VK")
        return Response(content=token, status_code=200, media_type="text/plain")

    # 2. Валидация секретного ключа Callback API
    if settings.VK_CALLBACK_SECRET:
        secret_received = data.get("secret")
        if secret_received != settings.VK_CALLBACK_SECRET:
            logger.warning("Отклонен запрос Callback API: неверный секретный ключ secret")
            return Response(content="forbidden", status_code=403, media_type="text/plain")

    # 3. Фоновая диспетчеризация события бота
    if event_type:
        task = asyncio.create_task(_handle_vk_event_async(data))
        _track_task(task)

    # 4. Ответ серверам VK строго 'ok' в течение < 5 секунд
    return Response(content="ok", status_code=200, media_type="text/plain")
