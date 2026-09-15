"""Страж старта (startup guard): единая точка проверок перед запуском сервиса.

Почему это нужно
----------------
Раньше проверки конфигурации были разбросаны по точкам входа и частично
срабатывали только при первом запросе. Теперь и веб-панель, и бот вызывают
`enforce_startup_security()` на старте процесса:

* жёсткие проверки прод-конфигурации (`settings.ensure_production_config`);
* проверки bootstrap-режима и placeholder-секретов (`core.bootstrap_guard`);
* в `production` любая ошибка стража прерывает запуск (fail-closed);
* в разработке ошибки превращаются в WARNING, чтобы не ломать локальную разработку.

Дополнительно `startup_summary()` отдаёт компактную сводку для `/health`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.bootstrap_guard import evaluate_config

if TYPE_CHECKING:
    from core.bootstrap_guard import GuardReport
    from core.config import Settings

logger = logging.getLogger(__name__)


def _report_log_lines(report: GuardReport, settings: Settings) -> list[str]:
    component = "production" if settings.IS_PRODUCTION else settings.APP_ENV
    lines = [f"Startup guard [{component}]"]
    lines += [f"  ERROR: {item}" for item in report.errors]
    lines += [f"  WARNING: {item}" for item in report.warnings]
    return lines


def enforce_startup_security(settings: Settings, *, component: str = "web") -> GuardReport:
    """Выполнить проверки безопасности. Возвращает отчёт; в прод бросает RuntimeError.

    Args:
        settings: экземпляр конфигурации.
        component: "web" или "bot" — только для сообщений в лог.
    """
    # 1. Жёсткие проверки конфигурации (БД, секрет сессий). В прод бросает RuntimeError.
    settings.ensure_production_config()

    # 2. Проверки bootstrap-режима и placeholder-секретов.
    report = evaluate_config(settings)

    for line in _report_log_lines(report, settings):
        logger.warning("%s | %s", component, line)

    if report.errors and settings.IS_PRODUCTION:
        raise RuntimeError(
            "Страж старта заблокировал запуск ("
            + component
            + "):\n"
            + "\n".join(f"  - {item}" for item in report.errors)
        )

    return report


def startup_summary(settings: Settings) -> dict[str, object]:
    """Компактная сводка стража для `/health` (без обращений к базе)."""
    report = evaluate_config(settings)
    return {
        "config_ok": report.ok,
        "placeholder_secrets": list(report.details.get("placeholder_secrets", [])),
        "weak_secrets": list(report.details.get("weak_secrets", [])),
        "bootstrap_credentials_active": bool(
            report.details.get("bootstrap_credentials_active", False)
        ),
        "two_factor_channel_ready": bool(report.details.get("two_factor_channel_ready", False)),
    }


__all__ = ["enforce_startup_security", "startup_summary"]
