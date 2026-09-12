from datetime import UTC, datetime, timedelta

import json
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from core.vk_compat import patch_vkbottle_logging

patch_vkbottle_logging()

from vkbottle import BaseStateGroup, Bot, ErrorHandler
from vkbottle.bot import Message
from vkbottle.dispatch.rules.base import RegexRule
from vkbottle.exception_factory.base_exceptions import VKAPIError
from core.commands import (
    ADMIN_STATUS_PATTERN,
    COMMANDS_ADMIN,
    COMMANDS_ADMIN_TICKETS,
    COMMANDS_ANONYMOUS,
    COMMANDS_ANONYMOUS_STAY,
    COMMAND_REVEAL_IDENTITY,
    COMMANDS_CANCEL,
    COMMANDS_CORPORATE,
    COMMANDS_CULTURE,
    COMMANDS_HOUSING,
    COMMANDS_INFORMATION,
    COMMANDS_FAQ,
    COMMANDS_KNOWLEDGE,
    COMMANDS_EVENTS,
    COMMAND_REGISTER_EVENT_PATTERN,
    COMMANDS_MY_TICKETS,
    COMMAND_TICKET_DETAILS_PATTERN,
    STUDENT_REPLY_PATTERN,
    COMMANDS_QUESTION,
    COMMANDS_REGULAR_MENU,
    COMMANDS_REPORT,
    COMMANDS_REPORT_BY_DATE,
    COMMANDS_REPORT_BY_PERIOD,
    COMMANDS_START,
)
from core.config import settings
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import Event, FAQNode, KnowledgeBase, Registration, Ticket, TicketStatus
from core.reporting import (
    build_daily_report,
    get_app_tz,
    get_report_for_date,
    get_report_for_period,
    parse_report_date,
    send_report_to_vk,
)
from core.ticket_service import (
    STATUS_LABELS,
    StatusTransitionError,
    change_ticket_status,
    create_ticket,
    find_knowledge_entry,
    format_ticket_details,
    format_ticket_list,
    get_ticket_messages,
    add_student_reply,
    get_user_tickets,
    reply_to_ticket,
    status_label,
)
from core.bot_core import BotCore, get_admin_scope_for_vk_id
from bots.vk.keyboards import (
    build_admin_keyboard,
    build_cancel_keyboard,
    build_main_keyboard,
    build_tickets_keyboard,
)

vk_bot = Bot(token=settings.VK_BOT_TOKEN)

logger = logging.getLogger(__name__)

# Глобальный перехватчик ошибок: ни одна ошибка не должна уйти
# пользователю в виде traceback. ErrorHandler подключается ко всем
# view роутера (API vkbottle 4.11: см. exception_factory.error_handler).
_error_handler = ErrorHandler(redirect_arguments=True)


@_error_handler.register_undefined_error_handler
async def _handle_bot_error(error: Exception, message: Message | None = None):
    logger.exception("Необработанная ошибка в боте", exc_info=error)
    # Пользователю — безопасное сообщение с просьбой сфотографировать и отправить техадмину
    if message is not None:
        try:
            await message.answer(
                "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору."
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке пользователю")
    return None


for _view in vk_bot.on.views().values():
    _view.error_handler = _error_handler


# FSM StateGroup для отчётов
class ReportStates(BaseStateGroup):
    WAITING_DATE = "waiting_date"
    WAITING_DATE_FROM = "waiting_date_from"


# FSM StateGroup для создания обращений (разделы меню, «Задать вопрос», анонимные)
class TicketStates(BaseStateGroup):
    WAITING_DESCRIPTION = "waiting_description"
    WAITING_IDENTITY_CHOICE = "waiting_identity_choice"


@vk_bot.on.private_message(text=COMMANDS_FAQ)
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
            "В FAQ пока нет опубликованных вопросов.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    lines = [
        "Частые вопросы:",
        *[f"\n#{node.id} {node.button_text or node.question}" for node in nodes],
        "\nВведите: FAQ #номер",
    ]
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


@vk_bot.on.private_message(text=COMMANDS_KNOWLEDGE)
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


