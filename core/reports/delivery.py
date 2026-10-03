"""Каналы доставки сформированных отчётов."""

import logging
from datetime import UTC, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from core.config import settings

logger = logging.getLogger(__name__)


async def send_report_email(report_bytes: bytes, filename: str) -> None:
    """Отправить отчёт по email через SMTP."""
    if not settings.SMTP_HOST or not settings.REPORT_EMAILS:
        logger.info("SMTP не настроен, email-рассылка пропущена")
        return

    message = MIMEMultipart()
    message["Subject"] = f"Отчёт студенческого бота за {datetime.now(UTC):%d.%m.%Y}"
    message["From"] = settings.SMTP_FROM
    message["To"] = ", ".join(settings.REPORT_EMAILS)
    message.attach(MIMEText("Ежедневный отчёт во вложении.", "plain", "utf-8"))

    attachment = MIMEApplication(
        report_bytes,
        _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    message.attach(attachment)

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        start_tls=True,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        timeout=30,
    )
    logger.info("Отчёт отправлен по email: %s", ", ".join(settings.REPORT_EMAILS))
