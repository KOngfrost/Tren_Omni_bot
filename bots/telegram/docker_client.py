"""Асинхронный клиент к Docker Engine API через сокет /var/run/docker.sock."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/var/run/docker.sock"


class ContainerInfo:
    """Структурированная информация о контейнере."""

    def __init__(
        self,
        id: str,
        name: str,
        image: str,
        state: str,
        status: str,
        health: str | None = None,
        is_project_container: bool = False,
    ):
        self.id = id
        self.name = name.lstrip("/")
        self.image = image
        self.state = state  # "running", "exited", "restarting", etc.
        self.status = status  # "Up 2 hours (healthy)", "Exited (0) 10m ago"
        self.health = health  # "healthy", "unhealthy", "starting", None
        self.is_project_container = is_project_container

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def is_healthy(self) -> bool:
        if self.health:
            return self.health == "healthy"
        return self.is_running

    @property
    def status_emoji(self) -> str:
        if self.health == "healthy":
            return "🟢"
        if self.health == "starting":
            return "🟡"
        if self.health == "unhealthy":
            return "🔴"
        if self.is_running:
            return "🟢"
        if "Exited (0)" in self.status:
            return "⚪"
        return "🔴"


class DockerClient:
    """Клиент для взаимодействия с Docker Engine API через Unix сокет."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH):
        self.socket_path = socket_path
        self._available: bool | None = None

    def is_socket_present(self) -> bool:
        """Проверить физическое наличие сокета на диске."""
        return os.path.exists(self.socket_path)

    async def _get_client(self) -> httpx.AsyncClient:
        """Создать httpx клиент через UDS (Unix Domain Socket)."""
        transport = httpx.AsyncHTTPTransport(uds=self.socket_path)
        return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=15.0)

    async def ping(self) -> bool:
        """Проверить доступность Docker Engine API."""
        if not self.is_socket_present():
            return False
        try:
            async with await self._get_client() as client:
                res = await client.get("/_ping")
                return res.status_code == 200 and res.text.strip() == "OK"
        except Exception as e:
            logger.warning("Ошибка проверки Docker ping: %s", e)
            return False

    async def list_containers(self, all: bool = True) -> list[ContainerInfo]:
        """Получить список контейнеров."""
        if not self.is_socket_present():
            return []
        try:
            async with await self._get_client() as client:
                res = await client.get(f"/containers/json?all={1 if all else 0}")
                if res.status_code != 200:
                    logger.error("Docker API вернул ошибку: %s", res.text)
                    return []
                raw_list: list[dict[str, Any]] = res.json()
                containers: list[ContainerInfo] = []

                for item in raw_list:
                    c_id = item.get("Id", "")[:12]
                    names = item.get("Names", ["/unknown"])
                    c_name = names[0].lstrip("/") if names else "unknown"
                    image = item.get("Image", "")
                    state = item.get("State", "")
                    status = item.get("Status", "")
                    labels = item.get("Labels", {}) or {}

                    # Определение health из статуса или labels
                    health = None
                    if "(healthy)" in status.lower():
                        health = "healthy"
                    elif "(unhealthy)" in status.lower():
                        health = "unhealthy"
                    elif "(health: starting)" in status.lower():
                        health = "starting"

                    is_project = (
                        "oss_bot" in c_name
                        or "student_bot" in c_name
                        or labels.get("com.docker.compose.project") in ("oss_bot", "student_bot")
                    )

                    containers.append(
                        ContainerInfo(
                            id=c_id,
                            name=c_name,
                            image=image,
                            state=state,
                            status=status,
                            health=health,
                            is_project_container=is_project,
                        )
                    )
                return containers
        except Exception as e:
            logger.error("Ошибка при получении списка контейнеров: %s", e)
            return []

    async def restart_container(self, name_or_id: str, timeout: int = 10) -> tuple[bool, str]:
        """Перезапустить контейнер по имени или ID."""
        if not self.is_socket_present():
            return False, "Docker сокет недоступен."
        try:
            async with await self._get_client() as client:
                res = await client.post(f"/containers/{name_or_id}/restart?t={timeout}")
                if res.status_code in (204, 200):
                    return True, f"Контейнер {name_or_id} успешно перезапущен."
                return False, f"Ошибка перезапуска ({res.status_code}): {res.text}"
        except Exception as e:
            logger.error("Ошибка при перезапуске контейнера %s: %s", name_or_id, e)
            return False, f"Исключение при перезапуске: {e}"

    async def get_container_logs(self, name_or_id: str, tail: int = 40) -> str:
        """Получить последние строки логов контейнера."""
        if not self.is_socket_present():
            return "Docker сокет недоступен."
        try:
            async with await self._get_client() as client:
                res = await client.get(
                    f"/containers/{name_or_id}/logs?stdout=1&stderr=1&tail={tail}&timestamps=0"
                )
                if res.status_code != 200:
                    return f"Не удалось получить логи ({res.status_code}): {res.text}"

                raw_bytes = res.content
                lines: list[str] = []

                # Демоплексирование потока Docker multiplexed stream (8 байт заголовок каждого фрейма)
                offset = 0
                total_len = len(raw_bytes)
                while offset + 8 <= total_len:
                    # Длина полезной нагрузки во фрейме
                    payload_size = int.from_bytes(raw_bytes[offset + 4 : offset + 8], byteorder="big")
                    frame_payload = raw_bytes[offset + 8 : offset + 8 + payload_size]
                    lines.append(frame_payload.decode("utf-8", errors="replace"))
                    offset += 8 + payload_size

                # Если поток не был мультиплексирован или демоплексирование пусто
                if not lines and raw_bytes:
                    text = raw_bytes.decode("utf-8", errors="replace")
                    clean_text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
                    return clean_text

                joined = "".join(lines).strip()
                return joined or "Логи пусты."
        except Exception as e:
            logger.error("Ошибка получения логов контейнера %s: %s", name_or_id, e)
            return f"Ошибка получения логов: {e}"
