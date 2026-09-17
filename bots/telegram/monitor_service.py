"""Фоновая служба периодического мониторинга контейнеров и отправки алертов."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bots.telegram.system_metrics import get_system_metrics

if TYPE_CHECKING:
    from aiogram import Bot

    from bots.telegram.docker_client import DockerClient
    from core.config import Settings

logger = logging.getLogger(__name__)


class MonitorService:
    """Сервис фонового отслеживания здоровья контейнеров и сервера."""

    def __init__(
        self,
        bot: Bot,
        admin_id: int,
        docker_client: DockerClient,
        check_interval: int = 30,
        alerts_enabled: bool = True,
        web_health_url: str = "http://web-admin:8000/health",
        settings: Settings | None = None,
        backup_interval_seconds: int = 86400,
    ):
        self.bot = bot
        self.admin_id = admin_id
        self.docker_client = docker_client
        self.check_interval = max(5, check_interval)
        self.alerts_enabled = alerts_enabled
        self.web_health_url = web_health_url
        self.settings = settings
        self.backup_interval_seconds = backup_interval_seconds

        # Предыдущие состояния контейнеров: name -> is_bad (bool)
        self._previous_states: dict[str, bool] = {}
        # Таймстемпы последних алертов по ресурсам (для предотвращения спама)
        self._last_resource_alert_time: float = 0.0
        # Таймстемп последнего планового бэкапа
        self._last_backup_time: float = 0.0
        self._is_running = False

    async def start(self) -> None:
        """Запустить бесконечный цикл мониторинга."""
        self._is_running = True
        logger.info(
            "Монитор запущен: интервал=%dc, админ ID=%d, алерты=%s",
            self.check_interval,
            self.admin_id,
            self.alerts_enabled,
        )

        while self._is_running:
            try:
                if self.alerts_enabled and self.admin_id > 0:
                    await self.check_all()
            except asyncio.CancelledError:
                logger.info("Цикл мониторинга отменён.")
                break
            except Exception as e:
                logger.error("Непредвиденная ошибка в цикле мониторинга: %s", e)

            await asyncio.sleep(self.check_interval)

    def stop(self) -> None:
        """Остановить мониторинг."""
        self._is_running = False

    async def check_all(self) -> None:
        """Выполнить один такт проверки контейнеров, ресурсов и планового бэкапа."""
        await self._check_containers()
        await self._check_system_resources()
        await self._check_scheduled_backup()

    async def _check_containers(self) -> None:
        """Проверить статусы контейнеров и отправить уведомления о сбоях/восстановлении."""
        containers = await self.docker_client.list_containers(all=True)
        if not containers:
            return

        for c in containers:
            # Игнорируем сервисы однократных миграций (Alembic)
            if "migrate" in c.name and "Exited (0)" in c.status:
                continue

            # Признак сбоя: unhealthy, не работает (кроме штатного Exited 0) или цикличный рестарт
            is_unhealthy = c.health == "unhealthy"
            is_stopped = not c.is_running and "Exited (0)" not in c.status
            is_restarting = "restarting" in c.status.lower()
            is_bad = is_unhealthy or is_stopped or is_restarting

            prev_bad = self._previous_states.get(c.name)

            # 1. Первый запуск: просто запоминаем состояние
            if prev_bad is None:
                self._previous_states[c.name] = is_bad
                # Если уже сбоит при старте монитора — отправим алерт
                if is_bad:
                    await self._send_failure_alert(c.name, c.status)
                continue

            # 2. Переход: всё было хорошо -> произошёл сбой
            if not prev_bad and is_bad:
                self._previous_states[c.name] = True
                await self._send_failure_alert(c.name, c.status)

            # 3. Переход: был сбой -> сервис восстановился
            elif prev_bad and not is_bad:
                self._previous_states[c.name] = False
                await self._send_recovery_alert(c.name, c.status)

    async def _check_system_resources(self) -> None:
        """Проверить критическую или повышенную нагрузку на диск и оперативную память."""
        now = time.time()
        # Лимит повтора алертов по ресурсам: не чаще 1 раза в 15 минут
        if now - self._last_resource_alert_time < 900:
            return

        metrics = get_system_metrics()
        alerts: list[str] = []

        if metrics["disk_percent"] >= 92.0:
            alerts.append(
                f"🚨 <b>Критически мало места на диске!</b> Занято: <b>{metrics['disk_percent']}%</b> (свободно {metrics['disk_free']})"
            )
        elif metrics["disk_percent"] >= 85.0:
            alerts.append(
                f"⚠️ <b>Повышенное заполнение диска:</b> <b>{metrics['disk_percent']}%</b> (свободно {metrics['disk_free']})"
            )

        if metrics["ram_percent"] >= 92.0:
            alerts.append(
                f"🚨 <b>Критическая загрузка RAM!</b> Занято: <b>{metrics['ram_percent']}%</b> ({metrics['ram_used']} / {metrics['ram_total']})"
            )
        elif metrics["ram_percent"] >= 85.0:
            alerts.append(
                f"⚠️ <b>Повышенная загрузка RAM:</b> <b>{metrics['ram_percent']}%</b> ({metrics['ram_used']} / {metrics['ram_total']})"
            )

        if alerts:
            self._last_resource_alert_time = now
            msg = "⚠️ <b>СИСТЕМНОЕ ПРЕДУПРЕЖДЕНИЕ</b>\n\n" + "\n".join(alerts)
            try:
                await self.bot.send_message(chat_id=self.admin_id, text=msg, parse_mode="HTML")
            except Exception as e:
                logger.error("Не удалось отправить системный алерт в Telegram: %s", e)

    async def _check_scheduled_backup(self) -> None:
        """Периодическое автоматическое создание резервной копии БД раз в сутки."""
        if not self.settings:
            return

        now = time.time()
        # Если это первый запуск монитора, планируем первый бэкап через 1 час, чтобы не грузить старт
        if self._last_backup_time == 0.0:
            self._last_backup_time = now - (self.backup_interval_seconds - 3600)
            return

        if now - self._last_backup_time < self.backup_interval_seconds:
            return

        self._last_backup_time = now
        logger.info("Запуск автоматического планового резервного копирования БД...")
        try:
            from bots.telegram.bot import perform_database_backup

            ok, data, filename = await perform_database_backup(self.docker_client, self.settings)
            if ok and data:
                size_mb = len(data) / (1024 * 1024)
                logger.info("Автоматический бэкап успешно создан: %s (%.2f МБ)", filename, size_mb)
                if self.alerts_enabled and self.admin_id > 0:
                    msg = (
                        f"💾 <b>Автоматический бэкап БД создан успешно</b>\n\n"
                        f"📁 <b>Файл:</b> <code>{filename}</code>\n"
                        f"📦 <b>Размер:</b> {size_mb:.2f} МБ\n"
                        f"🕒 <b>Хранение:</b> сохранено локально с ротацией 7 дней."
                    )
                    with contextlib.suppress(Exception):
                        await self.bot.send_message(chat_id=self.admin_id, text=msg, parse_mode="HTML")
            else:
                logger.error("Сбой автоматического бэкапа: %s", filename)
                if self.alerts_enabled and self.admin_id > 0:
                    alert_msg = f"❌ <b>Сбой планового бэкапа базы данных!</b>\n\n<code>{filename}</code>"
                    with contextlib.suppress(Exception):
                        await self.bot.send_message(chat_id=self.admin_id, text=alert_msg, parse_mode="HTML")
        except Exception as exc:
            logger.error("Исключение при выполнении планового бэкапа: %s", exc)

    async def _send_failure_alert(self, container_name: str, status: str) -> None:
        """Отправить тревожный алерт о сбое контейнера."""
        logger.warning("Алерт о сбое контейнера: %s (%s)", container_name, status)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🔄 Перезапустить {container_name}",
                        callback_data=f"restart:{container_name}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"📋 Логи {container_name}",
                        callback_data=f"logs:{container_name}",
                    )
                ],
            ]
        )

        text = (
            "🚨 <b>ВНИМАНИЕ: СБОЙ СЕРВИСА!</b>\n\n"
            f"Контейнер: <code>{container_name}</code>\n"
            f"Статус: <b>{status}</b>\n\n"
            "Нажмите кнопку ниже для быстрого перезапуска или просмотра логов."
        )

        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Не удалось отправить алерт в Telegram: %s", e)

    async def _send_recovery_alert(self, container_name: str, status: str) -> None:
        """Отправить уведомление о восстановлении сервиса."""
        logger.info("Сервис восстановился: %s (%s)", container_name, status)

        text = (
            "✅ <b>ВОССТАНОВЛЕНИЕ СЕРВИСА</b>\n\n"
            f"Контейнер <code>{container_name}</code> вернулся в рабочее состояние!\n"
            f"Текущий статус: <b>{status}</b>"
        )

        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error("Не удалось отправить уведомление о восстановлении в Telegram: %s", e)
