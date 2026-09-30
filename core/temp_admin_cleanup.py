"""
Автоудаление истёкших временных учётных записей.

Временный администратор создаётся на ограниченный срок (expires_at) и не
имеет привязки к VK-админу. После истечения срока он уже не может войти,
но сама строка продолжает занимать место в таблице web_users и засорять
карточки — её нужно убирать автоматически.

Стратегия:
- запись сначала деактивируется (is_active=False) — мгновенный запрет входа;
- затем удаляется из БД. Удаление выполняется с задержкой (grace), чтобы
  администратор увидел в логах, что учётка была, даже если срок истёк
  минуту назад;
- каждое удаление фиксируется в журнале аудита: история не должна зависеть
  от существования аккаунта.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from core.audit import ACTOR_SYSTEM, record_action
from core.models import WebUser
from core.time_utils import ensure_aware, now_app_tz

logger = logging.getLogger(__name__)

# Задержка между деактивацией и физическим удалением.
# Нужна, чтобы в журнале осталась запись о том, ЧТО было удалено и почему.
DEFAULT_GRACE_MINUTES = 0


def _session_maker():
    """Получить актуальный session maker.

    Обращаемся к core.database в момент вызова, а не к импортированному
    имени: так подмена соединения в тестах (monkeypatch по атрибуту модуля)
    применяется автоматически. Иначе фоновый цикл в тестах попытался бы
    подключиться к боевому PostgreSQL.
    """
    from core import database

    return database.async_session_maker()


async def cleanup_expired_temporary_admins(
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
) -> int:
    """Удалить истёкшие временные учётные записи. Возвращает число удалённых.

    Затрагивает ТОЛЬКО записи без привязки к VK-администратору
    (admin_id IS NULL) — постоянные администраторы с ограниченным сроком
    не трогаем, их срок обрабатывает логика входа.

    События аудита накапливаются в списке и пишутся ПОСЛЕ закрытия рабочей
    сессии: record_action() открывает собственное соединение, а держать одно
    открытым, пока открывается второе, — прямой путь к взаимной блокировке
    пула (в тестах SQLite держит единственное соединение StaticPool).
    """
    now = now_app_tz()
    grace_cutoff = now - timedelta(minutes=max(0, grace_minutes))
    removed = 0
    audit_events: list[dict[str, Any]] = []

    try:
        async with _session_maker() as session:
            # 1) Деактивируем всё истёкшее (мгновенный запрет входа).
            expired = list(
                (
                    await session.scalars(
                        select(WebUser).where(
                            WebUser.admin_id.is_(None),
                            WebUser.expires_at.is_not(None),
                            WebUser.expires_at <= now,
                        )
                    )
                ).all()
            )
            to_delete = []
            for user in expired:
                # expires_at может прийти из БД «naive» (SQLite) — приводим
                # к aware, иначе сравнение с now_app_tz() бросит TypeError.
                expires_at = ensure_aware(user.expires_at)
                if not user.is_active:
                    # Уже деактивировано ранее — пора удалять, если прошёл grace.
                    if expires_at and expires_at <= grace_cutoff:
                        to_delete.append(user)
                    continue
                user.is_active = False
                logger.info(
                    "Временная учётная запись истекла и деактивирована: %s (id=%s)",
                    user.username,
                    user.id,
                )
                audit_events.append(
                    {
                        "action": "Авто-блокировка временного администратора",
                        "details": (
                            f"Срок действия истёк "
                            f"{expires_at.isoformat() if expires_at else '?'}, "
                            f"логин={user.username}, web_user_id={user.id}"
                        ),
                        "actor_type": ACTOR_SYSTEM,
                        "actor_name": "system:temp-admin-cleanup",
                        "is_mutation": True,
                    }
                )
                if grace_minutes == 0:
                    to_delete.append(user)

            # 2) Удаляем те, у кого grace истёк.
            for user in to_delete:
                username = user.username
                user_id = user.id
                await session.delete(user)
                removed += 1
                logger.info(
                    "Временная учётная запись удалена после истечения: %s (id=%s)",
                    username,
                    user_id,
                )
                audit_events.append(
                    {
                        "action": "Авто-удаление временного администратора",
                        "details": f"Удалена учётная запись {username} (web_user_id={user_id})",
                        "actor_type": ACTOR_SYSTEM,
                        "actor_name": "system:temp-admin-cleanup",
                        "is_mutation": True,
                    }
                )

            if expired:
                await session.commit()
    except Exception:
        # Откатываем всё: если изменения не дошли до БД, то и аудит о них
        # писать нельзя — иначе журнал соврёт оператору.
        logger.warning("Не удалось выполнить очистку временных админов", exc_info=True)
        return removed

    for event in audit_events:
        await record_action(**event)

    return removed


async def temp_admin_cleanup_loop() -> None:
    """Фоновый цикл автоудаления истёкших временных учётных записей."""
    from core.config import settings

    interval = max(30, int(settings.TEMP_ADMIN_CLEANUP_INTERVAL_SECONDS))
    while True:
        try:
            await cleanup_expired_temporary_admins()
        except Exception:
            logger.exception("Ошибка цикла очистки временных админов")
        await asyncio.sleep(interval)


__all__ = [
    "DEFAULT_GRACE_MINUTES",
    "cleanup_expired_temporary_admins",
    "temp_admin_cleanup_loop",
]
