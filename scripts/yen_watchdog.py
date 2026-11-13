"""сервис независимого мониторинга сервера oss и контейнеров с хоста yen."""

from __future__ import annotations

import dataclasses
import html
import json
import logging
import os
import signal
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("yen_watchdog")


@dataclasses.dataclass(frozen=True)
class WatchdogConfig:
    """конфигурация службы мониторинга yen_watchdog."""

    check_interval_ok: int = 900  # 15 минут
    check_interval_fail: int = 300  # 5 минут
    fail_threshold_maintenance: int = 2
    admin_chat_id: int = 6665679072
    server_alert_admin_ids: tuple[int, ...] = (6665679072,)
    bot_token_server: str = "9073554630:AAGkF_Rz3My2-CHH0BNZwLhuzEmB-p-nd40"
    bot_token_brain: str = "8911229200:AAHP3-tVYJLCFl7eaArKNodgmhj4YfBxR0g"
    oss_ssh_target: str = "ossadmin@100.120.28.104"
    oss_web_url: str = "https://oss.tail5d7c2e.ts.net/health"
    state_file: str = "/tmp/yen_watchdog_state.json"
    admins_file: str = "/home/user/oss_watchdog/server_admins.json"
    ssh_timeout_seconds: int = 10
    http_timeout_seconds: int = 10
    core_containers: tuple[str, ...] = (
        "oss_bot_caddy",
        "oss_bot_web",
        "oss_bot_app",
        "oss_bot_go_api",
        "oss_bot_tg_monitor",
        "oss_bot_redis",
        "oss_bot_db",
        "oss_bot_pgbouncer",
    )


@dataclasses.dataclass(frozen=True)
class WatchdogState:
    """состояние службы мониторинга."""

    consecutive_failures: int = 0
    maintenance_active: bool = False
    last_failure_category: str = "none"  # "none", "server", "container"
    last_check_time: str = ""


class ServerAdminStore:
    """хранилище администраторов бота check_me_up с сохранением в json."""

    def __init__(self, file_path: str, root_admin_id: int = 6665679072) -> None:
        self.file_path = file_path
        self.root_admin_id = root_admin_id
        self._lock = threading.RLock()
        self._load_or_init()

    def _load_or_init(self) -> dict[str, Any]:
        with self._lock:
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "admins" in data:
                            return data
                except Exception as exc:
                    logger.warning(
                        "ошибка чтения файла администраторов %s: %s", self.file_path, exc
                    )

            now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            initial_data: dict[str, Any] = {
                "root_admin_id": self.root_admin_id,
                "admins": [
                    {
                        "id": self.root_admin_id,
                        "role": "root",
                        "note": "главный технический администратор",
                        "added_at": now_iso,
                        "added_by": "system",
                    }
                ],
            }
            self._save_unlocked(initial_data)
            return initial_data

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        parent = os.path.dirname(self.file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.file_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.file_path)

    def get_admins(self) -> list[int]:
        """получить список уникальных id администраторов."""
        data = self._load_or_init()
        admins = data.get("admins", [])
        ids = [int(item["id"]) for item in admins if "id" in item and int(item["id"]) > 0]
        if self.root_admin_id > 0 and self.root_admin_id not in ids:
            ids.insert(0, self.root_admin_id)
        return ids

    def get_admin_details(self) -> list[dict[str, Any]]:
        """получить подробный список администраторов."""
        data = self._load_or_init()
        return list(data.get("admins", []))

    def add_admin(self, admin_id: int, note: str = "", added_by: str = "") -> tuple[bool, str]:
        """добавить нового администратора."""
        if admin_id <= 0:
            return False, "некорректный id администратора"

        with self._lock:
            data = self._load_or_init()
            admins = data.get("admins", [])
            for item in admins:
                if int(item.get("id", 0)) == admin_id:
                    return False, f"пользователь {admin_id} уже является администратором"

            now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            admins.append(
                {
                    "id": admin_id,
                    "role": "admin",
                    "note": note,
                    "added_at": now_iso,
                    "added_by": added_by or "unknown",
                }
            )
            data["admins"] = admins
            try:
                self._save_unlocked(data)
                return True, f"администратор {admin_id} успешно добавлен"
            except Exception as exc:
                return False, f"ошибка сохранения администратора: {exc}"

    def del_admin(self, admin_id: int, root_id: int = 6665679072) -> tuple[bool, str]:
        """удалить администратора из списка (главного удалять запрещено)."""
        effective_root = root_id or self.root_admin_id
        if admin_id == effective_root or admin_id == self.root_admin_id:
            return False, "удаление главного администратора запрещено"

        with self._lock:
            data = self._load_or_init()
            admins = data.get("admins", [])
            new_admins = [item for item in admins if int(item.get("id", 0)) != admin_id]
            if len(new_admins) == len(admins):
                return False, f"администратор {admin_id} не найден в списке"

            data["admins"] = new_admins
            try:
                self._save_unlocked(data)
                return True, f"администратор {admin_id} успешно удален"
            except Exception as exc:
                return False, f"ошибка удаления администратора: {exc}"


