"""Асинхронный клиент к Docker Engine API через сокет /var/run/docker.sock."""

from __future__ import annotations

import io
import logging
import os
import posixpath
import re
import tarfile
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/var/run/docker.sock"

# Заголовок фрейма мультиплексированного потока Docker (stdcopy):
# 1 байт — тип потока, 3 байта — нули, 4 байта (big-endian) — длина данных.
_FRAME_HEADER_SIZE = 8
_STREAM_STDERR = 2
# Верхняя граница длины одного фрейма. Реальные кадры логов/вывода exec
# редко превышают несколько мегабайт; всё, что больше, — признак
# искажённого (не мультиплексированного) потока.
_MAX_FRAME_PAYLOAD = 64 * 1024 * 1024


def demultiplex_stream(raw: bytes) -> tuple[list[bytes], list[bytes]]:
    """Разобрать мультиплексированный поток Docker на части stdout и stderr.

    Защищено от искажения бинарного потока: если в заголовке объявлена
    недопустимая длина фрейма, разбор прерывается, а уже собранные данные
    возвращаются как есть. Без такой проверки повреждённый заголовок
    (например, при ``Tty=True`` или ответе с TTY, где поток НЕ
    мультиплексирован и начинается с обычного текста) давал бы
    ``payload_size`` в несколько гигабайт и либо выход за границы
    буфера, либо — что хуже — зацикливание на ``offset``, из-за которого
    бот зависал на таком ответе.

    Возвращает (части stdout, части stderr). Если поток оказался
    не-мультиплексированным, весь буфер возвращается в stdout.
    """
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    offset = 0
    total = len(raw)

    while offset + _FRAME_HEADER_SIZE <= total:
        try:
            stream_type = raw[offset]
            size = int.from_bytes(
                raw[offset + 4 : offset + _FRAME_HEADER_SIZE], byteorder="big"
            )
        except Exception:  # pragma: no cover - защитный блок
            logger.warning("Повреждён заголовок фрейма Docker на смещении %d", offset)
            break

        # Валидация длины фрейма: без неё битый поток приводит к зависанию
        if size < 0 or size > _MAX_FRAME_PAYLOAD:
            logger.warning(
                "Недопустимая длина фрейма Docker (%d байт) на смещении %d — разбор прерван",
                size,
                offset,
            )
            break

        end = offset + _FRAME_HEADER_SIZE + size
        truncated = end > total
        if truncated:
            # Фрейм обрезан (поток пришёл не полностью) — берём остаток и
            # ОБЯЗАТЕЛЬНО завершаем разбор. Без присваивания offset цикл
            # крутился бы на том же смещении и бот зависал.
            chunk = raw[offset + _FRAME_HEADER_SIZE :]
        else:
            chunk = raw[offset + _FRAME_HEADER_SIZE : end]
            offset = end

        if stream_type == _STREAM_STDERR:
            stderr_parts.append(chunk)
        else:
            stdout_parts.append(chunk)

        if truncated:
            break

    # Ни одного корректного фрейма разобрать не удалось — считаем поток
    # «сырым» текстом (например, ответ с TTY без мультиплексирования).
    if not stdout_parts and not stderr_parts and raw:
        return [raw], []

    return stdout_parts, stderr_parts


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
        return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=120.0)

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
                stdout_parts, _stderr_parts = demultiplex_stream(raw_bytes)

                if not stdout_parts:
                    return "Логи пусты."

                # Демоплексирование отброшено управляющие байты заголовков
                lines: list[str] = []
                for part in stdout_parts:
                    lines.append(part.decode("utf-8", errors="replace"))

                # Если поток не был мультиплексирован — вычищаем управляющие символы
                joined = "".join(lines)
                clean_text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", joined)
                return clean_text.strip() or "Логи пусты."
        except Exception as e:
            logger.error("Ошибка получения логов контейнера %s: %s", name_or_id, e)
            return f"Ошибка получения логов: {e}"

    async def exec_command(
        self,
        name_or_id: str,
        cmd: list[str],
        env: list[str] | None = None,
    ) -> tuple[int, bytes, bytes]:
        """Выполнить команду внутри контейнера.

        Возвращает кортеж (exit_code, stdout_bytes, stderr_bytes).
        """
        if not self.is_socket_present():
            return -1, b"", b"Docker socket not available."

        try:
            async with await self._get_client() as client:
                payload: dict[str, Any] = {
                    "AttachStdout": True,
                    "AttachStderr": True,
                    "Cmd": cmd,
                }
                if env:
                    payload["Env"] = env

                res = await client.post(f"/containers/{name_or_id}/exec", json=payload)
                if res.status_code not in (200, 201):
                    return res.status_code, b"", res.content

                exec_id = res.json().get("Id")
                if not exec_id:
                    return -1, b"", b"Failed to obtain exec ID."

                start_res = await client.post(
                    f"/exec/{exec_id}/start",
                    json={"Detach": False, "Tty": False},
                )
                raw = start_res.content

                inspect_res = await client.get(f"/exec/{exec_id}/json")
                exit_code = 0
                if inspect_res.status_code == 200:
                    exit_code = inspect_res.json().get("ExitCode", 0)

                # Безопасное демоплексирование: повреждённый заголовок фрейма
                # не приводит к зависанию (см. demultiplex_stream)
                stdout_parts, stderr_parts = demultiplex_stream(raw)

                return exit_code, b"".join(stdout_parts), b"".join(stderr_parts)
        except Exception as e:
            logger.error("Ошибка выполнения exec в контейнере %s: %s", name_or_id, e)
            return -1, b"", str(e).encode()

    async def upload_file(
        self,
        name_or_id: str,
        dest_path: str,
        content: bytes,
        mode: int = 0o600,
    ) -> bool:
        """Загрузить файл в файловую систему контейнера (Docker Archive API).

        Используется для передачи секретов (например, временного ``.pgpass``)
        внутрь контейнера без раскрытия значения в переменных окружения
        exec-процесса (``Env`` виден в ``/exec/{id}/json``) и в аргументах
        командной строки.

        Возвращает True, если архив успешно распакован в ``dest_path``.
        """
        if not self.is_socket_present():
            return False

        dest_dir = posixpath.dirname(dest_path) or "/"
        archive_name = posixpath.basename(dest_path)

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=archive_name)
            info.size = len(content)
            info.mode = mode
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(content))

        try:
            async with await self._get_client() as client:
                res = await client.put(
                    f"/containers/{name_or_id}/archive",
                    params={"path": dest_dir},
                    content=buffer.getvalue(),
                    headers={"Content-Type": "application/x-tar"},
                )
                if res.status_code not in (200, 204):
                    logger.error(
                        "Не удалось загрузить файл в контейнер %s: %s",
                        name_or_id,
                        res.text[:500],
                    )
                    return False
                return True
        except Exception as e:
            logger.error("Ошибка загрузки файла в контейнер %s: %s", name_or_id, e)
            return False

