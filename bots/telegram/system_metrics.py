"""Сбор системных метрик сервера (CPU, RAM, Диск, Uptime) с помощью psutil."""

from __future__ import annotations

import contextlib
import datetime
import os
import shutil
from typing import Any

import psutil


def render_progress_bar(percent: float, length: int = 10) -> str:
    """Отрисовать текстовый прогресс-бар вида [██████░░░░]."""
    clamped = max(0.0, min(100.0, percent))
    filled_len = round(length * clamped / 100)
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}]"


def _format_bytes(bytes_count: int | float) -> str:
    """Форматировать байты в человекочитаемый вид (MB, GB)."""
    gb = bytes_count / (1024**3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    mb = bytes_count / (1024**2)
    return f"{mb:.0f} MB"


def get_system_metrics() -> dict[str, Any]:
    """Собрать текущие показатели нагрузки и использования ресурсов сервера."""
    # 1. CPU
    cpu_percent = psutil.cpu_percent(interval=0.2)
    cpu_count = psutil.cpu_count(logical=True) or 1

    # Load average (на Linux)
    load_avg: tuple[float, float, float] | None = None
    if hasattr(os, "getloadavg"):
        with contextlib.suppress(OSError):
            load_avg = os.getloadavg()

    # 2. RAM
    virtual_mem = psutil.virtual_memory()
    ram_total = _format_bytes(virtual_mem.total)
    ram_used = _format_bytes(virtual_mem.used)
    ram_percent = virtual_mem.percent

    # 3. Disk (корень / или текущий том)
    disk_path = "/" if os.name != "nt" else "C:\\"
    disk_usage = shutil.disk_usage(disk_path)
    disk_total = _format_bytes(disk_usage.total)
    disk_used = _format_bytes(disk_usage.used)
    disk_free = _format_bytes(disk_usage.free)
    disk_percent = round((disk_usage.used / disk_usage.total) * 100, 1) if disk_usage.total > 0 else 0.0

    # 4. Uptime хоста
    boot_timestamp = psutil.boot_time()
    boot_dt = datetime.datetime.fromtimestamp(boot_timestamp, tz=datetime.UTC)
    now_dt = datetime.datetime.now(tz=datetime.UTC)
    uptime_delta = now_dt - boot_dt
    days = uptime_delta.days
    hours, remainder = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{days}д {hours}ч {minutes}м" if days > 0 else f"{hours}ч {minutes}м"

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "load_avg": load_avg,
        "ram_total": ram_total,
        "ram_used": ram_used,
        "ram_percent": ram_percent,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_free": disk_free,
        "disk_percent": disk_percent,
        "uptime_str": uptime_str,
    }


def format_metrics_message(metrics: dict[str, Any]) -> str:
    """Сформировать HTML-сообщение с обзором состояния системы."""
    cpu_p = metrics["cpu_percent"]
    ram_p = metrics["ram_percent"]
    disk_p = metrics["disk_percent"]

    cpu_bar = render_progress_bar(cpu_p)
    ram_bar = render_progress_bar(ram_p)
    disk_bar = render_progress_bar(disk_p)

    # Иконка нагрузки
    cpu_icon = "🟢" if cpu_p < 75 else ("🟡" if cpu_p < 90 else "🔴")
    ram_icon = "🟢" if ram_p < 80 else ("🟡" if ram_p < 92 else "🔴")
    disk_icon = "🟢" if disk_p < 85 else ("🟡" if disk_p < 95 else "🔴")

    lines = [
        "🖥 <b>Состояние сервера</b>",
        f"⏱ <b>Аптайм хоста:</b> {metrics['uptime_str']}",
    ]

    if metrics.get("load_avg"):
        l1, l5, l15 = metrics["load_avg"]
        lines.append(f"📊 <b>Load Average:</b> {l1:.2f}, {l5:.2f}, {l15:.2f}")

    lines.extend(
        [
            "",
            f"{cpu_icon} <b>CPU ({metrics['cpu_count']} cores):</b> {cpu_p}%",
            f"<code>{cpu_bar}</code>",
            "",
            f"{ram_icon} <b>RAM:</b> {metrics['ram_used']} / {metrics['ram_total']} ({ram_p}%)",
            f"<code>{ram_bar}</code>",
            "",
            f"{disk_icon} <b>Диск:</b> {metrics['disk_used']} / {metrics['disk_total']} ({disk_p}%)",
            f"<code>{disk_bar}</code> (свободно {metrics['disk_free']})",
        ]
    )

    return "\n".join(lines)