class StateHolder:
    """потокобезопасная обертка для хранения актуального состояния."""

    def __init__(self, state: WatchdogState) -> None:
        self._lock = threading.Lock()
        self._state = state

    @property
    def state(self) -> WatchdogState:
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: WatchdogState) -> None:
        with self._lock:
            self._state = value


def parse_admin_ids(raw_str: str) -> list[int]:
    """разобрать строку с id администраторов, разделенными запятыми."""
    if not raw_str:
        return []
    result: list[int] = []
    for item in raw_str.split(","):
        cleaned = item.strip()
        if cleaned.isdigit():
            result.append(int(cleaned))
    return result


def load_config_from_env() -> WatchdogConfig:
    """загрузить конфигурацию из переменных окружения."""
    server_ids_raw = os.getenv("SERVER_ALERT_ADMIN_IDS", "6665679072")
    server_ids = parse_admin_ids(server_ids_raw) or [6665679072]

    return WatchdogConfig(
        check_interval_ok=int(os.getenv("CHECK_INTERVAL_OK", "900")),
        check_interval_fail=int(os.getenv("CHECK_INTERVAL_FAIL", "300")),
        fail_threshold_maintenance=int(os.getenv("FAIL_THRESHOLD_MAINTENANCE", "2")),
        admin_chat_id=int(os.getenv("ADMIN_CHAT_ID", "6665679072")),
        server_alert_admin_ids=tuple(server_ids),
        bot_token_server=os.getenv(
            "BOT_TOKEN_SERVER", "9073554630:AAGkF_Rz3My2-CHH0BNZwLhuzEmB-p-nd40"
        ),
        bot_token_brain=os.getenv(
            "BOT_TOKEN_BRAIN", "8911229200:AAHP3-tVYJLCFl7eaArKNodgmhj4YfBxR0g"
        ),
        oss_ssh_target=os.getenv("OSS_SSH_TARGET", "ossadmin@100.120.28.104"),
        oss_web_url=os.getenv("OSS_WEB_URL", "https://oss.tail5d7c2e.ts.net/health"),
        state_file=os.getenv("STATE_FILE", "/tmp/yen_watchdog_state.json"),
        admins_file=os.getenv("ADMINS_FILE", "/home/user/oss_watchdog/server_admins.json"),
    )


def send_telegram_notification(
    bot_token: str,
    chat_ids: list[int],
    text: str,
    proxy_url: str | None = None,
) -> bool:
    """отправить сообщение в telegram доверенным администраторам."""
    if not bot_token or not chat_ids:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    success_any = False
    effective_proxy = (
        proxy_url
        if proxy_url is not None
        else os.getenv("TELEGRAM_PROXY", "http://127.0.0.1:10809")
    )
    proxies = {"http": effective_proxy, "https": effective_proxy} if effective_proxy else None

    try:
        import requests

        for chat_id in chat_ids:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            try:
                resp = requests.post(url, json=payload, proxies=proxies, timeout=10)
                if resp.status_code == 200:
                    success_any = True
                else:
                    logger.warning(
                        "telegram api ошибка chat_id=%s код=%d: %s",
                        chat_id,
                        resp.status_code,
                        resp.text,
                    )
            except Exception as exc:
                logger.warning(
                    "ошибка отправки через requests chat_id=%s: %s",
                    chat_id,
                    exc,
                )
        return success_any
    except ImportError:
        pass

    # резервная отправка через urllib
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    success_any = True
        except Exception as exc:
            logger.warning(
                "не удалось отправить уведомление в telegram chat_id=%s: %s",
                chat_id,
                exc,
            )
    return success_any


