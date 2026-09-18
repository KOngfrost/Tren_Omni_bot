"""Хендлеры формирования и отправки отчётов."""

import asyncio
import logging
import re
from datetime import datetime, timedelta

from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import _REPORT_PERIOD_PATTERN, ReportStates, _main_keyboard_for
from bots.vk.keyboards import build_admin_cancel_keyboard, build_admin_keyboard
from core.bot_core import BotCore
from core.commands import (
    COMMANDS_REPORT,
    COMMANDS_REPORT_BY_DATE,
    COMMANDS_REPORT_BY_PERIOD,
)
from core.reporting import (
    build_daily_report,
    get_app_tz,
    get_report_for_date,
    get_report_for_period,
    parse_report_date,
    send_report_to_vk,
)

logger = logging.getLogger(__name__)
reports_labeler = BotLabeler()


@reports_labeler.private_message(text=COMMANDS_REPORT)
async def report_handler(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        from bots.vk.common import _main_keyboard_for

        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    try:
        report_date = datetime.now(get_app_tz()) - timedelta(days=1)
        data = await get_report_for_date(report_date.date())
        # openpyxl — синхронная ресурсоёмкая библиотека: выносим в фоновый поток,
        # чтобы не блокировать event loop (Ошибка #11).
        report_bytes = await asyncio.to_thread(build_daily_report, data, report_date)
        filename = f"report_{report_date:%Y-%m-%d}.xlsx"
        from bots.vk.bot import vk_bot

        await send_report_to_vk(
            vk_bot.api,
            message.from_id,
            report_bytes,
            filename,
        )
        await BotCore.log_action(user, "report_generated", f"Сформирован отчёт {filename}")
    except Exception:
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


@reports_labeler.private_message(text=COMMANDS_REPORT_BY_DATE)
async def report_by_date(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        from bots.vk.common import _main_keyboard_for

        await message.answer(
            "Эта команда доступна только администраторам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.set(message.from_id, ReportStates.WAITING_DATE)
    await message.answer(
        "Введите дату для отчета в формате ДД.ММ.ГГГГ (например: 31.08.2026):",
        keyboard=build_admin_cancel_keyboard(),
    )


@reports_labeler.private_message(
    RegexRule(r"^\d{2}\.\d{2}\.\d{4}$"), state=ReportStates.WAITING_DATE
)
async def report_by_date_input(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        from bots.vk.bot import vk_bot

        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    parsed = parse_report_date(message.text)
    if parsed is None:
        await message.answer(
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ (например: 31.08.2026)"
        )
        return

    from bots.vk.bot import vk_bot

    try:
        data = await get_report_for_date(parsed)
        report_dt = datetime.combine(parsed, datetime.min.time(), tzinfo=get_app_tz())
        report_bytes = await asyncio.to_thread(build_daily_report, data, report_dt)
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
        import logging

        logging.getLogger(__name__).exception("Ошибка при формировании отчёта по дате %s", parsed)
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Не удалось сформировать отчет за указанную дату.",
            keyboard=build_admin_keyboard(),
        )


@reports_labeler.private_message(text=COMMANDS_REPORT_BY_PERIOD)
async def report_by_period_handler(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        from bots.vk.common import _main_keyboard_for

        await message.answer(
            "Эта команда доступна только администраторам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.set(message.from_id, ReportStates.WAITING_DATE_FROM)
    await message.answer(
        "Введите диапазон дат через дефис в формате: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ\n"
        "Например: 01.08.2026 - 31.08.2026",
        keyboard=build_admin_cancel_keyboard(),
    )


@reports_labeler.private_message(
    RegexRule(_REPORT_PERIOD_PATTERN), state=ReportStates.WAITING_DATE_FROM
)
async def report_by_period_input(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        from bots.vk.bot import vk_bot

        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "У вас нет доступа к отчетам.", keyboard=await _main_keyboard_for(message.from_id)
        )
        return

    match = re.match(_REPORT_PERIOD_PATTERN, message.text or "")
    if not match:
        await message.answer(
            "Неверный формат. Введите диапазон: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ (например: 01.08.2026 - 31.08.2026)"
        )
        return

    date_from_str, date_to_str = match.group(1), match.group(2)
    date_from = parse_report_date(date_from_str)
    date_to = parse_report_date(date_to_str)

    if not date_from or not date_to:
        await message.answer("Неверный формат даты. Используйте ДД.ММ.ГГГГ (например: 31.08.2026)")
        return

    if date_from > date_to:
        await message.answer("Дата начала не может быть позже даты окончания.")
        return

    MAX_PERIOD_DAYS = 90
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        await message.answer(
            f"Слишком большой период (максимум {MAX_PERIOD_DAYS} дней).\n"
            "Пожалуйста, укажите меньший диапазон для выгрузки."
        )
        return

    from bots.vk.bot import vk_bot

    try:
        data = await get_report_for_period(date_from, date_to)
        report_dt = datetime.combine(date_from, datetime.min.time(), tzinfo=get_app_tz())
        # openpyxl — синхронная ресурсоёмкая библиотека: выносим в фоновый поток,
        # чтобы не блокировать event loop (Ошибка #11).
        report_bytes = await asyncio.to_thread(build_daily_report, data, report_dt)
        filename = f"report_{date_from:%Y-%m-%d}_to_{date_to:%Y-%m-%d}.xlsx"
        await send_report_to_vk(
            vk_bot.api,
            message.from_id,
            report_bytes,
            filename,
        )
        await BotCore.log_action(
            user,
            "report_generated",
            f"Сформирован отчёт за период {date_from} — {date_to}",
        )
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            f"Отчет за период с {date_from:%d.%m.%Y} по {date_to:%d.%m.%Y} сформирован и отправлен.",
            keyboard=build_admin_keyboard(),
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Ошибка при формировании отчёта за период %s — %s", date_from, date_to
        )
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Не удалось сформировать отчет за указанный период.",
            keyboard=build_admin_keyboard(),
        )


@reports_labeler.private_message(state=ReportStates.WAITING_DATE)
async def report_by_date_fallback(message: Message):
    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.delete(message.from_id)
    await message.answer("Неверный формат даты. Формирование отчета отменено.")


@reports_labeler.private_message(state=ReportStates.WAITING_DATE_FROM)
async def report_by_period_fallback(message: Message):
    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.delete(message.from_id)
    await message.answer("Неверный формат диапазона дат. Формирование отчета отменено.")


# Алиасы для обратной совместимости
report_by_date_start = report_by_date
report_date_received = report_by_date_input
report_by_period_start = report_by_period_handler
report_date_from_received = report_by_period_input
report_date_to_received = report_by_period_input
