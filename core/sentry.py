"""Инициализация Sentry SDK для мониторинга ошибок в боте и веб-панели."""

import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


def _scrub_sentry_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Очищает чувствительные данные (пароли, токены, куки, персональные данные) перед отправкой в Sentry."""
    # Очистка заголовков запроса
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for k in list(headers.keys()):
                if k.lower() in ("cookie", "authorization", "x-csrf-token", "proxy-authorization"):
                    headers[k] = "[FILTERED]"

        # Очистка тела запроса
        data = request.get("data")
        if isinstance(data, dict):
            for k in list(data.keys()):
                if any(s in k.lower() for s in ("password", "code", "token", "secret", "csrf_token")):
                    data[k] = "[FILTERED]"

    # Очистка данных пользователя
    user = event.get("user")
    if isinstance(user, dict):
        # Удаляем персональные данные, оставляя только обезличенный id/role
        user.pop("email", None)
        user.pop("ip_address", None)

    return event


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
            send_default_pii=False,
            before_send=_scrub_sentry_event,
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