def check_host_ssh(target: str, timeout_sec: int) -> tuple[bool, str]:
    """проверить доступность хоста oss по ssh."""
    cmd = [
        "ssh",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_sec}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        target,
        "hostname",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 2)
        stdout_txt = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr_txt = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return True, stdout_txt
        err_msg = stderr_txt or f"код возврата ssh: {proc.returncode}"
        return False, err_msg
    except Exception as exc:
        return False, str(exc)


def check_containers_via_ssh(
    target: str, core_containers: tuple[str, ...], timeout_sec: int
) -> tuple[bool, str]:
    """проверить состояние docker-контейнеров на хосте oss."""
    cmd = [
        "ssh",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_sec}",
        target,
        "wsl.exe",
        "-u",
        "oss",
        "docker",
        "ps",
        "-a",
        "--format",
        "{{.Names}}::{{.Status}}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 5)
        stdout_txt = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr_txt = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return False, f"ошибка docker ps: {stderr_txt}"

        running_map: dict[str, str] = {}
        for line in stdout_txt.splitlines():
            parts = line.strip().split("::")
            if len(parts) == 2:
                running_map[parts[0].strip()] = parts[1].strip()

        bad_services: list[str] = []
        for name in core_containers:
            status = running_map.get(name)
            if not status:
                bad_services.append(f"{name} (отсутствует)")
                continue
            status_lower = status.lower()
            if (
                "unhealthy" in status_lower
                or "restarting" in status_lower
                or ("exited" in status_lower and "exited (0)" not in status_lower)
            ):
                bad_services.append(f"{name} ({status})")

        if bad_services:
            return False, "сбой контейнеров: " + ", ".join(bad_services)
        return True, "все контейнеры в строю"
    except Exception as exc:
        return False, f"ошибка при проверке docker: {exc}"


