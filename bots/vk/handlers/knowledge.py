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
    """Главный вход в Базу знаний с понятной инструкцией, отделами и темами."""
    dept_names = await _get_department_names()
    topics = []
    async with async_session_maker() as session:
        kb_topics = list(await session.scalars(select(KnowledgeBase.keywords).distinct().limit(6)))
        for k in kb_topics:
            if k:
                first_keyword = k.split(",")[0].strip()
                if first_keyword and first_keyword not in topics:
                    topics.append(first_keyword)

    instruction_text = (
        "📚 Инструкция по Базе знаний Студсовета:\n\n"
        "Здесь собраны официальные материалы, регламенты, памятки и контакты "
        "как по конкретным отделам, так и общие материалы без привязки к отделу.\n\n"
        "📖 Как пользоваться:\n"
        "1. Выберите отдел или нажмите «База: Без отдела» для общих материалов.\n"
        "2. Выберите интересующую тему на кнопках.\n"
        "3. Или найдите материал поиском: отправьте «База [слово]» "
        "(например: «База общежитие» или «База контакты»)."
    )
    keyboard = build_knowledge_departments_keyboard(dept_names, topics=topics[:4])
    await message.answer(instruction_text, keyboard=keyboard)


@knowledge_labeler.private_message(RegexRule(r"(?i)^(?:База:\s*|Тема:\s*)(.+)$"))
async def knowledge_department_handler(message: Message):
    """Отображение материалов выбранного отдела, общих материалов или темы."""
    match = re.match(r"(?i)^(?:База:\s*|Тема:\s*)(.+)$", message.text or "")
    if not match:
        return
    query_raw = match.group(1).strip()
    is_topic = (message.text or "").strip().lower().startswith("тема:")

    async with async_session_maker() as session:
        stmt = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.department))
            .order_by(KnowledgeBase.department_id, KnowledgeBase.id)
        )
        if is_topic:
            stmt = stmt.where(
                KnowledgeBase.keywords.ilike(f"%{query_raw}%")
                | KnowledgeBase.answer.ilike(f"%{query_raw}%")
            )
            title = f"Тема: {query_raw}"
        elif query_raw.lower() in ("без отдела", "общие", "общие материалы"):
            stmt = stmt.where(KnowledgeBase.department_id.is_(None))
            title = "Общие материалы (без отдела)"
        elif "все материалы" in query_raw.lower():
            title = "Все материалы"
        else:
            dept = await session.scalar(
                select(Department).where(Department.name.ilike(f"%{query_raw}%"))
            )
            if dept:
                stmt = stmt.where(KnowledgeBase.department_id == dept.id)
                title = f"Отдел: {dept.name}"
            else:
                await message.answer(
                    f"Раздел «{query_raw}» не найден.\nВыберите раздел из списка:",
                    keyboard=build_back_nav_keyboard("К разделам Базы"),
                )
                return

        entries = list(await session.scalars(stmt.limit(10)))

    if not entries:
        await message.answer(
            f"В разделе «{query_raw}» пока нет опубликованных материалов.\n"
            "Вы можете задать вопрос оператору через кнопку «Создать заявку» в меню.",
            keyboard=build_back_nav_keyboard("К разделам Базы"),
        )
        return

    cards = []
    for entry in entries:
        dept_name = entry.department.name if entry.department else "Без отдела (Общий)"
        ans = entry.answer or ""
        if len(ans) > 400:
            ans = ans[:400] + "..."
        card = (
            f"📌 Тема / Теги: {entry.keywords}\n🏢 Отдел: {dept_name}\nℹ️ Материал:\n{ans}"
        )
        cards.append(card)

    divider = "\n\n" + "─" * 28 + "\n\n"
    response_text = f"📚 База знаний — {title}:\n\n" + divider.join(cards)
    if len(response_text) > 4000:
        response_text = response_text[:3990] + "\n\n[...]"

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
        ans = entry.answer or ""
        if len(ans) > 400:
            ans = ans[:400] + "..."
        card = f"📌 Тема: {entry.keywords}\n🏢 Отдел: {dept_name}\nℹ️ Материал:\n{ans}"
        cards.append(card)

    divider = "\n\n" + "─" * 28 + "\n\n"
    response_text = f"🔍 Найдено в базе знаний по запросу «{query}»:\n\n" + divider.join(cards)
    if len(response_text) > 4000:
        response_text = response_text[:3990] + "\n\n[...]"

    await message.answer(
        response_text,
        keyboard=build_back_nav_keyboard("К разделам Базы"),
    )


# Алиас для обратной совместимости
knowledge_handler = knowledge_base_handler
