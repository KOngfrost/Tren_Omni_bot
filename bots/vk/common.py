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

    WAITING_TYPE = "waiting_type"
    WAITING_DATE = "waiting_date"
    WAITING_DATE_FROM = "waiting_date_from"


class TicketStates(BaseStateGroup):
    """FSM StateGroup для создания обращений."""

    WAITING_DEPARTMENT = "waiting_department"
    WAITING_DESCRIPTION = "waiting_description"
    WAITING_IDENTITY_CHOICE = "waiting_identity_choice"


class PartnershipStates(BaseStateGroup):
    """FSM StateGroup для раздела «Партнёрство»."""

    WAITING_PROPOSAL = "waiting_proposal"


class AdminTicketStates(BaseStateGroup):
    """FSM StateGroup для ответов администратора в тикеты."""

    WAITING_REPLY_TEXT = "admin_waiting_reply_text"



def _main_reply_text(departments: list[str] | None = None) -> str:
    lines = [
        "Привет! Я бот-помощник Объединённого студсовета общежитий.\n",
        "Выберите нужное действие в меню ниже:\n",
        "📝 Обращения:",
        "• Создать заявку — вопрос к определенному отделу или общий (без отдела)",
        "• Мои заявки — список ваших заявок и история ответов\n",
        "📌 Разделы информации:",
        "• Частые вопросы — ответы на популярные вопросы по темам",
        "• База знаний — регламенты, памятки по отделам и общие статьи",
        "• Мероприятия — анонсы событий и запись",
        "• Партнёрство — предложения о сотрудничестве\n",
        "💡 При подаче заявки можно выбрать: получить ответ в VK или отправить анонимно.",
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
