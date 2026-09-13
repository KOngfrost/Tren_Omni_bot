"""Хендлеры базы знаний бота."""

import re

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _get_department_names
from bots.vk.keyboards import (
    build_back_nav_keyboard,
    build_knowledge_departments_keyboard,
)
from core.commands import COMMANDS_KNOWLEDGE
from core.database import async_session_maker
from core.models import Department, KnowledgeBase

knowledge_labeler = BotLabeler()


@knowledge_labeler.private_message(text=COMMANDS_KNOWLEDGE)
@knowledge_labeler.private_message(text=("К разделам Базы", "к разделам базы"))
async def knowledge_base_handler(message: Message):
    """Главный вход в Базу знаний с понятной инструкцией и выбором отдела."""
    dept_names = await _get_department_names()
    instruction_text = (
        "ℹ️ Инструкция по Базе знаний:\n\n"
        "В Базе знаний собраны официальные материалы, регламенты, памятки, "
        "ссылки на полезные ресурсы и сообщества Студсовета.\n\n"
        "📖 Как пользоваться:\n"
        "1. Нажмите на нужный отдел на кнопках ниже или выберите «База: Все материалы».\n"
        "2. Бот покажет оформленные карточки с описанием и ссылками.\n"
        "3. Для быстрого поиска по теме отправьте: «База [слово]» "
        "(например: «База сообщество» или «База сантехника»)."
    )
    keyboard = build_knowledge_departments_keyboard(dept_names)
    await message.answer(instruction_text, keyboard=keyboard)


@knowledge_labeler.private_message(RegexRule(r"(?i)^База:\s*(.+)$"))
async def knowledge_department_handler(message: Message):
    """Отображение материалов выбранного отдела с красивым форматированием."""
    match = re.match(r"(?i)^База:\s*(.+)$", message.text or "")
    if not match:
        return
    dept_raw = match.group(1).strip()

    async with async_session_maker() as session:
        stmt = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.department))
            .order_by(KnowledgeBase.department_id, KnowledgeBase.id)
        )
        if "все материалы" not in dept_raw.lower():
            dept = await session.scalar(
                select(Department).where(Department.name.ilike(f"%{dept_raw}%"))
            )
            if dept:
                stmt = stmt.where(KnowledgeBase.department_id == dept.id)

        entries = list(await session.scalars(stmt))

    if not entries:
        await message.answer(
            f"В разделе «{dept_raw}» пока нет опубликованных материалов.\n"
            "Вы можете задать вопрос оператору через кнопку «Задать вопрос» в меню.",
            keyboard=build_back_nav_keyboard("К разделам Базы"),
        )
        return

    cards = []
    for entry in entries:
        dept_name = entry.department.name if entry.department else "Общий"
        card = (
            f"📌 Тема / Теги: {entry.keywords}\n🏢 Отдел: {dept_name}\nℹ️ Материал:\n{entry.answer}"
        )
        cards.append(card)

    divider = "\n\n" + "─" * 28 + "\n\n"
    response_text = f"📚 База знаний — {dept_raw}:\n\n" + divider.join(cards)

    await message.answer(
        response_text,
        keyboard=build_back_nav_keyboard("К разделам Базы"),
    )


@knowledge_labeler.private_message(RegexRule(r"(?i)^База\s+(.+)$"))
async def knowledge_search_handler(message: Message):
    """Поиск по базе знаний по ключевым словам."""
    match = re.match(r"(?i)^База\s+(.+)$", message.text or "")
    if not match:
        return
    query = match.group(1).strip()
    if not query or query.startswith(":"):
        return

    async with async_session_maker() as session:
        entries = list(
            await session.scalars(
                select(KnowledgeBase)
                .options(selectinload(KnowledgeBase.department))
                .where(
                    KnowledgeBase.keywords.ilike(f"%{query}%")
                    | KnowledgeBase.answer.ilike(f"%{query}%")
                )
                .order_by(KnowledgeBase.id)
                .limit(5)
            )
        )

    if not entries:
        await message.answer(
            f"По запросу «{query}» в базе знаний ничего не найдено.\n"
            "Попробуйте другое слово или обратитесь через «Задать вопрос» в меню.",
            keyboard=build_back_nav_keyboard("К разделам Базы"),
        )
        return

    cards = []
    for entry in entries:
        dept_name = entry.department.name if entry.department else "Общий"
        card = f"📌 Тема: {entry.keywords}\n🏢 Отдел: {dept_name}\nℹ️ Материал:\n{entry.answer}"
        cards.append(card)

    divider = "\n\n" + "─" * 28 + "\n\n"
    response_text = f"🔍 Найдено в базе знаний по запросу «{query}»:\n\n" + divider.join(cards)

    await message.answer(
        response_text,
        keyboard=build_back_nav_keyboard("К разделам Базы"),
    )


# Алиас для обратной совместимости
knowledge_handler = knowledge_base_handler
