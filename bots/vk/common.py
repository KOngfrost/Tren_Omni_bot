"""Общие FSM-состояния, клавиатуры и вспомогательные функции VK-бота."""

import logging

from sqlalchemy import select
from vkbottle import BaseStateGroup

from bots.vk.keyboards import build_main_keyboard
from core.bot_core import BotCore, get_admin_scope_for_vk_id
from core.database import async_session_maker
from core.models import Department, Ticket

logger = logging.getLogger(__name__)

_REPORT_PERIOD_PATTERN = r"^\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})\s*$"


class ReportStates(BaseStateGroup):
    """FSM StateGroup для отчётов."""

    WAITING_DATE = "waiting_date"
    WAITING_DATE_FROM = "waiting_date_from"


class TicketStates(BaseStateGroup):
    """FSM StateGroup для создания обращений."""

    WAITING_DESCRIPTION = "waiting_description"
    WAITING_IDENTITY_CHOICE = "waiting_identity_choice"


def _main_reply_text(departments: list[str] | None = None) -> str:
    lines = [
        "Привет! Я бот-помощник Объединённого студсовета общежитий.\n",
        "Выберите нужный раздел в меню ниже:\n",
        "📋 Отделы для подачи заявок:",
        "• Жил-быт (Жилищно-бытовой)",
        "• Культмасс (Культурно-массовый)",
        "• Информ (Информационный)",
        "• Корпоративный (Корп)\n",
        "📌 Дополнительные разделы:",
        "• Задать вопрос — общий вопрос без привязки к отделу",
        "• Часто задаваемы вопросы — ответы на часто задаваемые вопросы",
        "• Мероприятия — актуальные события и запись на них",
        "• База знаний — полезные статьи и инструкции",
        "• Мои заявки — список ваших обращений и их статусы\n",
        "💡 При создании обращения можно выбрать: получить ответ в VK или отправить анонимно.",
        "🔒 Отправляя обращение, вы даёте согласие на обработку персональных данных.",
    ]
    return "\n".join(lines)


async def _get_department_names() -> list[str]:
    """Получить список названий отделов из БД."""
    async with async_session_maker() as session:
        departments = list(await session.scalars(select(Department).order_by(Department.name)))
        return [dept.name for dept in departments if dept.name]


async def _main_keyboard_for(vk_id: int) -> str:
    """Сформировать главную клавиатуру с учетом прав пользователя."""
    user = await BotCore.get_or_create_user(vk_id=vk_id)
    departments = await _get_department_names()
    return build_main_keyboard(await BotCore.is_admin(user), departments)


async def _operator_can_access(vk_id: int, ticket_id: int) -> bool:
    """Проверить, имеет ли оператор доступ к указанной заявке."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, vk_id)
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            return False
        if is_super:
            return True
        if dept_id is None:
            return False
        return ticket.department_id == dept_id
