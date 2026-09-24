"""Отказоустойчивый LongPoll модуль для VK бота с автоматическим восстановлением после сбоев."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from aiohttp.client_exceptions import ClientConnectionError
from vkbottle.exception_factory.base_exceptions import VKAPIError
from vkbottle.polling.base import FailureCode
from vkbottle.polling.bot_polling import BotPolling

from core.heartbeat import touch_heartbeat

logger = logging.getLogger(__name__)


class RobustBotPolling(BotPolling):
    """Отказоустойчивый BotPolling для VK.

    Устраняет критические проблемы стандартного BotPolling из vkbottle:
    1. При истечении ключа (failed 2/3) или временном сбое VK API (VKAPIError_3 'Not found' и др.)
       старый сервер с истёкшим ключом принудительно сбрасывается (server = None),
       что предотвращает бесконечный цикл 'Unable to make request to BotPolling, retrying...'.
    2. При серии повторных ошибок соединения (>= 3 подряд) кэшированный сервер/ключ
       сбрасывается и запрашивается заново через groups.getLongPollServer.
    3. get_server() имеет встроенный механизм повторных попыток (retries с backoff)
       для обработки временных ошибок VK API и сетевых сбоев.
    4. touch_heartbeat() вызывается при каждом успешном цикле опроса и получении сервера,
       что связывает docker healthcheck с реальным состоянием соединения бота с VK.
    """

    MAX_CONSECUTIVE_ERRORS_BEFORE_RESET = 3
    GET_SERVER_MAX_RETRIES = 5

    async def get_server(self) -> dict[str, Any]:
        """Получить данные LongPoll сервера с автоматическими ретраями при сбоях VK API."""
        logger.debug("Запрос данных polling-сервера...")

        # 1. Разрешение group_id, если ещё не определён
        if self.group_id is None:
            for attempt in range(1, self.GET_SERVER_MAX_RETRIES + 1):
                try:
                    response = (await self.api.request("groups.getById", {}))["response"]
                    groups = response.get("groups") if isinstance(response, dict) else response
                    if not groups:
                        raise RuntimeError(
                            "Не удалось получить ID сообщества для bot polling. Проверьте токен сообщества."
                        )
                    self.group_id = groups[0]["id"]
                    logger.info("Успешно определён group_id: %s", self.group_id)
                    break
                except Exception as err:
                    if attempt >= self.GET_SERVER_MAX_RETRIES:
                        logger.error(
                            "Не удалось получить group_id после %d попыток: %s", attempt, err
                        )
                        raise
                    delay = min(2 * attempt, 10)
                    logger.warning(
                        "Ошибка groups.getById (попытка %d/%d): %s. Повтор через %dс...",
                        attempt,
                        self.GET_SERVER_MAX_RETRIES,
                        err,
                        delay,
                    )
                    await asyncio.sleep(delay)

        # 2. Получение LongPoll сервера и ключа
        for attempt in range(1, self.GET_SERVER_MAX_RETRIES + 1):
            try:
                res = await self.api.request(
                    "groups.getLongPollServer",
                    {"group_id": self.group_id},
                )
                server_data = res["response"]
                touch_heartbeat()
                logger.debug(
                    "LongPoll сервер успешно получен (ts=%s)", server_data.get("ts")
                )
                return server_data
            except Exception as err:
                if attempt >= self.GET_SERVER_MAX_RETRIES:
                    logger.error(
                        "Не удалось получить LongPoll сервер после %d попыток: %s",
                        attempt,
                        err,
                    )
                    raise
                delay = min(3 * attempt, 15)
                logger.warning(
                    "Ошибка groups.getLongPollServer (попытка %d/%d): %s. Повтор через %dс...",
                    attempt,
                    self.GET_SERVER_MAX_RETRIES,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Не удалось инициализировать LongPoll сервер.")

    async def handle_failed_event(
        self,
        server: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Обработка кодов ошибок LongPoll (failed 1, 2, 3, 4)."""
        try:
            failed_code = event.get("failed")
            try:
                failed = FailureCode(failed_code)
            except (ValueError, TypeError):
                logger.error("Неизвестный код ошибки LongPoll: %r, event: %r", failed_code, event)
                return {}

            if failed == FailureCode.HISTORY_OUTDATED:
                # Код 1: события устарели, обновляем ts
                server["ts"] = event["ts"]
                return server

            if failed in (FailureCode.KEY_EXPIRED, FailureCode.INFORMATION_LOST):
                # Коды 2 и 3: ключ протух или инфо потеряно, запрашиваем новый сервер
                logger.info(
                    "LongPoll ключ истёк или информация утрачена (failed=%s). Запрашиваем новый сервер...",
                    failed,
                )
                new_server = await self.get_server()
                if failed == FailureCode.KEY_EXPIRED and "ts" in server:
                    new_server["ts"] = server["ts"]
                return new_server

            if failed == FailureCode.INVALID_VERSION:
                logger.error("Неверная версия LongPoll. Сброс сервера.")
                return await self.get_server()

            return {}
        except Exception as exc:
            logger.warning(
                "Ошибка при обработке failed-события LongPoll: %s. Принудительный сброс текущего сервера.",
                exc,
            )
            # Возврат пустого dict гарантирует, что listen() не продолжит крутиться с протухшим key
            return {}

    async def listen(self) -> AsyncGenerator[dict[str, Any], None]:
        """Основной цикл прослушивания LongPoll с защитой от зависаний на протухших ключах."""
        self._stop_event = asyncio.Event()
        consecutive_errors = 0

        # Инициализация первого сервера
        server = self.restore_server_ts(await self.get_server())
        touch_heartbeat()
        logger.info(
            "Запущен отказоустойчивый цикл LongPoll для сообщества %s", self.group_id
        )

        while not self._stop_event.is_set():
            try:
                # Если сервер был сброшен из-за ошибки — получаем свежий
                if not server:
                    logger.info(
                        "Сервер не задан или был сброшен. Получаем новый LongPoll сервер..."
                    )
                    server = await self.get_server()
                    touch_heartbeat()

                event = await self.get_event(server)

                if "failed" in event:
                    server = await self.handle_failed_event(server, event)
                    if not server:
                        # Сбой не разрешён — сбрасываем сервер и делаем паузу
                        consecutive_errors += 1
                        backoff = min(1.0 * consecutive_errors, 10.0)
                        logger.warning(
                            "Не удалось восстановить сессию LongPoll. Пауза %0.1fс перед новым запросом сервера...",
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                    continue

                if "ts" not in event:
                    logger.warning("Ответ LongPoll не содержит 'ts': %r. Сброс сервера.", event)
                    server = None
                    continue

                server["ts"] = event["ts"]
                consecutive_errors = 0
                touch_heartbeat()

                if event.get("updates"):
                    yield event

                await asyncio.to_thread(self.save_server_ts, server)

            except (ClientConnectionError, asyncio.TimeoutError, VKAPIError, Exception) as exc:
                consecutive_errors += 1
                logger.error(
                    "Сбой соединения LongPoll (%s: %s). Ошибок подряд: %d",
                    type(exc).__name__,
                    exc,
                    consecutive_errors,
                )

                # КРИТИЧЕСКИЙ ФИКС: если 3 и более ошибок подряд, старый server/key
                # гарантированно сбрасывается в None, предотвращая бесконечный цикл с мёртвым ключом!
                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS_BEFORE_RESET:
                    logger.warning(
                        "Превышен лимит последовательных ошибок (%d). Сброс LongPoll ключа и принудительное получение нового сервера.",
                        consecutive_errors,
                    )
                    server = None

                backoff = min(1.0 * consecutive_errors, 30.0)
                await asyncio.sleep(backoff)
