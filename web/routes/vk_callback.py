"""Маршрутизатор входящих вебхуков VK Callback API.

Принимает события от серверов ВКонтакте через HTTP POST, производит
валидацию секретного ключа сообщества, рукопожатие confirmation,
сохраняет входящие события в Durable Inbox перед отправкой ACK ('ok'),
дедуплицирует повторные события и запускает их обработку.
"""

import asyncio
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bots.vk.bot import vk_bot
from core.config import settings
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import VkInbox

logger = logging.getLogger(__name__)

router = APIRouter(tags=["VK Callback API"])

# Удержание ссылок на активные фоновые задачи обработки событий
_callback_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> asyncio.Task:
    _callback_tasks.add(task)
    task.add_done_callback(_callback_tasks.discard)
    return task


async def _mark_inbox_processed(inbox_id: int) -> None:
    try:
        async with async_session_maker() as session:
            entry = await session.get(VkInbox, inbox_id)
            if entry is not None:
                entry.status = "processed"
                entry.processed_at = datetime.now(UTC)
                await session.commit()
    except Exception as exc:
        logger.warning("Не удалось отметить inbox #%s как обработанный: %s", inbox_id, exc)


async def _mark_inbox_failed(inbox_id: int, error_message: str) -> None:
    try:
        async with async_session_maker() as session:
            entry = await session.get(VkInbox, inbox_id)
            if entry is not None:
                entry.status = "failed"
                entry.error = error_message[:1000]
                await session.commit()
    except Exception as exc:
        logger.warning("Не удалось отметить inbox #%s как failed: %s", inbox_id, exc)


async def _handle_vk_event_async(event: dict[str, Any], inbox_id: int | None = None) -> None:
    """Асинхронная обработка события через пайплайн правил vkbottle."""
    try:
        touch_heartbeat()
        event_type = event.get("type")
        if event_type == "message_reply":
            from core.services.community_reply_service import handle_community_message_reply

            obj = event.get("object") or {}
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            peer_id = msg.get("peer_id") or obj.get("peer_id")
            text = msg.get("text") or obj.get("text") or ""
            admin_author_id = msg.get("admin_author_id") or obj.get("admin_author_id")
            reply_message = msg.get("reply_message") or obj.get("reply_message")
            fwd_messages = msg.get("fwd_messages") or obj.get("fwd_messages")
            if peer_id and text:
                await handle_community_message_reply(
                    peer_id=int(peer_id),
                    text=str(text),
                    admin_author_id=int(admin_author_id) if admin_author_id else None,
                    reply_message=reply_message if isinstance(reply_message, dict) else None,
                    fwd_messages=fwd_messages if isinstance(fwd_messages, list) else None,
                )
        await vk_bot.process_event(event)
        if inbox_id is not None:
            await _mark_inbox_processed(inbox_id)
    except Exception as exc:
        logger.exception("Ошибка при асинхронной обработке события Callback API: %s", exc)
        if inbox_id is not None:
            await _mark_inbox_failed(inbox_id, str(exc))


async def process_pending_inbox_events(batch_size: int = 50) -> int:
    """Обработать зависшие события inbox (например, после перезапуска сервиса)."""
    async with async_session_maker() as session:
        pending = (
            await session.scalars(
                select(VkInbox)
                .where(VkInbox.status == "pending")
                .order_by(VkInbox.created_at.asc(), VkInbox.id.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        to_process = [(entry.id, json.loads(entry.payload)) for entry in pending if entry.payload]

    for item_id, payload in to_process:
        await _handle_vk_event_async(payload, inbox_id=item_id)
    return len(to_process)


async def _save_event_to_inbox(
    data: dict[str, Any],
    event_type: str,
    event_id: Any,
    group_id: Any,
) -> tuple[bool, int | None]:
    """Сохранить событие в durable inbox. Возвращает (is_duplicate, inbox_id)."""
    async with async_session_maker() as session:
        if event_id:
            existing = await session.scalar(
                select(VkInbox.id).where(VkInbox.event_id == str(event_id))
            )
            if existing is not None:
                logger.info(
                    "Повторное событие VK Callback API event_id=%s, возврат 'ok'", event_id
                )
                return True, None

        inbox_entry = VkInbox(
            event_id=str(event_id) if event_id is not None else None,
            event_type=str(event_type),
            group_id=int(group_id) if group_id is not None else None,
            payload=json.dumps(data, ensure_ascii=False),
            status="pending",
        )
        session.add(inbox_entry)
        await session.commit()
        return False, inbox_entry.id


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

    # 1. Валидация (до раскрытия confirmation токена) секретного ключа Callback API
    if settings.VK_CALLBACK_SECRET:
        secret_received = data.get("secret")
        if not secrets.compare_digest(str(secret_received or ""), settings.VK_CALLBACK_SECRET):
            logger.warning("Отклонен запрос Callback API: неверный секретный ключ secret")
            return Response(content="forbidden", status_code=403, media_type="text/plain")
    elif settings.IS_PRODUCTION:
        logger.error(
            "Запрос Callback API в production отклонен: VK_CALLBACK_SECRET не настроен в .env"
        )
        return Response(content="secret_not_configured", status_code=403, media_type="text/plain")

    # 2. Проверка рукопожатия подтверждения сервера VK (confirmation)
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

    if not event_type:
        return Response(content="ok", status_code=200, media_type="text/plain")

    # 3. Durable Inbox: извлечение идентификаторов, дедупликация и персистентность до ACK
    event_id = data.get("event_id")
    group_id = data.get("group_id")

    try:
        is_duplicate, inbox_id = await _save_event_to_inbox(data, event_type, event_id, group_id)
        if is_duplicate:
            return Response(content="ok", status_code=200, media_type="text/plain")
    except IntegrityError:
        logger.info(
            "Параллельный дубликат события VK Callback API event_id=%s, возврат 'ok'", event_id
        )
        return Response(content="ok", status_code=200, media_type="text/plain")
    except Exception as exc:
        logger.exception("Ошибка сохранения входящего события в vk_inbox: %s", exc)
        return Response(content="Internal Server Error", status_code=500, media_type="text/plain")

    # 4. Фоновая диспетчеризация сохраненного события
    task = asyncio.create_task(_handle_vk_event_async(data, inbox_id=inbox_id))
    _track_task(task)

    # 5. Ответ серверам VK строго 'ok' в течение < 5 секунд
    return Response(content="ok", status_code=200, media_type="text/plain")
