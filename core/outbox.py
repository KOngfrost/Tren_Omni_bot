"""Outbox: надёжная отправка VK-уведомлений.

Проблема, которую решает: раньше VK-сообщение уходило ДО фиксации
транзакции в БД. Если БД падала, студент получал ответ, которого нет
в истории заявки. Теперь:

1. Сообщение пишется в таблицу vk_outbox в ТОЙ ЖЕ транзакции,
   что и изменение заявки (commit гарантирует сохранение).
2. Фоновый воркер (в цикле бота и/или по расписанию веб-панели)
   доставляет отложенные сообщения через core.vk_client.send_vk_message.

Так мы получаем «at-least-once» доставку с ретраями вместо
«потерянного» ответа студенту.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.config import settings
from core.database import async_session_maker
from core.models import VkOutbox
from core.vk_client import OUTBOX_MAX_ATTEMPTS, send_vk_message

logger = logging.getLogger(__name__)

# Размер партии и частота опроса настраиваются в .env (OUTBOX_BATCH_SIZE,
# OUTBOX_INTERVAL_SECONDS). При нагрузке >2500 пользователей разумнее
# выносить больше сообщений за проход, чем делать частые мелкие опросы.
OUTBOX_BATCH_SIZE = settings.OUTBOX_BATCH_SIZE
OUTBOX_RETRY_DELAY_SECONDS = settings.OUTBOX_INTERVAL_SECONDS
OUTBOX_CLAIM_TIMEOUT_SECONDS = settings.OUTBOX_CLAIM_TIMEOUT_SECONDS


# Ссылки на запущенные задачи доставки: без сильной ссылки GC может
# уничтожить задачу до её завершения
_delivery_tasks: set[asyncio.Task] = set()


def fire_outbox_delivery() -> None:
    """Запустить фоновую доставку outbox, если есть работающий event loop.

    Вызывается после commit() там, где нужно доставить сообщение «прямо сейчас»
    (ответ администратора в веб-панели). Если loop недоступен (тесты,
    синхронный контекст) — ничего страшного: доставит периодический воркер.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Нет запущенного event loop — outbox доставит воркер")
        return
    task = asyncio.create_task(deliver_pending_messages())
    _delivery_tasks.add(task)
    task.add_done_callback(_delivery_tasks.discard)


def add_outbox_message(session, vk_id: int, text: str, random_id: int | None = None) -> VkOutbox:
    """Добавить сообщение в outbox в текущей транзакции (без commit)."""
    entry = VkOutbox(
        vk_id=vk_id,
        text=text,
        status="pending",
        attempts=0,
        random_id=random_id,
    )
    session.add(entry)
    return entry


def _reclaim_stale_sending_messages(
    messages: list[VkOutbox],
    now: datetime,
    backoff_base: int,
) -> None:
    """Вернуть зависшие сообщения воркеров в pending или failed при lease timeout."""
    for message in messages:
        message.attempts = (message.attempts or 0) + 1
        message.claimed_at = None
        if message.attempts >= OUTBOX_MAX_ATTEMPTS:
            message.status = "failed"
            message.error = "Превышен таймаут захвата воркером (lease timeout, Dead Letter)"
            logger.error(
                "Outbox: сообщение #%s окончательно помечено failed из-за lease timeout",
                message.id,
            )
        else:
            message.status = "pending"
            message.error = "Истёк таймаут захвата воркером (worker lease timeout)"
            if backoff_base > 0:
                delay = min(300, backoff_base * (2 ** (message.attempts - 1)))
                message.next_retry_at = now + timedelta(seconds=delay)
            else:
                message.next_retry_at = now
            logger.warning(
                "Outbox: сообщение #%s вернулось в pending по lease timeout (попытка %s/%s)",
                message.id,
                message.attempts,
                OUTBOX_MAX_ATTEMPTS,
            )


def _handle_single_result(
    result: VkOutbox,
    ok: bool,
    err: str | None,
    backoff_base: int,
) -> bool:
    """Обновить состояние отдельного outbox-сообщения по результату отправки."""
    result.attempts = (result.attempts or 0) + 1
    result.claimed_at = None
    if ok:
        result.status = "sent"
        result.sent_at = datetime.now(UTC)
        result.error = None
        logger.info("Outbox: сообщение #%s доставлено vk_id=%s", result.id, result.vk_id)
        return True

    result.error = err or "VK API недоступен или токен не задан"
    if result.attempts >= OUTBOX_MAX_ATTEMPTS:
        result.status = "failed"
        logger.error(
            "Outbox: сообщение #%s окончательно не доставлено vk_id=%s (Dead Letter)",
            result.id,
            result.vk_id,
        )
    else:
        result.status = "pending"
        logger.warning(
            "Outbox: сообщение #%s пока не доставлено (попытка %s/%s)",
            result.id,
            result.attempts,
            OUTBOX_MAX_ATTEMPTS,
        )
        if backoff_base > 0:
            delay = min(300, backoff_base * (2 ** (result.attempts - 1)))
            result.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        else:
            result.next_retry_at = datetime.now(UTC)
    return False


