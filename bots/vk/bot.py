import logging

from core.vk_compat import patch_vkbottle_logging

patch_vkbottle_logging()

from vkbottle import Bot, ErrorHandler
from vkbottle.bot import Message

from core.config import settings
from core.heartbeat import touch_heartbeat

from bots.vk.common import (
    ReportStates,
    TicketStates,
    _get_department_names,
    _main_keyboard_for,
    _main_reply_text,
    _operator_can_access,
)
from bots.vk.handlers.admin import (
    admin_labeler,
    admin_panel,
    admin_status_handler,
    admin_tickets_handler,
    regular_menu_handler,
)
from bots.vk.handlers.events import (
    events_handler,
    events_labeler,
    register_event_handler,
)
from bots.vk.handlers.faq import (
    faq_handler,
    faq_labeler,
    faq_node_handler,
)
from bots.vk.handlers.knowledge import (
    knowledge_base_handler,
    knowledge_handler,
    knowledge_labeler,
)
from bots.vk.handlers.reports import (
    report_by_date,
    report_by_date_start,
    report_by_period_handler,
    report_by_period_start,
    report_date_from_received,
    report_date_received,
    report_date_to_received,
    report_handler,
    reports_labeler,
)
from bots.vk.handlers.student import (
    anonymous_section_start,
    ask_anonymous_handler,
    cancel_handler,
    corporate_section,
    culture_section,
    housing_section,
    identity_choice_handler,
    information_section,
    my_tickets_handler,
    question_handler,
    question_section_start,
    start_handler,
    student_labeler,
    student_reply_handler,
    ticket_description_handler,
    ticket_details_handler,
    ticket_identity_choice_handler,
    ticket_reply_handler,
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

# Регистрация модульных обработчиков через BotLabeler
vk_bot.labeler.load(faq_labeler)
vk_bot.labeler.load(knowledge_labeler)
vk_bot.labeler.load(events_labeler)
vk_bot.labeler.load(student_labeler)
vk_bot.labeler.load(admin_labeler)
vk_bot.labeler.load(reports_labeler)


@vk_bot.on.private_message()
async def fallback_handler(message: Message):
    """Возвращает пользователя в основное меню для неизвестных сообщений."""
    touch_heartbeat()
    await message.answer(
        "Я не распознал команду. Выберите действие в меню:",
        keyboard=await _main_keyboard_for(message.from_id),
    )


__all__ = [
    "ReportStates",
    "TicketStates",
    "_get_department_names",
    "_handle_bot_error",
    "_main_keyboard_for",
    "_main_reply_text",
    "_operator_can_access",
    "admin_labeler",
    "admin_panel",
    "admin_status_handler",
    "admin_tickets_handler",
    "anonymous_section_start",
    "ask_anonymous_handler",
    "cancel_handler",
    "corporate_section",
    "culture_section",
    "events_handler",
    "events_labeler",
    "fallback_handler",
    "faq_handler",
    "faq_labeler",
    "faq_node_handler",
    "housing_section",
    "identity_choice_handler",
    "information_section",
    "knowledge_base_handler",
    "knowledge_handler",
    "knowledge_labeler",
    "my_tickets_handler",
    "question_handler",
    "question_section_start",
    "register_event_handler",
    "regular_menu_handler",
    "report_by_date",
    "report_by_date_start",
    "report_by_period_handler",
    "report_by_period_start",
    "report_date_from_received",
    "report_date_received",
    "report_date_to_received",
    "report_handler",
    "reports_labeler",
    "start_handler",
    "student_labeler",
    "student_reply_handler",
    "ticket_description_handler",
    "ticket_details_handler",
    "ticket_identity_choice_handler",
    "ticket_reply_handler",
    "vk_bot",
]
