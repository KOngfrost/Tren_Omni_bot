"""Хендлеры мероприятий и записи на события."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _main_keyboard_for
from core.bot_core import BotCore
from core.commands import COMMAND_REGISTER_EVENT_PATTERN, COMMANDS_EVENTS
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import Event, Registration

events_labeler = BotLabeler()


@events_labeler.private_message(text=COMMANDS_EVENTS)
async def events_handler(message: Message):
    touch_heartbeat()
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.event_date >= now - timedelta(hours=3))
                .order_by(Event.event_date.asc())
                .limit(20)
            )
        )
    if not events:
        await message.answer(
            "Ближайших мероприятий нет.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return
    lines = ["Ближайшие мероприятия:"]
    for event in events:
        date = (
            event.event_date.strftime("%d.%m.%Y %H:%M") if event.event_date else "дата уточняется"
        )
        lines.append(f"\n\n#{event.id} {event.title} ({date})")
        if event.description:
            lines.append(f"\n{event.description}")
        lines.append(f"\nЗапись: Записаться #{event.id}")
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


@events_labeler.private_message(RegexRule(COMMAND_REGISTER_EVENT_PATTERN))
async def register_event_handler(message: Message):
    touch_heartbeat()
    match = re.search(r"#(\d+)", message.text or "")
    if match is None:
        return
    event_id = int(match.group(1))
    user = await BotCore.get_or_create_user(message.from_id)
    async with async_session_maker() as session:
        event = await session.get(Event, event_id)
        if event is None:
            await message.answer(
                "Мероприятие не найдено.", keyboard=await _main_keyboard_for(message.from_id)
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
                "Вы уже зарегистрированы на это мероприятие.",
                keyboard=await _main_keyboard_for(message.from_id),
            )
            return

    await message.answer(
        f"Вы зарегистрированы на «{event.title}».",
        keyboard=await _main_keyboard_for(message.from_id),
    )