async def _send_batch_messages(
    to_send: list[tuple[int, int, str, int]],
) -> list[tuple[int, int, bool, str | None]]:
    """Выполнить отправку сообщений пачки через VK API."""
    results: list[tuple[int, int, bool, str | None]] = []
    for msg_id, vk_id, text, stable_random_id in to_send:
        try:
            try:
                ok = await send_vk_message(vk_id, text, random_id=stable_random_id)
            except TypeError:
                ok = await send_vk_message(vk_id, text)
            err = None if ok else "VK API вернул статус неуспешной отправки"
        except Exception as e:
            logger.exception(
                "Outbox: непредвиденная ошибка отправки msg_id=%s vk_id=%s", msg_id, vk_id
            )
            ok = False
            err = f"Ошибка отправки: {type(e).__name__}: {e}"
        results.append((msg_id, vk_id, ok, err))
        if len(to_send) > 1:
            await asyncio.sleep(0.05)
    return results


async def deliver_pending_messages(
    batch_size: int = OUTBOX_BATCH_SIZE,
    ignore_backoff: bool = False,
) -> int:
    """Доставить одну партию отложенных сообщений. Возвращает число попыток."""
    delivered = 0
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=OUTBOX_CLAIM_TIMEOUT_SECONDS)
    backoff_base = getattr(settings, "OUTBOX_BACKOFF_BASE_SECONDS", 5)

    async with async_session_maker() as session:
        stale = (
            await session.scalars(
                select(VkOutbox)
                .where(VkOutbox.status == "sending")
                .where(VkOutbox.claimed_at.is_not(None))
                .where(VkOutbox.claimed_at < stale_before)
                .with_for_update(skip_locked=True)
            )
        ).all()
        _reclaim_stale_sending_messages(list(stale), now, backoff_base)
        await session.flush()

        pending_query = select(VkOutbox).where(VkOutbox.status == "pending")
        if not ignore_backoff and backoff_base > 0:
            pending_query = pending_query.where(
                (VkOutbox.next_retry_at.is_(None)) | (VkOutbox.next_retry_at <= now)
            )
        pending_query = (
            pending_query.order_by(VkOutbox.created_at.asc(), VkOutbox.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        pending = (await session.scalars(pending_query)).all()

        to_send: list[tuple[int, int, str, int]] = []
        for message in pending:
            if message.id is not None and message.vk_id is not None and message.text is not None:
                message.status = "sending"
                message.claimed_at = now
                stable_random_id = message.random_id or ((int(message.id) & 0x7FFFFFFF) or 1)
                if message.random_id is None:
                    message.random_id = stable_random_id
                to_send.append((message.id, message.vk_id, message.text, stable_random_id))
        await session.commit()

    if not to_send:
        return 0

    results = await _send_batch_messages(to_send)

    async with async_session_maker() as session:
        for msg_id, _vk_id, ok, err in results:
            result = await session.get(VkOutbox, msg_id)
            if (
                result is not None
                and result.status == "sending"
                and _handle_single_result(result, ok, err, backoff_base)
            ):
                delivered += 1
        await session.commit()
    return delivered


async def outbox_worker_loop(interval: float | None = None) -> None:
    """Бесконечный цикл доставки outbox (бот и/или фоновые задачи панели).

    Single-instance: при нескольких процессах (бот + uvicorn-воркеры
    веб-панели) распределённая Redis-блокировка из core/task_dispatcher.py
    гарантирует, что партию за тик доставляет ровно один процесс; остальные
    пропускают тик.
    """
    from core.task_dispatcher import single_instance_guard

    delay = interval or OUTBOX_RETRY_DELAY_SECONDS
    while True:
        try:
            async with single_instance_guard("outbox-worker") as is_leader:
                if is_leader:
                    await deliver_pending_messages()
        except Exception:
            # Ни одна ошибка не должна ронять воркер
            logger.exception("Outbox worker: ошибка при доставке")
        await asyncio.sleep(delay)
