"""Сервис массовой рассылки сообщений пользователям (Broadcast Service).

Обеспечивает:
1. Выборку N последних пользователей по дате активности/обращения.
2. Транзакционную постановку сообщений в очередь VkOutbox (at-least-once).
3. Журналирование действия в аудит (Log).
4. Запуск фоновой доставки (fire_outbox_delivery).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Log, User
from core.outbox import add_outbox_message, fire_outbox_delivery

logger = logging.getLogger(__name__)

_VK_POST_REF_PATTERN = re.compile(
    r"(?:^|[\W_])wall(-?\d+_\d+)",
    re.IGNORECASE,
)


def parse_vk_post_reference(input_str: str | None) -> str | None:
    """Распознать ссылку или идентификатор поста ВКонтакте вида wall-XXX_YYY.

    Поддерживает форматы:
    - wall-XXX_YYY
    - vk.com/wall-XXX_YYY
    - m.vk.com/wall-XXX_YYY
    - vk.com/...?...w=wall-XXX_YYY
    - https://vk.com/wall-XXX_YYY
    """
    if not input_str or not isinstance(input_str, str):
        return None
    match = _VK_POST_REF_PATTERN.search(input_str.strip())
    if match:
        return f"wall{match.group(1)}"
    return None


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
    excluded_user_ids: list[int] | None = None,
    attachment: str | None = None,
    forward: str | None = None,
    author_name: str = "superadmin",
    actor_type: str = "web",
) -> BroadcastResult:
    """Поставить рассылку сообщения N последним пользователям в очередь доставки VkOutbox."""
    clean_text = text.strip() if text else ""
    clean_attachment = attachment.strip() if attachment else None
    clean_forward = forward.strip() if forward else None

    if not clean_text and not clean_attachment and not clean_forward:
        return BroadcastResult(
            total_requested=count,
            recipients_count=0,
            recipient_vk_ids=[],
            status="error",
            error="Укажите текст сообщения или прикрепите пост/изображение.",
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

    if excluded_user_ids:
        excluded_set = set(excluded_user_ids)
        users = [u for u in users if u.id not in excluded_set and u.vk_id not in excluded_set]

    if not users:
        return BroadcastResult(
            total_requested=count,
            recipients_count=0,
            recipient_vk_ids=[],
            status="error",
            error="Все выбранные пользователи были исключены из списка получателей.",
        )

    recipient_vk_ids: list[int] = []
    for user in users:
        if user.vk_id:
            add_outbox_message(
                session,
                vk_id=user.vk_id,
                text=clean_text,
                attachment=clean_attachment,
                forward=clean_forward,
            )
            recipient_vk_ids.append(user.vk_id)

    # Журнал аудита действий администратора
    details_parts = [f"Рассылка сообщений ({len(recipient_vk_ids)} получателей)"]
    if clean_text:
        details_parts.append(f"текст: {clean_text[:120]}")
    if clean_attachment:
        details_parts.append(f"вложение: {clean_attachment}")
    if clean_forward:
        details_parts.append(f"пересылка: {clean_forward[:60]}")
    details_str = "; ".join(details_parts)

    log_entry = Log(
        action="broadcast_sent",
        details=details_str,
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
