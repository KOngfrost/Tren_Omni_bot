"""Страж bootstrap-режима: временный доступ по учётным данным из .env (раздел 5 ТЗ).

Доступ по `WEB_ADMIN_USERNAME` + `WEB_ADMIN_PASSWORD` из `.env` считается
**bootstrap-доступом**: он нужен, чтобы зайти в панель при пустой базе, но
постоянным не является. Постоянный доступ — это записи `web_users` с ролями.

Модуль умеет:
1. `bootstrap_credentials_active()` — активен ли bootstrap-доступ сейчас.
2. `bootstrap_mode_status()` — bootstrap-режим или нет (нет ни одного постоянного
   активного суперпользователя в базе).
3. `find_placeholder_secrets()` — детект placeholder/дефолтных секретов.
4. `evaluate_config()` — сводный отчёт для стража старта (`core.startup_guard`).

В production bootstrap-доступ допустим только как временная мера и обязан быть
виден: `/health` (`bootstrap_mode`), баннер дашборда, WARNING в логах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from core.models import WebRole, WebUser

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.config import Settings

# Значения-заглушки, которые нельзя оставлять в production.
PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "change_me",
    "changeme",
    "change-me",
    "replace_me",
    "replace-me",
    "your_",
    "your-",
    "yoursecret",
    "example",
    "dummy",
    "placeholder",
    "secret123",
    "password123",
    "qwerty",
    "admin123",
    "12345678",
)

# Проверяемые секреты: имя поля конфигурации -> минимальная длина.
SECRET_REQUIREMENTS: dict[str, int] = {
    "SESSION_SECRET_KEY": 32,
}

# Минимальная длина bootstrap-пароля, который сознательно вшивают в .env.
BOOTSTRAP_PASSWORD_MIN_LENGTH = 12


@dataclass(frozen=True)
class GuardReport:
    """Итог проверки конфигурации: `errors` блокируют запуск, `warnings` — нет."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _looks_placeholder(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in PLACEHOLDER_TOKENS)


def find_placeholder_secrets(settings: Settings) -> list[str]:
    """Поля конфигурации со значениями-заглушками (пусто = всё чисто)."""
    found = [
        name
        for name in SECRET_REQUIREMENTS
        if _looks_placeholder(str(getattr(settings, name, "") or ""))
    ]
    if _looks_placeholder(str(getattr(settings, "WEB_ADMIN_PASSWORD", "") or "")):
        found.append("WEB_ADMIN_PASSWORD")
    return found


def weak_secrets(settings: Settings) -> list[str]:
    """Секреты короче рекомендуемой длины (предупреждение, не ошибка)."""
    weak: list[str] = []
    for name, min_length in SECRET_REQUIREMENTS.items():
        value = str(getattr(settings, name, "") or "")
        if value and not _looks_placeholder(value) and len(value) < min_length:
            weak.append(name)
    return weak


def bootstrap_credentials_active(settings: Settings) -> bool:
    """Задан ли bootstrap-доступ (логин и пароль в конфигурации)."""
    username = (getattr(settings, "WEB_ADMIN_USERNAME", "") or "").strip()
    password = (getattr(settings, "WEB_ADMIN_PASSWORD", "") or "").strip()
    return bool(username and password)


def bootstrap_credentials_weak(settings: Settings) -> bool:
    """Слишком простой bootstrap-пароль: короче 12 символов или placeholder."""
    password = str(getattr(settings, "WEB_ADMIN_PASSWORD", "") or "")
    if not password:
        return False
    return len(password) < BOOTSTRAP_PASSWORD_MIN_LENGTH or _looks_placeholder(password)


def two_factor_channel_ready(settings: Settings) -> bool:
    """Есть ли канал доставки OTP: явный VK ID bootstrap-админа или админ отчётов."""
    if int(getattr(settings, "WEB_ADMIN_2FA_VK_ID", 0) or 0) > 0:
        return True
    return int(getattr(settings, "VK_REPORT_ADMIN_ID", 0) or 0) > 0


