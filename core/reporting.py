import asyncio
import contextlib
import logging
import os
import secrets
from datetime import UTC, date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from sqlalchemy import select
from vkbottle.tools.uploader import DocMessagesUploader

from core.config import settings
from core.database import async_session_maker
from core.models import Admin, TicketStatus, User, UserRole
from core.reports.daily import (
    DEFAULT_DEPTS,
    _DeptRef,
    _TicketRow,
    _UserRef,
    build_daily_report,
    get_app_tz,
)
from core.reports.period import (
    REPORT_CHUNK_SIZE,
    _fetch_report_data,
    get_report_for_date,
    get_report_for_period,
    is_report_already_sent,
    mark_report_sent,
)
from core.ticket_service import COMPLETED_STATUSES, status_label

logger = logging.getLogger(__name__)

# Dev-фоллбэк: список отделов по умолчанию. Используется ТОЛЬКО если в базе
# ещё нет ни одного отдела, чтобы отчёт формировался в пустой системе.
# В production отделы создаёт администратор через панель управления, и
# книга Excel получает по вкладке на каждый отдел из БД (Ошибка #16).


async def send_report_email(report_bytes: bytes, filename: str) -> None:
    """Отправляет отчёт по email через SMTP (асинхронно)."""
    if not settings.SMTP_HOST or not settings.REPORT_EMAILS:
        logger.info("SMTP не настроен, email-рассылка пропущена")
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"Отчёт студенческого бота за {datetime.now(UTC):%d.%m.%Y}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(settings.REPORT_EMAILS)

    msg.attach(MIMEText("Ежедневный отчёт во вложении.", "plain", "utf-8"))

    attachment = MIMEApplication(
        report_bytes,
        _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        start_tls=True,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        timeout=30,
    )

    logger.info("Отчёт отправлен по email: %s", ", ".join(settings.REPORT_EMAILS))


