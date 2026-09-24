"""Хендлеры и сервис резервного копирования базы данных PostgreSQL."""

from __future__ import annotations

import contextlib
import gzip
import html
import logging
import os
import time
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bots.telegram.docker_client import DockerClient
from core.config import Settings

logger = logging.getLogger(__name__)
router = Router(name="backup")


def rotate_old_backups(backup_dir: str | None = None, max_days: int = 7) -> int:
    """Удалить локальные файлы резервных копий старше max_days дней.

    Возвращает количество удаленных файлов.
    """
    target_dir = backup_dir or os.getenv("BACKUP_DIR", "/var/backups/oss_bot")
    if not os.path.exists(target_dir):
        return 0
    deleted_count = 0
    cutoff_time = time.time() - (max_days * 86400)
    try:
        for entry in os.scandir(target_dir):
            if entry.is_file() and entry.name.startswith("oss_bot_backup_"):
                try:
                    if entry.stat().st_mtime < cutoff_time:
                        os.remove(entry.path)
                        deleted_count += 1
                        logger.info("Удалена устаревшая резервная копия: %s", entry.name)
                except Exception as exc:
                    logger.warning("Не удалось удалить устаревший файл бэкапа %s: %s", entry.name, exc)
    except Exception as e:
        logger.warning("Ошибка сканирования каталога бэкапов %s: %s", target_dir, e)
    return deleted_count


def _save_local_copy(data: bytes, filename: str) -> None:
    """Сохранить резервную копию на диск и выполнить ротацию устаревших копий."""
    backup_dir = os.getenv("BACKUP_DIR", "/var/backups/oss_bot")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, filename), "wb") as f:
            f.write(data)
        rotate_old_backups(backup_dir, max_days=7)
    except Exception as e:
        logger.debug("Не удалось сохранить локальную копию бэкапа: %s", e)


async def perform_database_backup(
    docker_client: DockerClient,
    settings: Settings,
) -> tuple[bool, bytes | None, str]:
    """Выполнить резервное копирование базы данных.

    Возвращает (success, compressed_dump_bytes, filename_or_error).
    """
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"oss_bot_backup_{stamp}.sql.gz"

    user = settings.DB_USER or os.getenv("POSTGRES_USER", "oss_bot")
    db = settings.DB_NAME or os.getenv("POSTGRES_DB", "oss_bot")
    password = settings.DB_PASS or os.getenv("POSTGRES_PASSWORD", "")

    # 1. Попытка создания бэкапа через Docker (production-стек)
    containers = await docker_client.list_containers(all=False)
    db_container = None
    for c in containers:
        name_lower = c.name.lower()
        if ("db" in name_lower or "postgres" in name_lower) and "pgbouncer" not in name_lower:
            db_container = c.name
            break

    if db_container:
        cmd = ["pg_dump", "-U", user, "-d", db, "--no-owner", "--clean", "--if-exists"]
        env = [f"PGPASSWORD={password}"] if password else None
        exit_code, stdout, stderr = await docker_client.exec_command(db_container, cmd, env=env)
        if exit_code == 0 and stdout:
            compressed = gzip.compress(stdout)
            _save_local_copy(compressed, filename)
            return True, compressed, filename
        err_msg = stderr.decode("utf-8", errors="replace").strip() or f"pg_dump exit code {exit_code}"
        return False, None, f"Ошибка в контейнере {db_container}: {err_msg}"

    # 2. Локальный/тестовый фоллбек: если SQLite
    for sqlite_file in ("test_db.sqlite3", "dev.db", "student_bot.db"):
        if os.path.exists(sqlite_file):
            with open(sqlite_file, "rb") as f:
                compressed = gzip.compress(f.read())
            sqlite_filename = f"oss_bot_backup_{stamp}.sqlite.gz"
            _save_local_copy(compressed, sqlite_filename)
            return True, compressed, sqlite_filename

    return False, None, "Контейнер базы данных не найден или Docker недоступен."


@router.message(Command("backup"))
@router.message(F.text == "💾 Бэкап")
@router.callback_query(F.data == "backup:create")
async def cmd_backup(
    event: Message | CallbackQuery,
    docker_client: DockerClient,
    settings: Settings,
) -> None:
    """Создание и выгрузка резервной копии базы данных."""
    is_callback = isinstance(event, CallbackQuery)
    target_message = (
        event.message if is_callback and event.message else (event if isinstance(event, Message) else None)
    )

    if is_callback:
        await event.answer("Запуск создания бэкапа...")

    status_msg = None
    if target_message:
        status_msg = await target_message.answer(
            "⏳ <b>Создание резервной копии базы данных...</b>\nПожалуйста, подождите, идёт выгрузка данных.",
            parse_mode="HTML",
        )

    ok, data, info = await perform_database_backup(docker_client, settings)

    if ok and data:
        size_kb = len(data) / 1024
        size_str = f"{size_kb / 1024:.2f} МБ" if size_kb > 1024 else f"{size_kb:.1f} КБ"
        doc = BufferedInputFile(file=data, filename=info)
        caption = (
            "✅ <b>Резервная копия базы данных успешно создана!</b>\n\n"
            f"📦 <b>Размер архива:</b> {size_str}\n"
            f"💾 <b>База данных:</b> <code>{settings.DB_NAME}</code>\n"
            f"🕒 <b>Дата создания:</b> <code>{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC</code>\n"
            f"📁 <b>Имя файла:</b> <code>{info}</code>"
        )
        if target_message:
            if len(data) > 50 * 1024 * 1024:
                caption += (
                    "\n\n⚠️ <i>Размер архива превышает 50 МБ (лимит Telegram Bot API). "
                    "Файл сохранён локально на сервере в каталоге бэкапов.</i>"
                )
                await target_message.answer(caption, parse_mode="HTML")
            else:
                try:
                    await target_message.answer_document(document=doc, caption=caption, parse_mode="HTML")
                except Exception as exc:
                    logger.warning("Не удалось отправить файл бэкапа в Telegram: %s", exc)
                    caption += f"\n\n⚠️ <i>Не удалось отправить документ в Telegram: {exc}. Архив сохранён на сервере.</i>"
                    await target_message.answer(caption, parse_mode="HTML")
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.delete()
    else:
        err_text = html.escape(info)
        fail_text = f"❌ <b>Не удалось создать резервную копию:</b>\n\n<code>{err_text}</code>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="backup:create")],
                [InlineKeyboardButton(text="📊 К статусу", callback_data="status:refresh")],
            ]
        )
        if status_msg:
            await status_msg.edit_text(fail_text, reply_markup=kb, parse_mode="HTML")
        elif target_message:
            await target_message.answer(fail_text, reply_markup=kb, parse_mode="HTML")
