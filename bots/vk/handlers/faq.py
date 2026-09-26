"""Хендлеры раздела «Частые вопросы» бота."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _get_department_names
from bots.vk.handlers.pagination import (
    FETCH_LIMIT,
    KIND_FAQ_DEPARTMENTS,
    KIND_FAQ_SEARCH,
    PAGE_SIZE,
    clamp_page,
    open_list,
    page_count,
    persist_page,
    register_renderer,
)
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
@faq_labeler.private_message(text=("К разделам вопросов", "к разделам вопросов", "Темы вопросов"))
async def faq_handler(message: Message):
    """Главный вход в «Частые вопросы»: разбитый список по темам и короткое сообщение."""
    dept_names = await _get_department_names()
    lines = [
        "❓ Инструкция по разделу «Частые вопросы»:\n",
        "📖 Как пользоваться:",
        "Выберите интересующую вас тему из списка ниже (нажмите кнопку или напишите её название/номер):\n",
        "Список тем:",
    ]
    for idx, dname in enumerate(dept_names, start=1):
        lines.append(f"{idx}. {dname}")
    lines.append(f"{len(dept_names) + 1}. Все отделы")
    lines.append(
        "\nЧтобы выбрать тему, напишите её название или номер (или нажмите кнопку ниже):"
    )
    keyboard = build_faq_departments_keyboard(dept_names)
    await message.answer("\n".join(lines), keyboard=keyboard)


async def _render_faq_department_page(message: Message, page: int, meta: dict[str, Any]) -> None:
    """Отрисовать страницу списка вопросов отдела (пагинация, ЭТАП 4.1 / B4)."""
    dept_raw = str(meta.get("dept") or "Все отделы")

    async with async_session_maker() as session:
        stmt = (
            select(FAQNode)
            .options(selectinload(FAQNode.department))
            .where(FAQNode.parent_id.is_(None))
            .order_by(FAQNode.department_id, FAQNode.order_index, FAQNode.id)
        )
        if "все отделы" not in dept_raw.lower():
            dept = await session.scalar(
                select(Department).where(Department.name.ilike(f"%{dept_raw}%"))
            )
            if dept:
                stmt = stmt.where(FAQNode.department_id == dept.id)
            else:
                await message.answer(
                    f"Раздел «{dept_raw}» не найден.\nВыберите раздел из списка:",
                    keyboard=build_back_nav_keyboard("К разделам вопросов"),
                )
                return

        nodes = list(await session.scalars(stmt.limit(FETCH_LIMIT)))

    page = clamp_page(page, len(nodes))
    total_pages = page_count(len(nodes))
    start = page * PAGE_SIZE
    page_nodes = nodes[start : start + PAGE_SIZE]
    item_ids = [n.id for n in page_nodes]

    meta["item_ids"] = item_ids
    await persist_page(message.from_id, KIND_FAQ_DEPARTMENTS, page, meta)

    if not nodes:
        await message.answer(
            f"В разделе «{dept_raw}» пока нет опубликованных вопросов.\n"
            "Вы можете задать свой вопрос напрямую операторам через кнопку «Создать заявку» в меню.",
            keyboard=build_back_nav_keyboard("К разделам вопросов"),
        )
        return

    lines = [
        f"❓ Вопросы по теме «{dept_raw}»:\n",
    ]
    for idx, node in enumerate(page_nodes, start=start + 1):
        dept_prefix = f"[{node.department.name}] " if "все отделы" in dept_raw.lower() else ""
        lines.append(f"{idx}. {dept_prefix}{node.button_text or node.question}")

    if total_pages > 1:
        lines.append(f"\n📄 Страница {page + 1} из {total_pages}")

    lines.append(
        "\n💡 Чтобы прочитать ответ, напишите номер вопроса (например: 1) или нажмите кнопку с его номером:"
    )

    keyboard = build_faq_items_keyboard(
        item_ids, page=page, has_more=page + 1 < total_pages
    )
    await message.answer("\n".join(lines), keyboard=keyboard)


@faq_labeler.private_message(RegexRule(r"(?i)^(?:Вопросы:\s*|Тема:\s*)(.+)$"))
async def faq_department_handler(message: Message):
    """Отображение списка вопросов выбранного отдела."""
    match = re.match(r"(?i)^(?:Вопросы:\s*|Тема:\s*)(.+)$", message.text or "")
    if not match:
        return
    dept_raw = match.group(1).strip()
    await open_list(message, KIND_FAQ_DEPARTMENTS, {"dept": dept_raw})


@faq_labeler.private_message(RegexRule(r"(?i)^(?:Вопрос\s*#?|FAQ\s*#?|#)?(\d+)$"))
async def faq_node_handler(message: Message):
    """Отображение конкретного ответа на вопрос по номеру или ID."""
    match = re.search(r"(\d+)", message.text or "")
    if match is None:
        return
    parsed_num = int(match.group(1))

    # Сначала проверяем, находится ли пользователь на странице вопросов
    from bots.vk.handlers.pagination import get_page_state
    state = await get_page_state(message.from_id)
    node_id = parsed_num
    if state and state.get("kind") in (KIND_FAQ_DEPARTMENTS, KIND_FAQ_SEARCH):
        item_ids = state.get("meta", {}).get("item_ids", [])
        if 1 <= parsed_num <= len(item_ids):
            node_id = item_ids[parsed_num - 1]

    async with async_session_maker() as session:
        node = await session.scalar(
            select(FAQNode).options(selectinload(FAQNode.department)).where(FAQNode.id == node_id)
        )
        if node is None:
            # Если не найден и пользователь мог иметь в виду номер заявки,
            # не блокируем другие обработчики
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


async def _render_faq_search_page(message: Message, page: int, meta: dict[str, Any]) -> None:
    """Отрисовать страницу результатов поиска по FAQ (пагинация, ЭТАП 4.1 / B4)."""
    query = str(meta.get("query") or "").strip()
    if not query:
        # Без поискового запроса список не восстановить — возвращаем в разделы
        await message.answer(
            "Поисковый запрос потерян. Выберите раздел вопросов:",
            keyboard=build_back_nav_keyboard("К разделам вопросов"),
        )
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
                .limit(FETCH_LIMIT)
            )
        )

    page = clamp_page(page, len(nodes))
    await persist_page(message.from_id, KIND_FAQ_SEARCH, page, meta)

    if not nodes:
        await message.answer(
            f"По запросу «{query}» в частых вопросах ничего не найдено.\n"
            "Попробуйте сформулировать иначе или задайте вопрос через кнопку «Задать вопрос» в меню.",
            keyboard=build_back_nav_keyboard("К разделам вопросов"),
        )
        return

    total_pages = page_count(len(nodes))
    start = page * PAGE_SIZE
    page_nodes = nodes[start : start + PAGE_SIZE]

    lines = [f"🔍 Результаты поиска по запросу «{query}»:\n"]
    item_ids = []
    for idx, node in enumerate(page_nodes, start=start + 1):
        item_ids.append(node.id)
        lines.append(f"{idx}. {node.question} [Вопрос {node.id}]")

    if total_pages > 1:
        lines.append(f"\n📄 Страница {page + 1} из {total_pages}")

    lines.append("\n💡 Выберите номер вопроса на клавиатуре или отправьте номер:")
    keyboard = build_faq_items_keyboard(
        item_ids, page=page, has_more=page + 1 < total_pages
    )
    await message.answer("\n".join(lines), keyboard=keyboard)


@faq_labeler.private_message(RegexRule(r"(?i)^Поиск\s+(.+)$"))
async def faq_search_handler(message: Message):
    """Поиск по частым вопросам по ключевым словам."""
    match = re.match(r"(?i)^Поиск\s+(.+)$", message.text or "")
    if not match:
        return
    query = match.group(1).strip()
    if not query or query.isdigit():
        return
    await open_list(message, KIND_FAQ_SEARCH, {"query": query})


# Регистрация рендереров пагинации FAQ (ЭТАП 4.1 / B4)
register_renderer(KIND_FAQ_DEPARTMENTS, _render_faq_department_page)
register_renderer(KIND_FAQ_SEARCH, _render_faq_search_page)
