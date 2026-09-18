"""Хендлеры для студентов: меню, обратная связь, тикеты, FSM создания обращений."""

import logging
import re

from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import (
    TicketStates,
    _get_department_names,
    _main_keyboard_for,
    _main_reply_text,
    _operator_can_access,
)
from bots.vk.keyboards import (
    build_admin_keyboard,
    build_anonymous_choice_keyboard,
    build_cancel_keyboard,
    build_main_keyboard,
    build_tickets_keyboard,
)
from core.bot_core import BotCore
from core.commands import (
    COMMAND_REVEAL_IDENTITY,
    COMMAND_TICKET_DETAILS_PATTERN,
    COMMANDS_ANONYMOUS,
    COMMANDS_ANONYMOUS_STAY,
    COMMANDS_CANCEL,
    COMMANDS_CORPORATE,
    COMMANDS_CULTURE,
    COMMANDS_HOUSING,
    COMMANDS_INFORMATION,
    COMMANDS_MY_TICKETS,
    COMMANDS_QUESTION,
    COMMANDS_START,
    STUDENT_REPLY_PATTERN,
)
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.ticket_service import (
    add_student_reply,
    create_ticket,
    find_knowledge_entry,
    format_ticket_details,
    format_ticket_list,
    get_ticket_messages,
    get_user_tickets,
    reply_to_ticket,
    status_label,
)

logger = logging.getLogger(__name__)
student_labeler = BotLabeler()


@student_labeler.private_message(text=COMMANDS_START)
async def start_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    await BotCore.log_action(user, "start", "Пользователь нажал /start")
    departments = await _get_department_names()
    keyboard = build_main_keyboard(await BotCore.is_admin(user), departments)
    await message.answer(_main_reply_text(departments), keyboard=keyboard)


@student_labeler.private_message(text=COMMANDS_CANCEL)
async def cancel_handler(message: Message):
    """Отменить текущее действие (создание заявки/вопроса)."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

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


@student_labeler.private_message(text=COMMANDS_MY_TICKETS)
async def my_tickets_handler(message: Message):
    """Список заявок пользователя из базы: номер, отдел, тема, дата, статус, ответ."""
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    await BotCore.log_action(user, "my_tickets", "Запрос списка моих заявок")
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=10)
    if not tickets:
        await message.answer(
            "У вас пока нет созданных заявок.\n\n"
            "Чтобы создать заявку, выберите нужный раздел в меню ниже.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    local_ids = list(range(1, len(tickets) + 1))
    user_ticket_map = {t.id: idx for idx, t in enumerate(tickets, 1) if t.id is not None}
    await message.answer(
        format_ticket_list(tickets, user_ticket_map),
        keyboard=build_tickets_keyboard(local_ids),
    )


@student_labeler.private_message(RegexRule(COMMAND_TICKET_DETAILS_PATTERN))
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

    local_id = int(match.group(1))
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=10)

    # O(1) поиск заявки по локальному номеру
    ticket = tickets[local_id - 1] if 1 <= local_id <= len(tickets) else None

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


@student_labeler.private_message(RegexRule(STUDENT_REPLY_PATTERN))
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

    # O(1) поиск заявки по локальному номеру
    ticket = tickets[local_id - 1] if 1 <= local_id <= len(tickets) else None

    if ticket is None or ticket.id is None:
        await message.answer(
            f"Заявка #{local_id} не найдена среди ваших заявок.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

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
    from bots.vk.bot import vk_bot

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


@student_labeler.private_message(text=COMMANDS_HOUSING)
async def housing_section(message: Message):
    await _start_ticket_flow(message, "Жил-быт", "Жил-быт")


@student_labeler.private_message(text=COMMANDS_CULTURE)
async def culture_section(message: Message):
    await _start_ticket_flow(message, "Культмасс", "Культмасс")


@student_labeler.private_message(text=COMMANDS_INFORMATION)
async def information_section(message: Message):
    await _start_ticket_flow(message, "Информ", "Информ")


@student_labeler.private_message(text=COMMANDS_CORPORATE)
async def corporate_section(message: Message):
    await _start_ticket_flow(message, "Корпоративный", "Корпоративный")


@student_labeler.private_message(text=COMMANDS_QUESTION)
async def question_section_start(message: Message):
    await _start_ticket_flow(message, "Вопрос")


@student_labeler.private_message(text=COMMANDS_ANONYMOUS)
async def anonymous_section_start(message: Message):
    touch_heartbeat()
    from bots.vk.bot import vk_bot

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


@student_labeler.private_message(state=TicketStates.WAITING_DESCRIPTION)
async def ticket_description_handler(message: Message):
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    description = (message.text or "").strip()

    if len(description) < 10:
        await message.answer(
            "Описание слишком короткое.\n\n"
            "Пожалуйста, опишите проблему подробнее (минимум 10 символов).\n\n"
            "Введите текст обращения:"
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    department = state_peer.payload.get("department") if state_peer else None

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


@student_labeler.private_message(
    state=TicketStates.WAITING_IDENTITY_CHOICE,
    text=COMMANDS_ANONYMOUS_STAY,
)
async def ticket_identity_choice_handler(message: Message):
    from bots.vk.bot import vk_bot

    keep_identity = message.text == COMMAND_REVEAL_IDENTITY
    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    description = payload.get("description", "")
    topic = payload.get("topic", "Вопрос")
    department = payload.get("department")

    if not str(description).strip():
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Сессия создания обращения истекла или текст вопроса пуст.\n"
            "Пожалуйста, начните оформление обращения заново через главное меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

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


# Алиасы для обратной совместимости
student_reply_handler = ticket_reply_handler
ask_anonymous_handler = anonymous_section_start
identity_choice_handler = ticket_identity_choice_handler
question_handler = question_section_start