@vk_bot.on.private_message(RegexRule(r"(?i)^FAQ #(\d+)$"))
async def faq_answer_handler(message: Message):
    match = re.search(r"#(\d+)", message.text or "")
    if match is None:
        return
    node_id = int(match.group(1))
    async with async_session_maker() as session:
        node = await session.get(FAQNode, node_id)
        children = list(
            await session.scalars(
                select(FAQNode)
                .where(FAQNode.parent_id == node_id)
                .order_by(FAQNode.order_index, FAQNode.id)
            )
        )
    if node is None:
        await message.answer(
            "Вопрос не найден.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return
    if node.is_final and node.final_answer:
        await message.answer(node.final_answer, keyboard=await _main_keyboard_for(message.from_id))
        return
    lines = [node.question or "Вопрос"]
    lines.extend(
        f"\n#{child.id} {child.button_text or child.question or 'Вопрос'}" for child in children
    )
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


@vk_bot.on.private_message(text=COMMANDS_EVENTS)
async def events_handler(message: Message):
    async with async_session_maker() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.event_date >= func.now())
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
        lines.append(f"\n#{event.id} {event.title} ({date})\nЗапись: Записаться #{event.id}")
    await message.answer("".join(lines), keyboard=await _main_keyboard_for(message.from_id))


@vk_bot.on.private_message(RegexRule(COMMAND_REGISTER_EVENT_PATTERN))
async def register_event_handler(message: Message):
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
        # Проверяем, что мероприятие ещё не прошло
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


def build_anonymous_choice_keyboard() -> str:
    return json.dumps(
        {
            "one_time": True,
            "buttons": [
                [
                    {
                        "action": {"type": "text", "label": "Остаться анонимным"},
                        "color": "secondary",
                    },
                    {
                        "action": {"type": "text", "label": "Остаться не анонимным"},
                        "color": "primary",
                    },
                ]
            ],
        }
    )


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
        "• FAQ — ответы на часто задаваемые вопросы",
        "• Мероприятия — актуальные события и запись на них",
        "• База знаний — полезные статьи и инструкции",
        "• Мои заявки — список ваших обращений и их статусы\n",
        "💡 При создании обращения можно выбрать: получить ответ в VK или отправить анонимно.",
        "🔒 Отправляя обращение, вы даёте согласие на обработку персональных данных в соответствии с 152-ФЗ.",
    ]
    return "\n".join(lines)


async def _get_department_names() -> list[str]:
    """Получить список названий отделов из БД."""
    from core.models import Department

    async with async_session_maker() as session:
        departments = list(await session.scalars(select(Department).order_by(Department.name)))
        return [dept.name for dept in departments if dept.name]


async def _main_keyboard_for(vk_id: int) -> str:
    user = await BotCore.get_or_create_user(vk_id=vk_id)
    departments = await _get_department_names()
    return build_main_keyboard(await BotCore.is_admin(user), departments)


@vk_bot.on.private_message(text=COMMANDS_START)
async def start_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    await BotCore.log_action(user, "start", "Пользователь нажал /start")
    departments = await _get_department_names()
    keyboard = build_main_keyboard(await BotCore.is_admin(user), departments)
    await message.answer(_main_reply_text(departments), keyboard=keyboard)


