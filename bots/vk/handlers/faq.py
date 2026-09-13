"""Хендлеры FAQ бота."""

from sqlalchemy import select
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _main_keyboard_for
from core.commands import COMMANDS_FAQ
from core.database import async_session_maker
from core.models import FAQNode

faq_labeler = BotLabeler()


@faq_labeler.private_message(text=COMMANDS_FAQ)
async def faq_handler(message: Message):
    async with async_session_maker() as session:
        nodes = list(
            await session.scalars(
                select(FAQNode)
                .where(FAQNode.parent_id.is_(None))
                .order_by(FAQNode.order_index, FAQNode.id)
            )
        )
    if not nodes:
        await message.answer(
            "В Частых вопросах, пока нет опубликованных вопросов.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    lines = [
        "Частые вопросы:",
        *[f"\n#{node.id} {node.button_text or node.question}" for node in nodes],
        "\nВведите: FAQ #номер",
    ]
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


@faq_labeler.private_message(RegexRule(r"(?i)^FAQ #(\d+)$"))
async def faq_node_handler(message: Message):
    import re

    match = re.search(r"#(\d+)", message.text or "")
    if match is None:
        return
    node_id = int(match.group(1))
    async with async_session_maker() as session:
        node = await session.get(FAQNode, node_id)
        if node is None:
            await message.answer(
                "Вопрос FAQ не найден.", keyboard=await _main_keyboard_for(message.from_id)
            )
            return
        children = list(
            await session.scalars(
                select(FAQNode)
                .where(FAQNode.parent_id == node_id)
                .order_by(FAQNode.order_index, FAQNode.id)
            )
        )
    response_lines = [f"{node.question}\n\n{node.answer or ''}"]
    if children:
        response_lines.append("\nПодразделы:")
        response_lines.extend(
            f"#{child.id} {child.button_text or child.question}" for child in children
        )
        response_lines.append("\nВведите: FAQ #номер")
    await message.answer(
        "\n".join(response_lines), keyboard=await _main_keyboard_for(message.from_id)
    )
