"""Хендлеры для студентов: меню, обратная связь, тикеты, FSM создания обращений."""

import logging
import re
from typing import Any

from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import (
    PartnershipStates,
    ReportStates,
    TicketStates,
    _get_department_names,
    _main_keyboard_for,
    _main_reply_text,
    _operator_can_access,
)
from bots.vk.handlers.pagination import (
    FETCH_LIMIT,
    KIND_MY_TICKETS,
    PAGE_SIZE,
    clamp_page,
    open_list,
    page_count,
    persist_page,
    register_renderer,
)
from bots.vk.keyboards import (
    build_admin_keyboard,
    build_anonymous_choice_keyboard,
    build_cancel_keyboard,
    build_knowledge_suggest_keyboard,
    build_main_keyboard,
    build_ticket_department_keyboard,
    build_tickets_keyboard,
)
from core.bot_core import BotCore
from core.commands import (
    COMMAND_CREATE_TICKET,
    COMMAND_REVEAL_IDENTITY,
    COMMAND_TICKET_DETAILS_PATTERN,
    COMMANDS_ANONYMOUS,
    COMMANDS_ANONYMOUS_STAY,
    COMMANDS_CANCEL,
    COMMANDS_CORPORATE,
    COMMANDS_CREATE_TICKET,
    COMMANDS_CULTURE,
    COMMANDS_HOUSING,
    COMMANDS_INFORMATION,
    COMMANDS_MY_TICKETS,
    COMMANDS_PARTNERSHIP,
    COMMANDS_QUESTION,
    COMMANDS_START,
    COMMANDS_WITHOUT_DEPT,
    STUDENT_REPLY_PATTERN,
)
from sqlalchemy import select

from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import PartnershipRequest, User
from core.redis_client import get_redis_client
from core.ticket_service import (
    add_student_reply,
    create_ticket,
    find_knowledge_entry,
    format_ticket_details,
    format_ticket_list,
    get_ticket_messages,
    get_user_ticket_by_id,
    get_user_ticket_local_number,
    get_user_tickets,
    get_user_tickets_mapping,
    reply_to_ticket,
    resolve_user_ticket,
    status_label,
)

logger = logging.getLogger(__name__)
student_labeler = BotLabeler()

# === Ограничение частоты подачи обращений (SEC-08) ===
# Значения настраиваются через переменные окружения (.env)
import os

TICKET_RATE_LIMIT = int(os.getenv("VK_TICKET_RATE_LIMIT", "3"))
TICKET_RATE_WINDOW_SECONDS = int(os.getenv("VK_TICKET_RATE_WINDOW_SECONDS", "300"))


async def _is_ticket_rate_limited(vk_id: int) -> bool:
    """Redis-based ограничение частоты создания обращений (SEC-08).

    Фиксированное окно: атомарный INCR-счётчик с TTL (первый инкремент
    устанавливает окно 5 минут). Работает единообразно при нескольких
    воркерах бота. Если Redis недоступен — ограничение не применяется
    (fail-open), чтобы сбой кэша не блокировал реальных студентов.
    """
    redis = await get_redis_client()
    if redis is None:
        return False
    key = f"ticket_rate:{vk_id}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, TICKET_RATE_WINDOW_SECONDS)
        return count > TICKET_RATE_LIMIT
    except Exception as e:
        logger.warning("SEC-08: Redis недоступен, rate limit обращений не применён: %s", e)
        return False


@student_labeler.private_message(text=COMMANDS_START)
async def start_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    await BotCore.log_action(user, "start", "Пользователь нажал /start")
    departments = await _get_department_names()
    keyboard = build_main_keyboard(await BotCore.is_admin(user), departments)
    await message.answer(_main_reply_text(departments), keyboard=keyboard)


@student_labeler.private_message(text=COMMANDS_CANCEL)
@student_labeler.private_message(state=TicketStates.WAITING_DEPARTMENT, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=TicketStates.WAITING_DESCRIPTION, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=TicketStates.WAITING_IDENTITY_CHOICE, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=PartnershipStates.WAITING_PROPOSAL, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=ReportStates.WAITING_TYPE, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=ReportStates.WAITING_DATE, text=COMMANDS_CANCEL)
@student_labeler.private_message(state=ReportStates.WAITING_DATE_FROM, text=COMMANDS_CANCEL)
async def cancel_handler(message: Message):
    """Отменить текущее действие (создание заявки/вопроса/партнёрства/отчёта)."""
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