@vk_bot.on.private_message(text=COMMANDS_CANCEL)
async def cancel_handler(message: Message):
    """Отменить текущее действие (создание заявки/вопроса)."""
    touch_heartbeat()
    user_state = await vk_bot.state_dispenser.get(message.from_id)
    if user_state is not None:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Действие отменено. Вы вернулись в главное меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
    else:
        await message.answer(
            "Нечего отменять. Вы уже в главном меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )


@vk_bot.on.private_message(text=COMMANDS_MY_TICKETS)
async def my_tickets_handler(message: Message):
    """Список заявок пользователя из базы: номер, отдел, тема, дата, статус, ответ."""
    touch_heartbeat()
    await BotCore.get_or_create_user(vk_id=message.from_id)
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=10)

    if not tickets:
        await message.answer(
            "У вас пока нет заявок.\n\nЧтобы создать заявку, выберите раздел в меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    # Преобразуем глобальные ID в локальные номера для пользователя
    user_ticket_map: dict[int, int] = {}
    local_ids = []
    for idx, ticket in enumerate(tickets, 1):
        if ticket.id is not None:
            user_ticket_map[ticket.id] = idx
            local_ids.append(idx)

    await message.answer(
        format_ticket_list(tickets, user_ticket_map),
        keyboard=build_tickets_keyboard(local_ids),
    )


@vk_bot.on.private_message(RegexRule(COMMAND_TICKET_DETAILS_PATTERN))
async def ticket_details_handler(message: Message):
    """История заявки: «Подробнее #N» — номер, отдел, тема, статус, вся переписка."""
    touch_heartbeat()
    match = re.search(r"(\d+)", message.text or "")
    if not match:
        await message.answer(
            "Укажите номер заявки, например: «Подробнее #12» или «Подробнее 12».",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    # Получаем локальный номер и преобразуем в глобальный ID
    local_id = int(match.group(1))
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=10)

    # Находим заявку по локальному номеру
    ticket = None
    for idx, t in enumerate(tickets, 1):
        if idx == local_id:
            ticket = t
            break

    if ticket is None or ticket.id is None:
        await message.answer(
            f"Заявка #{local_id} не найдена среди ваших заявок.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    history = await get_ticket_messages(ticket.id)
    await message.answer(
        format_ticket_details(ticket, history),
        keyboard=build_tickets_keyboard([local_id]),
    )


async def _operator_can_access(vk_id: int, ticket_id: int) -> bool:
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


@vk_bot.on.private_message(RegexRule(STUDENT_REPLY_PATTERN))
async def ticket_reply_handler(message: Message):
    """Единый диспетчер ответов на заявки: операторы (глобальный ID) и студенты (локальный ID)."""
    match = re.match(STUDENT_REPLY_PATTERN, message.text or "", re.DOTALL)
    if match is None:
        return
    parsed_id = int(match.group(1))
    reply_text = match.group(2).strip()

    # 1. Если отправитель — оператор с доступом к глобальной заявке #parsed_id
    if await _operator_can_access(message.from_id, parsed_id):
        ticket, delivered = await reply_to_ticket(
            ticket_id=parsed_id, admin_username=str(message.from_id), message=reply_text
        )
        if ticket is None:
            await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
            return
        await message.answer(
            "Ответ сохранён." + (" Уведомление поставлено в очередь VK." if delivered else ""),
            keyboard=build_admin_keyboard(),
        )
        return

    # 2. Иначе обрабатываем как ответ студента по локальному номеру
    local_id = parsed_id
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=10)

    # Находим заявку по локальному номеру
    ticket = None
    for idx, t in enumerate(tickets, 1):
        if idx == local_id:
            ticket = t
            break

    if ticket is None or ticket.id is None:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope_for_vk_id(session, message.from_id)
        if is_super or dept_id is not None:
            await message.answer(
                f"Заявка #{parsed_id} не найдена или недоступна вашему отделу.",
                keyboard=build_admin_keyboard(),
            )
            return

        await message.answer(
            f"Заявка #{local_id} не найдена среди ваших заявок.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    # Используем глобальный ID для добавления ответа студента
    result = await add_student_reply(ticket.id, message.from_id, reply_text)
    if result is None:
        await message.answer(
            "Заявка не найдена или ответ в неё недоступен.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    await message.answer(
        f"Ответ добавлен в заявку #{local_id}. Администратор увидит его в переписке.",
        keyboard=build_tickets_keyboard([local_id]),
    )


async def _start_ticket_flow(
    message: Message,
    topic: str,
    department: str | None = None,
) -> None:
    """Единый старт создания обращения: разделы меню, вопрос или анонимный запрос."""
    touch_heartbeat()
    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_DESCRIPTION,
        topic=topic,
        department=department,
    )
    header = f"Раздел «{topic}»" if department else "Задать вопрос"
    await message.answer(
        f"{header}\n\n"
        "Опишите вашу проблему одним сообщением: что случилось, когда и где.\n"
        "Чем подробнее описание, тем быстрее ответственный отдел сможет помочь.\n\n"
        "После текста вы сможете выбрать, оставить ли свой VK ID для ответа.\n\n"
        "Нажмите «Отмена», чтобы отменить создание заявки.",
        keyboard=build_cancel_keyboard(),
    )


@vk_bot.on.private_message(text=COMMANDS_HOUSING)
async def housing_section(message: Message):
    """Раздел «Жил-быт»: бытовые условия и проблемы в общежитии."""
    await _start_ticket_flow(message, "Жил-быт", "Жил-быт")


@vk_bot.on.private_message(text=COMMANDS_CULTURE)
async def culture_section(message: Message):
    """Раздел «Культмасс»: мероприятия, анонсы, запись на события."""
    await _start_ticket_flow(message, "Культмасс", "Культмасс")


@vk_bot.on.private_message(text=COMMANDS_INFORMATION)
async def information_section(message: Message):
    """Раздел «Информ»: справочная информация и частые вопросы."""
    await _start_ticket_flow(message, "Информ", "Информ")


@vk_bot.on.private_message(text=COMMANDS_CORPORATE)
async def corporate_section(message: Message):
    """Раздел «Корпоративный»: вопросы мероприятий и жизни университета."""
    await _start_ticket_flow(message, "Корпоративный", "Корпоративный")


@vk_bot.on.private_message(text=COMMANDS_QUESTION)
async def question_section_start(message: Message):
    """Общий вопрос без привязки к конкретному отделу."""
    await _start_ticket_flow(message, "Вопрос")


@vk_bot.on.private_message(text=COMMANDS_ANONYMOUS)
async def anonymous_section_start(message: Message):
    """Начало анонимного обращения — бот запрашивает текст."""
    touch_heartbeat()
    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_DESCRIPTION,
        topic="Анонимное обращение",
        department=None,
    )
    await message.answer(
        "Анонимное обращение\n\n"
        "Вы можете сообщить о проблеме без указания своего имени.\n"
        "Ваше имя и VK ID не будут привязаны к обращению.\n\n"
        "Опишите проблему в одном сообщении:",
        keyboard=await _main_keyboard_for(message.from_id),
    )