async def bootstrap_mode_status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Сводка по bootstrap-режиму для `/health` и баннера дашборда.

    `bootstrap_mode == True`, если постоянных активных суперпользователей в базе
    нет, а bootstrap-доступ из `.env` при этом задан.
    """
    stmt = select(WebUser.username).where(
        WebUser.role == WebRole.SUPERADMIN,
        WebUser.is_active.is_(True),
    )
    permanent = sorted({str(row) for row in (await session.execute(stmt)).scalars().all()})
    username = (getattr(settings, "WEB_ADMIN_USERNAME", "") or "").strip()
    bootstrap_active = bootstrap_credentials_active(settings)
    # Bootstrap-вход реально сработает, только если в базе нет постоянного
    # пользователя с таким же именем (иначе выигрывает web_users).
    shadowed = bool(username) and username in permanent
    return {
        "bootstrap_mode": bool(bootstrap_active and not permanent),
        "bootstrap_login_enabled": bool(bootstrap_active and not shadowed),
        "bootstrap_username": username or None,
        "bootstrap_credentials_weak": bootstrap_credentials_weak(settings),
        "permanent_superadmins": len(permanent),
        "two_factor_channel_ready": two_factor_channel_ready(settings),
    }


def evaluate_config(settings: Settings) -> GuardReport:
    """Проверить конфигурацию запуска без обращения к базе."""
    errors: list[str] = []
    warnings: list[str] = []

    placeholders = find_placeholder_secrets(settings)
    allow_placeholder = bool(getattr(settings, "WEB_BOOTSTRAP_ALLOW_PLACEHOLDER_SECRETS", False))
    if placeholders:
        message = (
            "Placeholder-значения в конфигурации: "
            + ", ".join(placeholders)
            + '. Сгенерируйте стойкие значения: python -c "import secrets; print(secrets.token_urlsafe(64))".'
        )
        if bool(getattr(settings, "IS_PRODUCTION", False)):
            errors.append(message)
        elif allow_placeholder:
            warnings.append(
                message + " Запуск разрешён локально (WEB_BOOTSTRAP_ALLOW_PLACEHOLDER_SECRETS)."
            )
        else:
            warnings.append(message)

    weak = weak_secrets(settings)
    if weak:
        warnings.append("Секреты короче рекомендуемой длины: " + ", ".join(weak))

    if bootstrap_credentials_active(settings):
        warnings.append(
            "Активен bootstrap-доступ из .env (WEB_ADMIN_USERNAME/WEB_ADMIN_PASSWORD). "
            "После создания постоянного суперпользователя в web_users удалите его из конфигурации "
            "или выставите BOOTSTRAP_ALLOWED=false.",
        )
    if bootstrap_credentials_weak(settings):
        warnings.append(
            f"Bootstrap-пароль слабый: короче {BOOTSTRAP_PASSWORD_MIN_LENGTH} символов либо placeholder.",
        )
    if bootstrap_credentials_active(settings) and not two_factor_channel_ready(settings):
        warnings.append(
            "Не задан канал доставки 2FA-кода (WEB_ADMIN_2FA_VK_ID или VK_REPORT_ADMIN_ID): "
            "bootstrap-вход нельзя подтвердить вторым фактором.",
        )

    return GuardReport(
        errors=tuple(errors),
        warnings=tuple(warnings),
        details={
            "placeholder_secrets": placeholders,
            "weak_secrets": weak,
            "bootstrap_credentials_active": bootstrap_credentials_active(settings),
            "two_factor_channel_ready": two_factor_channel_ready(settings),
        },
    )


__all__ = [
    "BOOTSTRAP_PASSWORD_MIN_LENGTH",
    "GuardReport",
    "bootstrap_credentials_active",
    "bootstrap_credentials_weak",
    "bootstrap_mode_status",
    "evaluate_config",
    "find_placeholder_secrets",
    "two_factor_channel_ready",
    "weak_secrets",
]
