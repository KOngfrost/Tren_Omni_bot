"""Хендлеры базы знаний бота."""

from sqlalchemy import select
from vkbottle.bot import BotLabeler, Message

from bots.vk.common import _main_keyboard_for
from core.commands import COMMANDS_KNOWLEDGE
from core.database import async_session_maker
from core.models import KnowledgeBase

knowledge_labeler = BotLabeler()


@knowledge_labeler.private_message(text=COMMANDS_KNOWLEDGE)
async def knowledge_base_handler(message: Message):
    async with async_session_maker() as session:
        entries = list(
            await session.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id).limit(20))
        )
    if not entries:
        await message.answer(
            "В базе знаний пока нет опубликованных материалов.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    lines = [
        "Материалы базы знаний:",
        *[f"\n{entry.keywords}: {entry.answer}" for entry in entries],
    ]
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


# Алиас для обратной совместимости
knowledge_handler = knowledge_base_handler
