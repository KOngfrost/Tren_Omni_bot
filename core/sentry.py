"""Инициализация Sentry SDK для мониторинга ошибок в боте и веб-панели."""

import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


def init_sentry(service_name: str = "app") -> bool:
    """Инициализировать Sentry, если задан SENTRY_DSN.

    Безопасен при отсутствии DSN или сбое инициализации — приложение
    продолжает работать штатно.
    """
    if not settings.SENTRY_DSN:
        logger.debug("SENTRY_DSN не задан — мониторинг Sentry отключён")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        integrations: list[Any] = [
            FastApiIntegration(),
            SqlalchemyIntegration(),
            AsyncioIntegration(),
        ]

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            release=f"student-bot@{settings.PROJECT_VERSION}",
            integrations=integrations,
        )
        sentry_sdk.set_tag("service", service_name)
        logger.info(
            "Sentry SDK успешно инициализирован для сервиса '%s' (env: %s)",
            service_name,
            settings.SENTRY_ENVIRONMENT,
        )
        return True
    except Exception as e:
        logger.warning("Не удалось инициализировать Sentry SDK: %s", e)
        return False
