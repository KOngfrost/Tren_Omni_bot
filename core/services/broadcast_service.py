"""Сервис массовой рассылки сообщений пользователям (Broadcast Service).

Обеспечивает:
1. Выборку N последних пользователей по дате активности/обращения.
2. Транзакционную постановку сообщений в очередь VkOutbox (at-least-once).
3. Журналирование действия в аудит (Log).
4. Запуск фоновой доставки (fire_outbox_delivery).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Log, User
from core.outbox import add_outbox_message, fire_outbox_delivery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BroadcastResult:
    """Результат постановки рассылки в очередь."""

    total_requested: int
    recipients_count: int
    recipient_vk_ids: list[int]
    status: str
    error: str | None = None


async def get_recent_users(
    session: AsyncSession,
    limit: int,
) -> list[User]:
    """Получить N последних активных/обратившихся пользователей.

    Сортировка:
    - по дате последней активности/обращения (coalesce(last_seen_at, created_at) DESC)
    - вторично по ID пользователя (id DESC)
    """
    if limit <= 0:
        return []

    stmt = (
        select(User)
        .order_by(
            func.coalesce(User.last_seen_at, User.created_at).desc(),
            User.id.desc(),
        )
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def get_total_users_count(session: AsyncSession) -> int:
    """Общее количество зарегистрированных пользователей в системе."""
    count = await session.scalar(select(func.count(User.id)))
    return int(count or 0)


async def enqueue_broadcast(
    session: AsyncSession,
    text: str,
    count: int,
    author_name: str = "superadmin",
    actor_type: str = "web",
) -> BroadcastResult:
    """Поставить рассылку сообщения N последним пользователям в очередь доставки VkOutbox."""
    clean_text = text.strip()
    if not clean_text:
        return BroadcastResult(
            total_requested=count,
            recipients_count=0,
            recipient_vk_ids=[],
            status="error",
            error="Текст сообщения не может быть пустым.",
        )
    if count <= 0:
        return BroadcastResult(
            total_requested=count,
            recipients_count=0,
            recipient_vk_ids=[],
            status="error",
            error="Количество получателей должно быть положительным числом.",
        )

    users = await get_recent_users(session, limit=count)
    if not users:
        return BroadcastResult(
            total_requested=count,
            recipients_count=0,
            recipient_vk_ids=[],
            status="error",
            error="В системе пока нет пользователей для отправки сообщений.",
        )

    recipient_vk_ids: list[int] = []
    for user in users:
        if user.vk_id:
            add_outbox_message(session, vk_id=user.vk_id, text=clean_text)
            recipient_vk_ids.append(user.vk_id)

    # Журнал аудита действий администратора
    log_entry = Log(
        action="broadcast_sent",
        details=f"Рассылка сообщений ({len(recipient_vk_ids)} получателей): {clean_text[:120]}",
        actor_type=actor_type,
        actor_name=author_name,
        is_mutation=True,
    )
    session.add(log_entry)
    await session.commit()

    # Запускаем фоновую доставку в VK
    fire_outbox_delivery()

    logger.info(
        "Рассылка поставлена в VkOutbox: автор=%s, получателей=%d, запрошено=%d",
        author_name,
        len(recipient_vk_ids),
        count,
    )

    return BroadcastResult(
        total_requested=count,
        recipients_count=len(recipient_vk_ids),
        recipient_vk_ids=recipient_vk_ids,
        status="success",
    )
