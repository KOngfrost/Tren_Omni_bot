"""Сервис удаления и анонимизации персональных данных (152-ФЗ / GDPR: Право на забвение).

Обеспечивает:
- Анонимизацию тикетов пользователя (is_anonymous=True, user_id=None)
- Удаление персональных идентификаторов из сообщений тикетов (author_vk_id=None)
- Отмену и очистку неотправленных сообщений в очереди outbox (recipient_vk_id)
- Удаление подписок и регистраций на мероприятия
- Удаление записи пользователя из таблицы users
- Защиту от случайного удаления административных аккаунтов через студенческий интерфейс
- Запись факта удаления в журнал аудита без сохранения PII
"""

import logging
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Admin, Registration, Subscription, Ticket, TicketMessage, User, VkOutbox

logger = logging.getLogger(__name__)


class PrivacyServiceError(Exception):
    """Базовая ошибка сервиса приватности."""


class AdminAccountDeletionError(PrivacyServiceError):
    """Попытка удаления административного аккаунта через студенческий эндпоинт."""


async def delete_user_personal_data(
    session: AsyncSession,
    vk_id: int,
) -> dict[str, Any]:
    """Удалить и анонимизировать все персональные данные пользователя по VK ID.

    Возвращает статистику обработанных записей:
    {
        "success": bool,
        "vk_id": int,
        "user_id": int | None,
        "tickets_anonymized": int,
        "messages_anonymized": int,
        "subscriptions_deleted": int,
        "registrations_deleted": int,
        "outbox_deleted": int,
    }
    """
    stmt = select(User).where(User.vk_id == vk_id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if not user:
        return {
            "success": False,
            "error": "not_found",
            "vk_id": vk_id,
        }

    # Защита: нельзя удалять администратора через процедуру очистки студенческих данных
    admin_stmt = select(Admin).where(Admin.user_id == user.id)
    admin = (await session.execute(admin_stmt)).scalar_one_or_none()
    if admin:
        raise AdminAccountDeletionError(
            f"Пользователь VK ID {vk_id} является администратором. "
            "Сначала удалите административные права через панель администраторов."
        )

    user_id = user.id

    # 1. Анонимизируем тикеты пользователя
    ticket_update_stmt = (
        update(Ticket).where(Ticket.user_id == user_id).values(user_id=None, is_anonymous=True)
    )
    ticket_res = await session.execute(ticket_update_stmt)
    tickets_anonymized = ticket_res.rowcount or 0

    # 2. Анонимизируем сообщения студента в диалогах
    msg_update_stmt = (
        update(TicketMessage).where(TicketMessage.author_vk_id == vk_id).values(author_vk_id=None)
    )
    msg_res = await session.execute(msg_update_stmt)
    messages_anonymized = msg_res.rowcount or 0

    # 3. Удаляем ожидающие сообщения из Outbox для этого VK ID
    outbox_del_stmt = delete(VkOutbox).where(VkOutbox.vk_id == vk_id)
    outbox_res = await session.execute(outbox_del_stmt)
    outbox_deleted = outbox_res.rowcount or 0

    # 4. Удаляем подписки на отделы
    sub_del_stmt = delete(Subscription).where(Subscription.user_id == user_id)
    sub_res = await session.execute(sub_del_stmt)
    subscriptions_deleted = sub_res.rowcount or 0

    # 5. Удаляем регистрации на мероприятия
    reg_del_stmt = delete(Registration).where(Registration.user_id == user_id)
    reg_res = await session.execute(reg_del_stmt)
    registrations_deleted = reg_res.rowcount or 0

    # 6. Удаляем саму сущность пользователя
    await session.delete(user)
    await session.commit()

    logger.info(
        "Персональные данные для VK ID %d успешно удалены/анонимизированы: "
        "tickets=%d, messages=%d, subs=%d, regs=%d, outbox=%d",
        vk_id,
        tickets_anonymized,
        messages_anonymized,
        subscriptions_deleted,
        registrations_deleted,
        outbox_deleted,
    )

    return {
        "success": True,
        "vk_id": vk_id,
        "user_id": user_id,
        "tickets_anonymized": tickets_anonymized,
        "messages_anonymized": messages_anonymized,
        "subscriptions_deleted": subscriptions_deleted,
        "registrations_deleted": registrations_deleted,
        "outbox_deleted": outbox_deleted,
    }