async def send_report_to_vk(api, admin_vk_id: int, report_bytes: bytes, filename: str) -> None:
    """Отправляет отчёт в VK как документ через встроенный uploader vkbottle."""
    if not admin_vk_id:
        raise ValueError("VK_REPORT_ADMIN_ID не настроен")
    if not report_bytes:
        raise ValueError("Сформированный файл отчёта пуст (0 байт)")

    import tempfile

    uploader = DocMessagesUploader(api)
    suffix = os.path.splitext(filename)[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(report_bytes)
        tmp_path = tmp.name

    try:
        try:
            attachment = await uploader.upload(
                file_source=tmp_path,
                peer_id=admin_vk_id,
                title=filename,
            )
        except Exception as e:
            logger.warning(
                "Первая попытка загрузки отчёта в VK (%s) не удалась: %s. Выполняется повторная попытка...",
                filename,
                e,
            )
            await asyncio.sleep(1.0)
            attachment = await uploader.upload(
                file_source=tmp_path,
                peer_id=admin_vk_id,
                title=filename,
            )

        await api.messages.send(
            peer_id=admin_vk_id,
            random_id=secrets.randbelow(2**31 - 1) + 1,
            message="Ежедневный отчёт.",
            attachment=attachment,
        )
    finally:
        if os.path.exists(tmp_path):
            with contextlib.suppress(Exception):
                os.remove(tmp_path)


async def get_superadmin_vk_ids() -> list[int]:
    """Возвращает VK ID всех суперадминистраторов с доступным VK ID."""
    async with async_session_maker() as session:
        result = await session.scalars(
            select(User.vk_id)
            .join(Admin, Admin.user_id == User.id)
            .where(Admin.role == UserRole.SUPERADMIN, User.vk_id.is_not(None))
            .distinct()
        )
        return [vk_id for vk_id in result if vk_id is not None]


def _seconds_until_report() -> float:
    """Секунды до ближайшего запуска отчёта (в часовом поясе APP_TIMEZONE)."""
    try:
        report_time = datetime.strptime(settings.REPORT_TIME, "%H:%M").time()
    except ValueError as error:
        raise ValueError("REPORT_TIME должен быть в формате HH:MM") from error

    tz = get_app_tz()
    now = datetime.now(tz)
    next_report = datetime.combine(now.date(), report_time, tzinfo=tz)
    if next_report <= now:
        next_report += timedelta(days=1)
    return (next_report - now).total_seconds()


def parse_report_date(text: str) -> date | None:
    """Парсит дату из строки формата ДД.ММ.ГГГГ или ДД.ММ.ГГ.

    Возвращает date или None, если формат неверный.
    """
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def _run_report(api, admin_vk_ids: list[int], report_date: datetime) -> bool:
    """Сформировать и отправить ежедневный отчет всем суперадминам и по email.

    Возвращает True при успешной отправке (или если уже отправлен ранее).
    """
    report_day = report_date.date()

    if await is_report_already_sent(report_day):
        logger.info("Отчёт за %s уже отправлен ранее — пропуск", report_day)
        return True

    data = await get_report_for_date(report_day)
    # openpyxl — синхронная ресурсоёмкая библиотека: выносим в фоновый поток,
    # чтобы не блокировать event loop (Ошибка #11).
    report_bytes = await asyncio.to_thread(build_daily_report, data, report_date)
    filename = f"report_{report_date:%Y-%m-%d}.xlsx"

    # Отправка в VK — отдельный try/except
    vk_failed = False
    sent_any_vk = False
    email_configured = bool(settings.SMTP_HOST and settings.REPORT_EMAILS)
    delivery_configured = bool(admin_vk_ids or email_configured)
    if not delivery_configured:
        logger.error("Отчёт не отправлен: не настроен ни один канал доставки")
        return False

    from core.redis_client import get_redis_client

    for admin_vk_id in admin_vk_ids:
        # Проверяем дедупликацию по конкретному администратору:
        # если этот админ уже получил отчёт за report_day, не шлём повторно
        recip_key = f"oss_bot:report_vk_sent:{report_day}:{admin_vk_id}"
        redis = None
        try:
            redis = await get_redis_client()
            if redis and await redis.get(recip_key):
                logger.info(
                    "Отчёт за %s уже был отправлен VK-администратору %s ранее — пропуск",
                    report_day,
                    admin_vk_id,
                )
                sent_any_vk = True
                continue
        except Exception:
            redis = None

        try:
            await send_report_to_vk(api, admin_vk_id, report_bytes, filename)
            sent_any_vk = True
            if redis:
                with contextlib.suppress(Exception):
                    await redis.set(recip_key, "1", ex=86400 * 2)
        except Exception:
            logger.exception("Не удалось отправить отчёт в VK администратору %s", admin_vk_id)
            vk_failed = True

    # Отправка по email — отдельный try/except
    email_failed = False
    if email_configured:
        try:
            await send_report_email(report_bytes, filename)
        except Exception:
            logger.exception("Не удалось отправить отчёт по email")
            email_failed = True

    # Если отчёт был успешно доставлен хотя бы одному получателю или в один канал:
    # фиксируем отправку немедленно, чтобы предотвратить циклический спам
    if sent_any_vk or (email_configured and not email_failed):
        final_status = "sent" if not (vk_failed or email_failed) else "partial"
        await mark_report_sent(report_day, status=final_status)
        logger.info(
            "Ежедневный отчёт за %s зафиксирован со статусом '%s'",
            report_day,
            final_status,
        )
        return True

    return False


async def _report_loop(api) -> None:
    tz = get_app_tz()
    retry_count = 0
    max_retries = 3

    while True:
        try:
            now = datetime.now(tz)
            try:
                target_time = datetime.strptime(settings.REPORT_TIME, "%H:%M").time()
            except ValueError:
                target_time = datetime.strptime("09:00", "%H:%M").time()

            yesterday = now.date() - timedelta(days=1)
            scheduled_today = datetime.combine(now.date(), target_time, tzinfo=tz)

            # Если время отчёта на сегодня уже наступило, но отчёт за вчера ещё не отправлен
            if now >= scheduled_today and not await is_report_already_sent(yesterday):
                report_dt = datetime.combine(yesterday, target_time, tzinfo=tz)
                admin_ids = await get_superadmin_vk_ids()
                success = await _run_report(api, admin_ids, report_dt)
                if not success:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.error(
                            "Сбой доставки отчёта за %s: исчерпано максимальное число попыток (%s). "
                            "Отчёт фиксируется как завершённый для предотвращения спама.",
                            yesterday,
                            max_retries,
                        )
                        await mark_report_sent(yesterday, status="failed")
                        retry_count = 0
                    else:
                        retry_delay = 300 * retry_count
                        logger.warning(
                            "Сбой доставки отчёта за %s (попытка %s/%s). Повтор через %s сек...",
                            yesterday,
                            retry_count,
                            max_retries,
                            retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                else:
                    retry_count = 0

            # Отчёт за вчера отправлен или время отчёта ещё не наступило —
            # спим до следующего планового времени (порциями не более часа)
            delay = _seconds_until_report()
            sleep_chunk = min(max(30.0, delay), 3600.0)
            await asyncio.sleep(sleep_chunk)
        except Exception:
            logger.exception("Непредвиденная ошибка в цикле ежедневных отчётов")
            await asyncio.sleep(60)


def start_report_scheduler(api) -> asyncio.Task:
    """Запускает asyncio-планировщик ежедневных отчётов."""
    task = asyncio.create_task(_report_loop(api))
    return task


__all__ = [
    "COMPLETED_STATUSES",
    "DEFAULT_DEPTS",
    "REPORT_CHUNK_SIZE",
    "TicketStatus",
    "_DeptRef",
    "_TicketRow",
    "_UserRef",
    "_fetch_report_data",
    "_report_loop",
    "_run_report",
    "_seconds_until_report",
    "build_daily_report",
    "get_app_tz",
    "get_report_for_date",
    "get_report_for_period",
    "get_superadmin_vk_ids",
    "is_report_already_sent",
    "mark_report_sent",
    "parse_report_date",
    "send_report_email",
    "send_report_to_vk",
    "start_report_scheduler",
    "status_label",
]
