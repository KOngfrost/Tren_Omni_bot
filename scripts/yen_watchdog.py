"""сервис независимого мониторинга сервера oss и контейнеров с хоста yen."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

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
    effective_proxy = proxy_url if proxy_url is not None else os.getenv("TELEGRAM_PROXY", "http://127.0.0.1:10809")
    proxies = (
        {"http": effective_proxy, "https": effective_proxy} if effective_proxy else None
    )

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

    if fails == 1:
        if category == "server":
            msg = (
                f"<b>ВНИМАНИЕ: СЕРВЕР OSS НЕДОСТУПЕН</b>\n\n"
                f"Статус: <b>проверьте пк</b>\n"
                f"Диагностика: <code>{details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: 1/2. Опрос переведен на 5 минут."
            )
            notifier(config.bot_token_server, list(config.server_alert_admin_ids), msg)
        else:
            msg = (
                f"<b>ВНИМАНИЕ: СБОЙ СЕРВИСОВ OSS</b>\n\n"
                f"Диагностика: <code>{details}</code>\n"
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
                f"Диагностика: <code>{details}</code>\n"
                f"Время: {now_iso}\n"
                f"Сбой: {fails}/{config.fail_threshold_maintenance}. "
                f"Режим технических работ <b>АКТИВИРОВАН</b>."
            )
            notifier(config.bot_token_brain, [config.admin_chat_id], msg)
        else:
            msg = (
                f"<b>КРИТИЧЕСКИЙ СБОЙ: ПОВТОРНАЯ НЕДОСТУПНОСТЬ ХОСТА</b>\n\n"
                f"Статус: <b>проверьте пк</b>\n"
                f"Диагностика: <code>{details}</code>\n"
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

    # если всё в порядке, но ранее был сбой или активны техработы — запускаем восстановление
    if state.consecutive_failures > 0 or state.maintenance_active:
        return _handle_recovery_cycle(state, config, now_iso, notifier, maintenance_setter)

    # штатная проверка при нормальной работе
    new_state = WatchdogState(
        consecutive_failures=0,
        maintenance_active=False,
        last_failure_category="none",
        last_check_time=now_iso,
    )
    return new_state, config.check_interval_ok


def perform_full_check(config: WatchdogConfig) -> tuple[bool, str, str]:
    """выполнить полный шаг диагностики хоста oss, контейнеров и сайта."""
    # 1. Проверка доступности хоста ПК
    host_ok, host_msg = check_host_ssh(config.oss_ssh_target, config.ssh_timeout_seconds)
    if not host_ok:
        return False, "server", f"хост {config.oss_ssh_target} недоступен: {host_msg}"

    # 2. Проверка docker контейнеров
    cont_ok, cont_msg = check_containers_via_ssh(
        config.oss_ssh_target, config.core_containers, config.ssh_timeout_seconds
    )
    if not cont_ok:
        return False, "container", cont_msg

    # 3. Проверка доступности веб-сайта
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


def main() -> None:
    """основной цикл службы сторожа на сервере yen."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config_from_env()
    state = load_state(config.state_file)
    is_running = True

    def _sig_handler(_sig: int, _frame: object) -> None:
        nonlocal is_running
        logger.info("получен сигнал завершения работы, останавливаем watchdog...")
        is_running = False

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    logger.info(
        "служба yen_watchdog запущена (интервал нормы=%dc, интервал сбоя=%dc, цель=%s)",
        config.check_interval_ok,
        config.check_interval_fail,
        config.oss_ssh_target,
    )

    while is_running:
        is_ok, category, details = perform_full_check(config)
        logger.info("результат проверки: ok=%s category=%s details=%s", is_ok, category, details)

        state, sleep_sec = process_check_result(
            state=state,
            config=config,
            is_ok=is_ok,
            category=category,
            details=details,
            notifier=send_telegram_notification,
            maintenance_setter=lambda en: set_remote_maintenance_ssh(config.oss_ssh_target, en),
        )
        save_state(config.state_file, state)

        # сон с проверкой флага is_running каждые 2 секунды
        slept = 0
        while is_running and slept < sleep_sec:
            time.sleep(2)
            slept += 2

    logger.info("служба yen_watchdog корректно остановлена.")


if __name__ == "__main__":
    main()