@vk_bot.on.private_message(state=TicketStates.WAITING_DESCRIPTION)
async def ticket_description_handler(message: Message):
    """Получить текст обращения и запросить режим обратной связи."""
    touch_heartbeat()
    description = message.text.strip()

    if len(description) < 10:
        await message.answer(
            "Описание слишком короткое.\n\n"
            "Пожалуйста, опишите проблему подробнее (минимум 10 символов).\n\n"
            "Введите текст обращения:"
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    department = state_peer.payload.get("department") if state_peer else None

    # Если пользователь ввел «Создать заявку» после подсказки из базы знаний,
    # используем уже сохранённое в сессии описание, минуя повторный поиск по БЗ.
    stored_description = state_peer.payload.get("description") if state_peer else None
    if stored_description and description.strip().casefold() == "создать заявку":
        topic = state_peer.payload.get("topic", "Вопрос") if state_peer else "Вопрос"
        department = state_peer.payload.get("department") if state_peer else None
        await vk_bot.state_dispenser.set(
            message.from_id,
            TicketStates.WAITING_IDENTITY_CHOICE,
            description=stored_description,
            topic=topic,
            department=department,
        )
        await message.answer(
            "Выберите режим обращения:\n\n"
            "«Остаться анонимным» — VK ID не будет сохранён, ответ через VK невозможен.\n"
            "«Остаться не анонимным» — администратор сможет ответить вам в VK.",
            keyboard=build_anonymous_choice_keyboard(),
        )
        return

    # Try the knowledge base before opening a ticket for a known routine question.
    # Логика поиска — в core/ticket_service.py (общая для бота и веб-панели).
    async with async_session_maker() as session:
        knowledge_entry = await find_knowledge_entry(session, description, department)
    if knowledge_entry is not None:
        topic = state_peer.payload.get("topic", "Вопрос") if state_peer else "Вопрос"
        department = state_peer.payload.get("department") if state_peer else None
        await vk_bot.state_dispenser.set(
            message.from_id,
            TicketStates.WAITING_DESCRIPTION,
            description=description,
            topic=topic,
            department=department,
        )
        await message.answer(
            f"Возможно, поможет эта информация:\n\n{knowledge_entry.answer}\n\n"
            "Если ответ не подходит, напишите «Создать заявку», чтобы продолжить.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    topic = state_peer.payload.get("topic", "Вопрос") if state_peer else "Вопрос"
    department = state_peer.payload.get("department") if state_peer else None
    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_IDENTITY_CHOICE,
        description=description,
        topic=topic,
        department=department,
    )
    await message.answer(
        "Выберите режим обращения:\n\n"
        "«Остаться анонимным» — VK ID не будет сохранён, ответ через VK невозможен.\n"
        "«Остаться не анонимным» — администратор сможет ответить вам в VK.",
        keyboard=build_anonymous_choice_keyboard(),
    )


@vk_bot.on.private_message(
    state=TicketStates.WAITING_IDENTITY_CHOICE,
    text=COMMANDS_ANONYMOUS_STAY,
)
async def ticket_identity_choice_handler(message: Message):
    """Создать обращение после выбора канала обратной связи."""
    keep_identity = message.text == COMMAND_REVEAL_IDENTITY
    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    description = payload.get("description", "")
    topic = payload.get("topic", "Вопрос")
    department = payload.get("department")

    try:
        ticket = await create_ticket(
            topic=topic,
            description=description,
            vk_id=message.from_id,
            keep_identity=keep_identity,
            department_name=department,
        )

        department_name = department or "Общая"
        answer_tail = (
            "Администратор сможет ответить вам в этом диалоге."
            if keep_identity
            else "VK ID не сохранён. Ответ через VK на это обращение не отправляется."
        )
        await message.answer(
            f"Ваше обращение принято.\n\n"
            f"Номер обращения: #{ticket.id}\n"
            f"Раздел: {department_name}\n"
            f"Статус: {status_label(ticket.status)}\n\n"
            f"{answer_tail}",
            keyboard=await _main_keyboard_for(message.from_id),
        )

        await vk_bot.state_dispenser.delete(message.from_id)

    except Exception:
        logger.exception("Ошибка при создании обращения")
        await message.answer(
            "Не удалось отправить обращение. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        await vk_bot.state_dispenser.delete(message.from_id)


@vk_bot.on.private_message(text=COMMANDS_ADMIN)
async def admin_panel(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к админ-панели.\n\n"
            "По всем вопросам обращайтесь к главному администратору.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    await BotCore.log_action(user, "admin_panel", "Открыта админ-панель")
    await message.answer(
        "Админ-панель\n\n"
        "Доступные функции: просмотр заявок, формирование отчётов.\n"
        "Нажмите «Заявки администратора» для просмотра активных заявок.\n"
        "Нажмите «Сформировать отчет», чтобы получить файл отчёта в VK.",
        keyboard=build_admin_keyboard(),
    )


@vk_bot.on.private_message(text=COMMANDS_REGULAR_MENU)
async def regular_menu_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    departments = await _get_department_names()
    keyboard = build_main_keyboard(await BotCore.is_admin(user), departments)
    await message.answer(
        _main_reply_text(departments),
        keyboard=keyboard,
    )


@vk_bot.on.private_message(text=COMMANDS_ADMIN_TICKETS)
async def admin_tickets_handler(message: Message):
    """Показать оператору заявки, доступные его отделу."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, message.from_id)

    if not is_super and dept_id is None:
        await message.answer(
            "У вас нет доступа к заявкам администратора.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    async with async_session_maker() as session:
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department))
            .order_by(Ticket.created_at.desc())
            .limit(20)
        )
        if not is_super and dept_id is not None:
            stmt = stmt.where(Ticket.department_id == dept_id)
        count_stmt = select(func.count(Ticket.id)).where(
            Ticket.status.not_in((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO))
        )
        total_count_stmt = select(func.count(Ticket.id))
        if not is_super and dept_id is not None:
            count_stmt = count_stmt.where(Ticket.department_id == dept_id)
            total_count_stmt = total_count_stmt.where(Ticket.department_id == dept_id)
        pending_count = await session.scalar(count_stmt)
        total_count = await session.scalar(total_count_stmt)
        tickets = list((await session.scalars(stmt)).all())
    if not tickets:
        await message.answer(
            f"Всего доступных заявок: {total_count or 0}\n"
            f"Незавершенных заявок: {pending_count or 0}\n"
            "Доступных заявок нет.",
            keyboard=build_admin_keyboard(),
        )
        return
    lines = [
        f"Всего доступных заявок: {total_count or 0}",
        f"Незавершенных заявок: {pending_count or 0}",
        "Заявки (последние 20):",
    ]
    for ticket in tickets:
        description = (ticket.description or "Без описания").strip()
        if len(description) > 500:
            description = description[:500].rstrip() + "..."
        lines.append(
            f"\n#{ticket.id} [{status_label(ticket.status)}] {ticket.topic or 'Без темы'}\n"
            f"Описание: {description}"
        )
    lines.append("")
    lines.append("Как ответить на заявку:")
    lines.append("  Ответ #N: текст ответа. Например: «Ответ #12: Уже исправили»")
    lines.append("  Статус #N: статус. Например: «Статус #12: Выполнено»")
    await message.answer("\n".join(lines), keyboard=build_admin_keyboard())


def _parse_status(value: str) -> TicketStatus:
    """Распознать статус по имени enum или по русской метке."""
    value = value.strip()
    try:
        return TicketStatus(value)
    except ValueError:
        pass
    for status, label in STATUS_LABELS.items():
        if label.lower() == value.lower():
            return status
    raise ValueError(f"Неизвестный статус: {value!r}")


@vk_bot.on.private_message(RegexRule(ADMIN_STATUS_PATTERN))
async def admin_status_handler(message: Message):
    match = re.match(ADMIN_STATUS_PATTERN, message.text or "", re.DOTALL)
    if not match or not await _operator_can_access(message.from_id, int(match.group(1))):
        await message.answer(
            "Заявка не найдена или недоступна.",
            keyboard=build_admin_keyboard(),
        )
        return
    try:
        new_status = _parse_status(match.group(2))
        ticket = await change_ticket_status(int(match.group(1)), new_status, str(message.from_id))
    except (ValueError, StatusTransitionError):
        await message.answer(
            "Неизвестный статус или недопустимый переход.\n"
            "Допустимые статусы: Новое, В обработке, Выполнено, Передано в администрацию, "
            "Передано в хозчасть.",
            keyboard=build_admin_keyboard(),
        )
        return
    await message.answer(
        f"Статус заявки #{match.group(1)} изменён: {status_label(ticket.status)}."
        if ticket
        else "Заявка не найдена.",
        keyboard=build_admin_keyboard(),
    )


@vk_bot.on.private_message(text=COMMANDS_REPORT)
async def report_handler(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    try:
        from core.reporting import get_app_tz

        report_date = datetime.now(get_app_tz()) - timedelta(days=1)
        data = await get_report_for_date(report_date.date())
        report_bytes = build_daily_report(data, report_date)
        filename = f"report_{report_date:%Y-%m-%d}.xlsx"
        await send_report_to_vk(
            vk_bot.api,
            message.from_id,
            report_bytes,
            filename,
        )
        await BotCore.log_action(user, "report_generated", f"Сформирован отчёт {filename}")
    except (ValueError, OSError, KeyError, VKAPIError):
        logger.exception("Ошибка при формировании отчёта")
        await BotCore.log_action(user, "report_failed", "Ошибка при формировании отчёта")
        await message.answer(
            "Не удалось сформировать отчёт. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            keyboard=build_admin_keyboard(),
        )
        return
    await message.answer(
        f"Отчёт за {report_date:%d.%m.%Y} сформирован и отправлен вам в VK.",
        keyboard=build_admin_keyboard(),
    )


@vk_bot.on.private_message(text=COMMANDS_REPORT_BY_DATE)
async def report_by_date_handler(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return
    await vk_bot.state_dispenser.set(message.from_id, ReportStates.WAITING_DATE)
    await message.answer(
        "Введите дату в формате ДД.ММ.ГГГГ (например: 31.08.2026)\n\n"
        "Нажмите «Отмена», чтобы отменить.",
        keyboard=build_cancel_keyboard(),
    )


@vk_bot.on.private_message(RegexRule(r"^\d{2}\.\d{2}\.\d{4}$"), state=ReportStates.WAITING_DATE)
async def report_by_date_input(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        return

    parsed = parse_report_date(message.text)
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ (например: 31.08.2026)"
        )
        return

    try:
        data = await get_report_for_date(parsed)
        report_dt = datetime.combine(parsed, datetime.min.time(), tzinfo=get_app_tz())
        report_bytes = build_daily_report(data, report_dt)
        filename = f"report_{parsed:%Y-%m-%d}.xlsx"
        await send_report_to_vk(
            vk_bot.api,
            message.from_id,
            report_bytes,
            filename,
        )
        await BotCore.log_action(
            user, "report_generated", f"Сформирован отчёт за {parsed} (по дате)"
        )
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            f"Отчет за {parsed:%d.%m.%Y} сформирован и отправлен.",
            keyboard=build_admin_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка при формировании отчёта за %s", parsed)
        await BotCore.log_action(
            user, "report_failed", f"Ошибка при формировании отчёта за {parsed}"
        )
        await message.answer(
            "Не удалось отправить отчёт. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            keyboard=build_admin_keyboard(),
        )
        await vk_bot.state_dispenser.delete(message.from_id)


@vk_bot.on.private_message(text=COMMANDS_REPORT_BY_PERIOD)
async def report_by_period_handler(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return
    await vk_bot.state_dispenser.set(message.from_id, ReportStates.WAITING_DATE_FROM)
    await message.answer(
        "Введите диапазон дат в формате:\n"
        "31.08.2026 - 15.09.2026\n"
        "или\n"
        "с 31.08.2026 по 15.09.2026\n\n"
        "Нажмите «Отмена», чтобы отменить.",
        keyboard=build_cancel_keyboard(),
    )


_REPORT_PERIOD_PATTERN = r"^(?:\d{2}\.\d{2}\.\d{4}\s*[-–—]\s*\d{2}\.\d{2}\.\d{4}|с\s+\d{2}\.\d{2}\.\d{4}\s+по\s+\d{2}\.\d{2}\.\d{4})$"


@vk_bot.on.private_message(RegexRule(_REPORT_PERIOD_PATTERN), state=ReportStates.WAITING_DATE_FROM)
async def report_by_period_input(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        return

    normalized = message.text.strip()
    # Убираем предлог "с" в начале и заменяем "по" между двумя датами на тире,
    # чтобы результат всегда содержал тире и корректно парсился далее.
    normalized = re.sub(r"^\s*с\s+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})",
        r"\1 - \2",
        normalized,
        flags=re.IGNORECASE,
    )
    # Разбиваем по тире (разные варианты: -, –, —)
    parts = re.split(r"\s*[-–—]\s*", normalized, maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Неверный формат. Введите две даты через тире (например: 31.08.2026 - 15.09.2026)"
        )
        return

    date_from = parse_report_date(parts[0])
    date_to = parse_report_date(parts[1])

    if date_from is None or date_to is None:
        await message.answer("Неверный формат даты. Используйте ДД.ММ.ГГГГ (например: 31.08.2026)")
        return

    if date_from > date_to:
        await message.answer("Дата начала не может быть позже даты окончания.")
        return

    try:
        data = await get_report_for_period(date_from, date_to)
        report_dt = datetime.combine(date_from, datetime.min.time(), tzinfo=get_app_tz())
        report_bytes = build_daily_report(data, report_dt)
        filename = f"report_{date_from:%Y-%m-%d}_to_{date_to:%Y-%m-%d}.xlsx"
        await send_report_to_vk(
            vk_bot.api,
            message.from_id,
            report_bytes,
            filename,
        )
        await BotCore.log_action(
            user, "report_generated", f"Сформирован отчёт за период {date_from} - {date_to}"
        )
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            f"Отчёт за период с {date_from:%d.%m.%Y} по {date_to:%d.%m.%Y} "
            "сформирован и отправлен вам в VK.",
            keyboard=build_admin_keyboard(),
        )
    except Exception:
        logger.exception(
            "Ошибка при формировании отчёта за период %s - %s",
            date_from,
            date_to,
        )
        await BotCore.log_action(
            user,
            "report_failed",
            f"Ошибка при формировании отчёта за период {date_from} - {date_to}",
        )
        await message.answer(
            "Не удалось отправить отчёт. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            keyboard=build_admin_keyboard(),
        )
        await vk_bot.state_dispenser.delete(message.from_id)


@vk_bot.on.private_message()
async def fallback_handler(message: Message):
    """Возвращает пользователя в основное меню для неизвестных сообщений."""
    touch_heartbeat()
    await message.answer(
        "Я не распознал команду. Выберите действие в меню:",
        keyboard=await _main_keyboard_for(message.from_id),
    )


if __name__ == "__main__":
    vk_bot.run()