def check_web_endpoint(url: str, timeout_sec: int) -> tuple[bool, str]:
    """проверить доступность веб-эндпоинта healthcheck."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": "yen-watchdog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            if resp.status != 200:
                return False, f"web health статус {resp.status}, тело: {body[:100]}"
            try:
                data = json.loads(body)
                if data.get("status") == "ok":
                    return True, "web health ok"
            except Exception:
                pass
            return False, f"web health некорректный ответ: {body[:100]}"
    except Exception as exc:
        return False, f"ошибка подключения к сайту: {exc}"


def set_remote_maintenance_ssh(target: str, enable: bool, timeout_sec: int = 15) -> bool:
    """переключить режим техработ на oss через ssh."""
    action = "enable" if enable else "disable"
    cmd = [
        "ssh",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_sec}",
        target,
        "wsl.exe",
        "-u",
        "oss",
        "docker",
        "exec",
        "oss_bot_app",
        "python3",
        "scripts/manage_maintenance.py",
        action,
        "--by",
        "yen_watchdog",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 2)
        stdout_txt = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr_txt = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            logger.info("режим техработ успешно переключен: %s", action)
            return True
        logger.warning(
            "ошибка при вызове manage_maintenance: %s %s",
            stdout_txt,
            stderr_txt,
        )
        return False
    except Exception as exc:
        logger.error("исключение при переключении техработ: %s", exc)
        return False


def _handle_failure_cycle(
    state: WatchdogState,
    config: WatchdogConfig,
    category: str,
    details: str,
    now_iso: str,
    notifier: Callable[[str, list[int], str], bool],
    maintenance_setter: Callable[[bool], bool],
) -> tuple[WatchdogState, int]:
    """обработать сбой проверки (сервер или контейнер)."""
    fails = state.consecutive_failures + 1
    maint_active = state.maintenance_active
    safe_details = html.escape(details)

    if fails == 1:
        if category == "server":
            msg = (
                f"<b>ВНИМАНИЕ: СЕРВЕР OSS НЕДОСТУПЕН</b>\n\n"
                f"Статус: <b>проверьте пк</b>\n"
                f"Диагностика: <code>{safe_details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: 1/2. Опрос переведен на 5 минут."
            )
            notifier(config.bot_token_server, list(config.server_alert_admin_ids), msg)
        else:
            msg = (
                f"<b>ВНИМАНИЕ: СБОЙ СЕРВИСОВ OSS</b>\n\n"
                f"Диагностика: <code>{safe_details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: 1/2. Опрос переведен на 5 минут. Техработы пока не включены."
            )
            notifier(config.bot_token_brain, [config.admin_chat_id], msg)

    elif fails == config.fail_threshold_maintenance:
        maint_active = True
        if category == "container":
            maintenance_setter(True)
            msg = (
                f"<b>КРИТИЧЕСКИЙ СБОЙ: ПОВТОРНЫЙ ОТКАЗ СЕРВИСОВ</b>\n\n"
                f"Диагностика: <code>{safe_details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: {fails}/{config.fail_threshold_maintenance}. "
                f"Режим технических работ <b>АКТИВИРОВАН</b>."
            )
            notifier(config.bot_token_brain, [config.admin_chat_id], msg)
        else:
            msg = (
                f"<b>КРИТИЧЕСКИЙ СБОЙ: ПОВТОРНАЯ НЕДОСТУПНОСТЬ ХОСТА</b>\n\n"
                f"Статус: <b>проверьте пк</b>\n"
                f"Диагностика: <code>{safe_details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: {fails}/{config.fail_threshold_maintenance}. "
                f"Режим техработ будет гарантирован при включении питания/связи."
            )
            notifier(config.bot_token_server, list(config.server_alert_admin_ids), msg)

    new_state = WatchdogState(
        consecutive_failures=fails,
        maintenance_active=maint_active,
        last_failure_category=category,
        last_check_time=now_iso,
    )
    return new_state, config.check_interval_fail


def _handle_recovery_cycle(
    state: WatchdogState,
    config: WatchdogConfig,
    now_iso: str,
    notifier: Callable[[str, list[int], str], bool],
    maintenance_setter: Callable[[bool], bool],
) -> tuple[WatchdogState, int]:
    """обработать успешное восстановление систем после сбоя."""
    if state.maintenance_active:
        maintenance_setter(False)

    prev_cat = state.last_failure_category
    if prev_cat == "server":
        msg = (
            f"<b>ВОССТАНОВЛЕНИЕ: СЕРВЕР OSS В СЕТИ</b>\n\n"
            f"Связь с ПК восстановлена. Все контейнеры и сайт работают в штатном режиме.\n"
            f"Режим технических работ снят. Время: {now_iso}."
        )
        notifier(config.bot_token_server, list(config.server_alert_admin_ids), msg)
    else:
        msg = (
            f"<b>ВОССТАНОВЛЕНИЕ: ВСЕ СИСТЕМЫ В ШТАТНОМ РЕЖИМЕ</b>\n\n"
            f"Контейнеры и сайт функционируют корректно.\n"
            f"Режим технических работ снят. Время: {now_iso}."
        )
        notifier(config.bot_token_brain, [config.admin_chat_id], msg)

    new_state = WatchdogState(
        consecutive_failures=0,
        maintenance_active=False,
        last_failure_category="none",
        last_check_time=now_iso,
    )
    return new_state, config.check_interval_ok


def process_check_result(
    state: WatchdogState,
    config: WatchdogConfig,
    is_ok: bool,
    category: str,
    details: str,
    notifier: Callable[[str, list[int], str], bool],
    maintenance_setter: Callable[[bool], bool],
) -> tuple[WatchdogState, int]:
    """рассчитать переход состояния и интервал паузы до следующей проверки."""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not is_ok:
        return _handle_failure_cycle(
            state, config, category, details, now_iso, notifier, maintenance_setter
        )

    if state.consecutive_failures > 0 or state.maintenance_active:
        return _handle_recovery_cycle(state, config, now_iso, notifier, maintenance_setter)

    new_state = WatchdogState(
        consecutive_failures=0,
        maintenance_active=False,
        last_failure_category="none",
        last_check_time=now_iso,
    )
    return new_state, config.check_interval_ok


def make_maintenance_setter(target: str) -> Callable[[bool], bool]:
    """создать функцию-сеттер режима техработ для целевого хоста."""

    def _setter(enable: bool) -> bool:
        return set_remote_maintenance_ssh(target, enable)

    return _setter


def perform_full_check(config: WatchdogConfig) -> tuple[bool, str, str]:
    """выполнить полный шаг диагностики хоста oss, контейнеров и сайта."""
    host_ok, host_msg = check_host_ssh(config.oss_ssh_target, config.ssh_timeout_seconds)
    if not host_ok:
        return False, "server", f"хост {config.oss_ssh_target} недоступен: {host_msg}"

    cont_ok, cont_msg = check_containers_via_ssh(
        config.oss_ssh_target, config.core_containers, config.ssh_timeout_seconds
    )
    if not cont_ok:
        return False, "container", cont_msg

    web_ok, web_msg = check_web_endpoint(config.oss_web_url, config.http_timeout_seconds)
    if not web_ok:
        return False, "container", web_msg

    return True, "none", "все компоненты в норме"


def save_state(path: str, state: WatchdogState) -> None:
    """сохранить состояние в json-файл на диске."""
    try:
        data = dataclasses.asdict(state)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        logger.debug("не удалось сохранить файл состояния: %s", exc)


def load_state(path: str) -> WatchdogState:
    """загрузить состояние из json-файла на диске."""
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return WatchdogState(**data)
    except Exception as exc:
        logger.debug("не удалось прочитать файл состояния: %s", exc)
    return WatchdogState()


def _handle_start_help() -> str:
    """вернуть текст справки и перечень команд."""
    return (
        "система мониторинга Check_me_up.\n\n"
        "доступные команды:\n"
        "/status — текущий статус инфраструктуры и проверок\n"
        "/admins — список авторизованных администраторов\n"
        "/addadmin &lt;id&gt; [комментарий] — добавить администратора (только root)\n"
        "/deladmin &lt;id&gt; — удалить администратора (только root)"
    )


def _handle_status(state: WatchdogState) -> str:
    """сформировать сводку о текущем состоянии инфраструктуры."""
    maint_txt = "включен" if state.maintenance_active else "выключен"
    return (
        "текущий статус инфраструктуры oss:\n\n"
        f"последняя проверка: {state.last_check_time or 'еще не проводилась'}\n"
        f"число последовательных сбоев: {state.consecutive_failures}\n"
        f"режим техработ: {maint_txt}\n"
        f"категория последнего сбоя: {state.last_failure_category}"
    )


def _handle_admins(store: ServerAdminStore) -> str:
    """сформировать список авторизованных администраторов."""
    details = store.get_admin_details()
    lines = ["список администраторов бота Check_me_up:"]
    for item in details:
        aid = item.get("id")
        role = item.get("role", "admin")
        note = item.get("note", "")
        note_str = f" ({note})" if note else ""
        lines.append(f"— id: {aid}{note_str} [{role}]")
    return "\n".join(lines)


def _handle_addadmin(
    text: str,
    cmd_parts: list[str],
    user_id: int,
    config: WatchdogConfig,
    store: ServerAdminStore,
) -> str:
    """обработать команду добавления администратора."""
    if user_id != config.admin_chat_id and user_id != store.root_admin_id:
        return "доступ запрещен. только главный технический администратор может добавлять администраторов."
    if len(cmd_parts) < 2 or not cmd_parts[1].isdigit():
        return "использование: /addadmin &lt;id&gt; [комментарий]"
    new_id = int(cmd_parts[1])
    parts = text.split(maxsplit=2)
    note = parts[2].strip() if len(parts) > 2 else ""
    ok, res_msg = store.add_admin(new_id, note=note, added_by=f"telegram:{user_id}")
    if ok:
        greeting = (
            "вы были добавлены в качестве администратора сервера в бот Check_me_up.\n"
            "сюда будут приходить экстренные оповещения о доступности сервера oss (проверьте пк)."
        )
        send_telegram_notification(config.bot_token_server, [new_id], greeting)
    return res_msg


def _handle_deladmin(
    cmd_parts: list[str],
    user_id: int,
    config: WatchdogConfig,
    store: ServerAdminStore,
) -> str:
    """обработать команду удаления администратора."""
    if user_id != config.admin_chat_id and user_id != store.root_admin_id:
        return "доступ запрещен. только главный технический администратор может удалять администраторов."
    if len(cmd_parts) < 2 or not cmd_parts[1].isdigit():
        return "использование: /deladmin &lt;id&gt;"
    del_id = int(cmd_parts[1])
    ok, res_msg = store.del_admin(del_id, root_id=config.admin_chat_id)
    return res_msg


def process_bot_message(
    msg: dict[str, Any],
    config: WatchdogConfig,
    store: ServerAdminStore,
    state: WatchdogState,
) -> str | None:
    """обработать входящее сообщение от администратора в telegram-боте."""
    from_user = msg.get("from", {})
    user_id = from_user.get("id")
    if not user_id:
        return None

    allowed_admins = set(store.get_admins())
    if user_id not in allowed_admins:
        return (
            "доступ запрещен. данный бот предназначен исключительно для "
            "авторизованных администраторов инфраструктуры oss."
        )

    text = msg.get("text", "").strip()
    if not text:
        return None

    cmd_parts = text.split()
    cmd = cmd_parts[0].lower().split("@")[0]

    if cmd in ("/start", "/help"):
        return _handle_start_help()
    if cmd == "/status":
        return _handle_status(state)
    if cmd == "/admins":
        return _handle_admins(store)
    if cmd == "/addadmin":
        return _handle_addadmin(text, cmd_parts, user_id, config, store)
    if cmd == "/deladmin":
        return _handle_deladmin(cmd_parts, user_id, config, store)

    return "неизвестная команда. введите /help для просмотра списка команд."


def fetch_telegram_updates(
    bot_token: str,
    offset: int,
    timeout_sec: int = 20,
    proxy_url: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """получить обновления от telegram bot api через getUpdates."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "timeout": timeout_sec}
    effective_proxy = (
        proxy_url
        if proxy_url is not None
        else os.getenv("TELEGRAM_PROXY", "http://127.0.0.1:10809")
    )
    proxies = {"http": effective_proxy, "https": effective_proxy} if effective_proxy else None

    try:
        import requests

        resp = requests.get(url, params=params, proxies=proxies, timeout=timeout_sec + 5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                max_offset = offset
                for u in updates:
                    u_id = u.get("update_id", 0)
                    if u_id >= max_offset:
                        max_offset = u_id + 1
                return max_offset, updates
    except Exception as exc:
        logger.debug("ошибка получения обновлений telegram: %s", exc)
    return offset, []


def poll_bot_loop(
    config: WatchdogConfig,
    store: ServerAdminStore,
    state_holder: StateHolder,
    stop_event: threading.Event,
) -> None:
    """фоновый цикл приема и обработки команд в telegram-боте check_me_up."""
    offset = 0
    logger.info("фоновый опрос бота check_me_up запущен")
    while not stop_event.is_set():
        try:
            offset, updates = fetch_telegram_updates(
                config.bot_token_server,
                offset=offset,
                timeout_sec=5,
            )
            for item in updates:
                msg = item.get("message")
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                reply = process_bot_message(msg, config, store, state_holder.state)
                if reply and chat_id:
                    send_telegram_notification(config.bot_token_server, [chat_id], reply)
        except Exception as exc:
            logger.warning("ошибка в цикле опроса бота: %s", exc)
            time.sleep(3)
        time.sleep(1)


def main() -> None:
    """основной цикл службы сторожа на сервере yen."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config_from_env()
    state = load_state(config.state_file)
    admin_store = ServerAdminStore(config.admins_file, root_admin_id=config.admin_chat_id)
    state_holder = StateHolder(state)
    stop_event = threading.Event()
    is_running = True

    def _sig_handler(_sig: int, _frame: object) -> None:
        nonlocal is_running
        logger.info("получен сигнал завершения работы, останавливаем watchdog...")
        is_running = False
        stop_event.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    bot_thread = threading.Thread(
        target=poll_bot_loop,
        args=(config, admin_store, state_holder, stop_event),
        daemon=True,
    )
    bot_thread.start()

    logger.info(
        "служба yen_watchdog запущена (интервал нормы=%dc, интервал сбоя=%dc, цель=%s)",
        config.check_interval_ok,
        config.check_interval_fail,
        config.oss_ssh_target,
    )

    while is_running:
        current_admin_ids = tuple(admin_store.get_admins())
        config = dataclasses.replace(config, server_alert_admin_ids=current_admin_ids)

        is_ok, category, details = perform_full_check(config)
        logger.info("результат проверки: ok=%s category=%s details=%s", is_ok, category, details)

        state, sleep_sec = process_check_result(
            state=state,
            config=config,
            is_ok=is_ok,
            category=category,
            details=details,
            notifier=send_telegram_notification,
            maintenance_setter=make_maintenance_setter(config.oss_ssh_target),
        )
        save_state(config.state_file, state)
        state_holder.state = state

        slept = 0
        while is_running and slept < sleep_sec:
            time.sleep(2)
            slept += 2

    stop_event.set()
    bot_thread.join(timeout=3)
    logger.info("служба yen_watchdog корректно остановлена.")


if __name__ == "__main__":
    main()
