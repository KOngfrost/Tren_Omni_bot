"""Хендлеры мероприятий и записи на события."""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _main_keyboard_for
from bots.vk.keyboards import build_events_keyboard
from core.bot_core import BotCore
from core.commands import COMMAND_REGISTER_EVENT_PATTERN, COMMANDS_EVENTS
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import Event, Registration

logger = logging.getLogger(__name__)
events_labeler = BotLabeler()


@events_labeler.private_message(text=COMMANDS_EVENTS)
async def events_handler(message: Message):
    touch_heartbeat()
    logger.info("Запрос афиши мероприятий от vk_id=%s", message.from_id)
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.event_date >= now - timedelta(hours=3))
                .order_by(Event.event_date.asc())
                .limit(10)
            )
        )
    if not events:
        await message.answer(
            "Ближайших мероприятий нет.\n\n"
            "Следите за анонсами в сообществе или загляните в этот раздел позже.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    lines = ["Ближайшие мероприятия:"]
    event_ids = []
    for event in events:
        event_ids.append(event.id)
        date = (
            event.event_date.strftime("%d.%m.%Y %H:%M") if event.event_date else "дата уточняется"
        )
        lines.append(f"\n\n#{event.id} {event.title} ({date})")
        if event.description:
            lines.append(f"\n{event.description}")
        lines.append(f"\nЗапись: Записаться #{event.id}")

    lines.append("\n\nДля быстрой записи нажмите кнопку с номером события ниже:")
    keyboard = build_events_keyboard(event_ids)
    await message.answer("".join(lines), keyboard=keyboard)


@events_labeler.private_message(RegexRule(COMMAND_REGISTER_EVENT_PATTERN))
async def register_event_handler(message: Message):
    touch_heartbeat()
    match = re.search(r"(\d+)", message.text or "")
    if match is None:
        return
    event_id = int(match.group(1))
    logger.info("Запрос регистрации на событие #%d от vk_id=%s", event_id, message.from_id)
    user = await BotCore.get_or_create_user(message.from_id)
    async with async_session_maker() as session:
        event = await session.get(Event, event_id)
        if event is None:
            await message.answer(
                f"Мероприятие #{event_id} не найдено или было удалено.",
                keyboard=await _main_keyboard_for(message.from_id),
            )
            return
        now = datetime.now(UTC)
        event_dt = (
            event.event_date if event.event_date.tzinfo else event.event_date.replace(tzinfo=UTC)
        )
        if event_dt < now:
            await message.answer(
                "Нельзя записаться на прошедшее мероприятие.",
                keyboard=await _main_keyboard_for(message.from_id),
            )
            return
        session.add(Registration(user_id=user.id, event_id=event_id))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await message.answer(
                f"Вы уже зарегистрированы на «{event.title}».\n\nЖдём вас на мероприятии!",
                keyboard=await _main_keyboard_for(message.from_id),
            )
            return

    date_str = (
        f" ({event.event_date.strftime('%d.%m.%Y %H:%M')})" if event.event_date else ""
    )
    await message.answer(
        f"Вы зарегистрированы на «{event.title}»{date_str}!\n\nЖдём вас на мероприятии.",
        keyboard=await _main_keyboard_for(message.from_id),
    )

