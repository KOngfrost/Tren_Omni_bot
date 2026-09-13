"""Хендлеры раздела «Частые вопросы» бота."""

import re

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _get_department_names
from bots.vk.keyboards import (
    build_back_nav_keyboard,
    build_faq_departments_keyboard,
    build_faq_items_keyboard,
)
from core.commands import COMMANDS_FAQ
from core.database import async_session_maker
from core.models import Department, FAQNode

faq_labeler = BotLabeler()


@faq_labeler.private_message(text=COMMANDS_FAQ)
@faq_labeler.private_message(text=("К разделам вопросов", "к разделам вопросов"))
async def faq_handler(message: Message):
    """Главный вход в «Частые вопросы» с инструкцией по использованию и выбором отдела."""
    dept_names = await _get_department_names()
    instruction_text = (
        "ℹ️ Инструкция по разделу «Частые вопросы»:\n\n"
        "Здесь собраны проверенные ответы на самые частые вопросы студентов по общежитиям, "
        "стипендиям, мероприятиям и сервисам университета.\n\n"
        "📖 Как пользоваться:\n"
        "1. Нажмите на нужный отдел на кнопках ниже или выберите «Вопросы: Все отделы».\n"
        "2. Нажмите кнопку с номером интересующего вопроса, чтобы прочитать подробный ответ.\n"
        "3. Для быстрого поиска по ключевому слову отправьте: «Поиск [слово]» "
        "(например: «Поиск общежитие» или «Поиск пропуск»)."
    )
    keyboard = build_faq_departments_keyboard(dept_names)
    await message.answer(instruction_text, keyboard=keyboard)


@faq_labeler.private_message(RegexRule(r"(?i)^Вопросы:\s*(.+)$"))
async def faq_department_handler(message: Message):
    """Отображение списка вопросов выбранного отдела."""
    match = re.match(r"(?i)^Вопросы:\s*(.+)$", message.text or "")
    if not match:
        return
    dept_raw = match.group(1).strip()

    async with async_session_maker() as session:
        stmt = (
            select(FAQNode)
            .options(selectinload(FAQNode.department))
            .where(FAQNode.parent_id.is_(None))
            .order_by(FAQNode.department_id, FAQNode.order_index, FAQNode.id)
        )
        if "все отделы" not in dept_raw.lower():
            # Поиск отдела по подстроке названия
            dept = await session.scalar(
                select(Department).where(Department.name.ilike(f"%{dept_raw}%"))
            )
            if dept:
                stmt = stmt.where(FAQNode.department_id == dept.id)

        nodes = list(await session.scalars(stmt))

    if not nodes:
        await message.answer(
            f"В разделе «{dept_raw}» пока нет опубликованных вопросов.\n"
            "Вы можете задать свой вопрос напрямую операторам через кнопку «Задать вопрос» в меню.",
            keyboard=build_back_nav_keyboard("К разделам вопросов"),
        )
        return

    lines = [
        f"❓ Частые вопросы — {dept_raw}:\n",
    ]
    item_ids = []
    for idx, node in enumerate(nodes, start=1):
        item_ids.append(node.id)
        dept_prefix = f"[{node.department.name}] " if "все отделы" in dept_raw.lower() else ""
        lines.append(f"{idx}. {dept_prefix}{node.button_text or node.question} [Вопрос {node.id}]")

    lines.append(
        "\n💡 Нажмите кнопку с номером вопроса на клавиатуре или отправьте его номер (например: 1 или Вопрос 1):"
    )

    keyboard = build_faq_items_keyboard(item_ids)
    await message.answer("\n".join(lines), keyboard=keyboard)


@faq_labeler.private_message(RegexRule(r"(?i)^(?:Вопрос\s*#?|FAQ\s*#?|#)?(\d+)$"))
async def faq_node_handler(message: Message):
    """Отображение конкретного ответа на вопрос по ID."""
    match = re.search(r"(\d+)", message.text or "")
    if match is None:
        return
    node_id = int(match.group(1))

    async with async_session_maker() as session:
        node = await session.scalar(
            select(FAQNode).options(selectinload(FAQNode.department)).where(FAQNode.id == node_id)
        )
        if node is None:
            await message.answer(
                "Вопрос с указанным номером не найден. Выберите раздел вопросов в меню:",
                keyboard=build_back_nav_keyboard("К разделам вопросов"),
            )
            return

        children = list(
            await session.scalars(
                select(FAQNode)
                .where(FAQNode.parent_id == node_id)
                .order_by(FAQNode.order_index, FAQNode.id)
            )
        )

    dept_name = node.department.name if node.department else "Общий раздел"
    answer_text = node.final_answer or "Ответ на данный вопрос готовится специалистами отдела."

    response_lines = [
        f"❓ Вопрос: {node.question}",
        f"🏢 Отдел: {dept_name}\n",
        "💡 Ответ:",
        answer_text,
    ]

    if children:
        response_lines.append("\n📁 Уточняющие подразделы:")
        response_lines.extend(
            f"• {child.button_text or child.question} [Вопрос {child.id}]" for child in children
        )

    keyboard = build_back_nav_keyboard("К разделам вопросов")
    await message.answer("\n".join(response_lines), keyboard=keyboard)


@faq_labeler.private_message(RegexRule(r"(?i)^Поиск\s+(.+)$"))
async def faq_search_handler(message: Message):
    """Поиск по частым вопросам по ключевым словам."""
    match = re.match(r"(?i)^Поиск\s+(.+)$", message.text or "")
    if not match:
        return
    query = match.group(1).strip()
    if not query or query.isdigit():
        return

    async with async_session_maker() as session:
        nodes = list(
            await session.scalars(
                select(FAQNode)
                .options(selectinload(FAQNode.department))
                .where(
                    FAQNode.question.ilike(f"%{query}%") | FAQNode.final_answer.ilike(f"%{query}%")
                )
                .order_by(FAQNode.id)
                .limit(8)
            )
        )

    if not nodes:
        await message.answer(
            f"По запросу «{query}» в частых вопросах ничего не найдено.\n"
            "Попробуйте сформулировать иначе или задайте вопрос через кнопку «Задать вопрос» в меню.",
            keyboard=build_back_nav_keyboard("К разделам вопросов"),
        )
        return

    lines = [f"🔍 Результаты поиска по запросу «{query}»:\n"]
    item_ids = []
    for idx, node in enumerate(nodes, start=1):
        item_ids.append(node.id)
        lines.append(f"{idx}. {node.question} [Вопрос {node.id}]")

    lines.append("\n💡 Выберите номер вопроса на клавиатуре или отправьте номер:")
    keyboard = build_faq_items_keyboard(item_ids)
    await message.answer("\n".join(lines), keyboard=keyboard)