async def _render_my_tickets_page(message: Message, page: int, meta: dict[str, Any]) -> None:
    """Отрисовать страницу списка заявок студента (пагинация, ЭТАП 4.1 / B4)."""
    tickets = await get_user_tickets(message.from_id, include_completed=True, limit=FETCH_LIMIT)
    page = clamp_page(page, len(tickets))
    await persist_page(message.from_id, KIND_MY_TICKETS, page, meta)

    if not tickets:
        await message.answer(
            "У вас пока нет созданных заявок.\n\n"
            "Чтобы создать заявку, нажмите кнопку «Создать заявку» в меню ниже.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    total_pages = page_count(len(tickets))
    start = page * PAGE_SIZE
    page_tickets = tickets[start : start + PAGE_SIZE]

    mapping = await get_user_tickets_mapping(message.from_id)
    # Используем персональные локальные номера (#1, #2, ...)
    ticket_ids = [mapping.get(t.id, 1) for t in page_tickets if t.id is not None]

    text = format_ticket_list(page_tickets, user_ticket_map=mapping)
    if total_pages > 1:
        text += f"\n\n📄 Страница {page + 1} из {total_pages}"
    await message.answer(
        text,
        keyboard=build_tickets_keyboard(
            ticket_ids, page=page, has_more=page + 1 < total_pages
        ),
    )


@student_labeler.private_message(text=COMMANDS_MY_TICKETS)
async def my_tickets_handler(message: Message):
    """Список заявок пользователя из базы: номер, отдел, тема, дата, статус, ответ."""
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    await BotCore.log_action(user, "my_tickets", "Запрос списка моих заявок")
    await open_list(message, KIND_MY_TICKETS)


@student_labeler.private_message(RegexRule(r"(?i)^(?:Подробнее\s*#?|#)(\d+)$"))
async def ticket_details_handler(message: Message):
    """История заявки: «Подробнее #N» или «#N» — номер, отдел, тема, статус, вся переписка."""
    touch_heartbeat()
    match = re.search(r"(\d+)", message.text or "")
    if not match:
        await message.answer(
            "Укажите номер заявки, например: «Подробнее #1» или «#1».",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    parsed_id = int(match.group(1))

    # Находим заявку по персональному локальному номеру пользователя
    ticket, local_num = await resolve_user_ticket(message.from_id, parsed_id)

    if ticket is None or ticket.id is None:
        await message.answer(
            f"Заявка #{parsed_id} не найдена среди ваших заявок.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    history = await get_ticket_messages(ticket.id)
    details = format_ticket_details(ticket, history, local_id=local_num)
    details += (
        f"\n\n💬 Чтобы задать уточняющий вопрос или дополнить заявку, отправьте:\n"
        f"«Ответ #{local_num}: ваш текст»"
    )
    await message.answer(
        details,
        keyboard=build_tickets_keyboard([local_num]),
    )


@student_labeler.private_message(RegexRule(r"^\d+$"))
async def ticket_number_direct_handler(message: Message):
    """Прямой ввод номера заявки (например, «1» или «105») при просмотре списка."""
    from bots.vk.handlers.pagination import get_page_state

    raw_num = int((message.text or "").strip())
    state = await get_page_state(message.from_id)
    if state and state.get("kind") == KIND_MY_TICKETS:
        await ticket_details_handler(message)
        return

    ticket, _ = await resolve_user_ticket(message.from_id, raw_num)
    if ticket is not None:
        await ticket_details_handler(message)
        return


@student_labeler.private_message(RegexRule(STUDENT_REPLY_PATTERN))
async def ticket_reply_handler(message: Message):
    """Единый диспетчер ответов на заявки: автор заявки (студент) или оператор (администратор)."""
    match = re.match(STUDENT_REPLY_PATTERN, message.text or "", re.DOTALL)
    if match is None:
        return
    parsed_id = int(match.group(1))
    reply_text = match.group(2).strip()

    # 1. Сначала проверяем, является ли отправитель автором этой заявки (студентом)
    # Если автор заявки отправляет «Ответ #N: ...», это ВСЕГДА вопрос или дополнение от студента!
    user_ticket, local_num = await resolve_user_ticket(message.from_id, parsed_id)

    if user_ticket is not None and user_ticket.id is not None:
        result = await add_student_reply(user_ticket.id, message.from_id, reply_text)
        if result is None:
            await message.answer(
                "Заявка не найдена или ответ в неё недоступен.",
                keyboard=await _main_keyboard_for(message.from_id),
            )
            return
        await message.answer(
            f"Ваш вопрос/ответ добавлен к заявке #{local_num}.\n"
            f"Ответственный отдел уведомлён, статус обновлён.",
            keyboard=build_tickets_keyboard([local_num]),
        )
        return

    # 2. Если отправитель — НЕ автор заявки, но оператор с доступом к заявке #parsed_id — это ответ оператора студенту
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

    # 3. Ни автор, ни оператор
    await message.answer(
        f"Заявка #{parsed_id} не найдена среди ваших заявок.",
        keyboard=await _main_keyboard_for(message.from_id),
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
    header = f"Раздел «{topic}»" if department else "Создать заявку"
    await message.answer(
        f"{header}\n\n"
        "Опишите вашу проблему или вопрос одним сообщением (минимум 10 символов).\n"
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
@student_labeler.private_message(text=COMMANDS_CREATE_TICKET)
async def question_section_start(message: Message):
    """Общий старт создания заявки: выбор отдела, без отдела, с отменой."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    departments = await _get_department_names()
    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_DEPARTMENT,
    )
    await message.answer(
        "📝 Создание заявки\n\n"
        "Выберите отдел, в который хотите направить вопрос, либо выберите «Без отдела» (общий вопрос):\n\n"
        "Нажмите «Отмена», чтобы вернуться в главное меню.",
        keyboard=build_ticket_department_keyboard(departments),
    )


@student_labeler.private_message(state=TicketStates.WAITING_DEPARTMENT)
async def ticket_department_choice_handler(message: Message):
    """Выбор отдела или 'Без отдела' при создании заявки."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    text = (message.text or "").strip()
    if text.lower() in [c.lower() for c in COMMANDS_CANCEL]:
        await cancel_handler(message)
        return

    selected_dept = None
    topic = "Общее обращение"
    if text.lower() in [c.lower() for c in COMMANDS_WITHOUT_DEPT] or "без отдела" in text.lower():
        selected_dept = None
        dept_label = "Без отдела (Общий)"
    else:
        departments = await _get_department_names()
        matched = [d for d in departments if d.lower() == text.lower() or d.lower() in text.lower()]
        if not matched:
            await message.answer(
                "Пожалуйста, выберите отдел из предложенных кнопок или нажмите «Без отдела».\n"
                "Для отмены нажмите кнопку «Отмена».",
                keyboard=build_ticket_department_keyboard(departments),
            )
            return
        selected_dept = matched[0]
        dept_label = selected_dept
        topic = selected_dept

    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_DESCRIPTION,
        topic=topic,
        department=selected_dept,
    )
    await message.answer(
        f"📝 Выбран раздел: {dept_label}\n\n"
        "Опишите вашу проблему или вопрос одним сообщением (минимум 10 символов).\n"
        "Чем подробнее описание, тем быстрее мы сможем вам помочь.\n\n"
        "Нажмите «Отмена», чтобы отменить создание заявки.",
        keyboard=build_cancel_keyboard(),
    )


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
        "Опишите проблему в одном сообщении (минимум 10 символов):\n\n"
        "Нажмите «Отмена», чтобы вернуться в меню.",
        keyboard=build_cancel_keyboard(),
    )


@student_labeler.private_message(state=TicketStates.WAITING_DESCRIPTION)
async def ticket_description_handler(message: Message):
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    text = (message.text or "").strip()
    if text.lower() in [c.lower() for c in COMMANDS_CANCEL]:
        await cancel_handler(message)
        return

    description = text

    if len(description) < 10:
        await message.answer(
            "Описание слишком короткое.\n\n"
            "Пожалуйста, опишите проблему подробнее (минимум 10 символов).\n\n"
            "Или нажмите «Отмена» для возврата в меню:",
            keyboard=build_cancel_keyboard(),
        )
        return

    if len(description) > 3000:
        await message.answer(
            "Описание слишком длинное (максимум 3000 символов).\n\n"
            f"Текущая длина: {len(description)} символов. Пожалуйста, сократите текст и отправьте снова:",
            keyboard=build_cancel_keyboard(),
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    department = state_peer.payload.get("department") if state_peer else None
    topic = state_peer.payload.get("topic", "Вопрос") if state_peer else "Вопрос"

    stored_description = state_peer.payload.get("description") if state_peer else None
    if stored_description and description.strip().casefold() in ("создать заявку", "продолжить"):
        await vk_bot.state_dispenser.set(
            message.from_id,
            TicketStates.WAITING_IDENTITY_CHOICE,
            description=stored_description,
            topic=topic,
            department=department,
        )
        await message.answer(
            "Выберите режим обращения:\n\n"
            "«Остаться не анонимным» — администратор сможет ответить вам в VK.\n"
            "«Остаться анонимным» — VK ID не будет сохранён, ответ через VK невозможен.\n\n"
            "Нажмите «Отмена», если хотите отменить создание заявки.",
            keyboard=build_anonymous_choice_keyboard(),
        )
        return

    async with async_session_maker() as session:
        knowledge_entry = await find_knowledge_entry(session, description, department)
    if knowledge_entry is not None:
        await vk_bot.state_dispenser.set(
            message.from_id,
            TicketStates.WAITING_DESCRIPTION,
            description=description,
            topic=topic,
            department=department,
        )
        await message.answer(
            f"Возможно, поможет эта информация:\n\n{knowledge_entry.answer}\n\n"
            "Если ответ не подходит, нажмите «Создать заявку», чтобы продолжить.",
            keyboard=build_knowledge_suggest_keyboard(),
        )
        return

    await vk_bot.state_dispenser.set(
        message.from_id,
        TicketStates.WAITING_IDENTITY_CHOICE,
        description=description,
        topic=topic,
        department=department,
    )
    await message.answer(
        "Выберите режим обращения:\n\n"
        "«Остаться не анонимным» — администратор сможет ответить вам в VK.\n"
        "«Остаться анонимным» — VK ID не будет сохранён, ответ через VK невозможен.\n\n"
        "Нажмите «Отмена», если хотите отменить создание заявки.",
        keyboard=build_anonymous_choice_keyboard(),
    )


@student_labeler.private_message(
    state=TicketStates.WAITING_IDENTITY_CHOICE,
)
async def ticket_identity_choice_handler(message: Message):
    from bots.vk.bot import vk_bot

    text = (message.text or "").strip()
    if text.lower() in [c.lower() for c in COMMANDS_CANCEL]:
        await cancel_handler(message)
        return

    if text not in COMMANDS_ANONYMOUS_STAY:
        await message.answer(
            "Пожалуйста, выберите один из вариантов на клавиатуре или нажмите «Отмена»:",
            keyboard=build_anonymous_choice_keyboard(),
        )
        return

    keep_identity = text == COMMAND_REVEAL_IDENTITY
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

    # SEC-08: не более 3 обращений за 5 минут от одного VK ID.
    if await _is_ticket_rate_limited(message.from_id):
        await message.answer(
            "Слишком много обращений подряд.\n\n"
            "Лимит: не более 3 обращений за 5 минут. Пожалуйста, подождите "
            "несколько минут и выберите режим обращения снова — описание сохранено.",
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

        if ticket.user_id is not None and ticket.id is not None:
            local_num = await get_user_ticket_local_number(ticket.user_id, ticket.id)
        else:
            local_num = 1

        department_name = department or "Без отдела (Общий)"
        answer_tail = (
            "Администратор сможет ответить вам в этом диалоге."
            if keep_identity
            else "VK ID не сохранён. Ответ через VK на это обращение не отправляется."
        )
        await message.answer(
            f"Ваше обращение принято.\n\n"
            f"Номер обращения: #{local_num}\n"
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


# === Раздел «Партнёрство» ===


@student_labeler.private_message(text=COMMANDS_PARTNERSHIP)
async def partnership_handler(message: Message):
    """Раздел партнёрства: запрос предложения от пользователя."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.set(
        message.from_id,
        PartnershipStates.WAITING_PROPOSAL,
    )
    await message.answer(
        "🤝 Партнёрство со Студенческим советом\n\n"
        "Мы всегда открыты к новым проектам, идеям и сотрудничеству!\n\n"
        "Пожалуйста, напишите о себе (компания/организация/проект, ваши контакты) "
        "и в чём заключается ваше предложение о сотрудничестве.\n\n"
        "Нажмите «Отмена», если хотите вернуться в главное меню.",
        keyboard=build_cancel_keyboard(),
    )


@student_labeler.private_message(state=PartnershipStates.WAITING_PROPOSAL)
async def partnership_proposal_handler(message: Message):
    """Обработка предложения о партнёрстве и сохранение в базу данных."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    text = (message.text or "").strip()
    if text.lower() in [c.lower() for c in COMMANDS_CANCEL]:
        await cancel_handler(message)
        return

    if len(text) < 10:
        await message.answer(
            "Пожалуйста, расскажите о вашем предложении подробнее (минимум 10 символов).\n"
            "Или нажмите «Отмена» для выхода в меню.",
            keyboard=build_cancel_keyboard(),
        )
        return

    try:
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.vk_id == message.from_id))
            user_name = user.full_name if user and user.full_name else f"id{message.from_id}"
            req = PartnershipRequest(
                vk_id=message.from_id,
                user_name=user_name,
                proposal_text=text,
                status="new",
            )
            session.add(req)
            await session.commit()

        await message.answer(
            "Спасибо за ваше предложение! Мы с вами свяжемся в ближайшее время.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
    except Exception:
        logger.exception("Ошибка при сохранении заявки на партнёрство")
        await message.answer(
            "Произошла ошибка при отправке предложения. Пожалуйста, попробуйте позже.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
    finally:
        await vk_bot.state_dispenser.delete(message.from_id)


# Рендерер пагинации списка «Мои заявки» (ЭТАП 4.1 / B4)
register_renderer(KIND_MY_TICKETS, _render_my_tickets_page)

# Алиасы для обратной совместимости
student_reply_handler = ticket_reply_handler
ask_anonymous_handler = anonymous_section_start
identity_choice_handler = ticket_identity_choice_handler
question_handler = question_section_start
