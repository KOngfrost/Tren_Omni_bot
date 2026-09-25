"""Хендлеры и сервис резервного копирования базы данных PostgreSQL."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import html
import logging
import os
import time
import uuid
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bots.telegram.docker_client import ContainerInfo, DockerClient
from core.config import Settings

logger = logging.getLogger(__name__)
router = Router(name="backup")

# Каталог защищённого локального хранения дампов. В контейнере tg-monitor он
# смонтирован с хоста (см. docker-compose.yml: /var/backups/oss_bot).
DEFAULT_BACKUP_DIR = "/var/backups/oss_bot"
# SEC-07 / 152-ФЗ: доступ к дампам с ПДн ограничен владельцем —
# 0700 на каталог, 0600 на файл. Сырые дампы в Telegram не передаются.
BACKUP_DIR_MODE = 0o700
BACKUP_FILE_MODE = 0o600
# Срок хранения локальных копий (дней) для ротации (настраивается в .env).
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_ROTATION_DAYS", "7"))


def _backup_dir() -> str:
    """Каталог защищённого локального хранения дампов (переопределяется BACKUP_DIR)."""
    return os.getenv("BACKUP_DIR", DEFAULT_BACKUP_DIR)


def rotate_old_backups(
    backup_dir: str | None = None, max_days: int = BACKUP_RETENTION_DAYS
) -> int:
    """Удалить локальные файлы резервных копий старше max_days дней.

    Возвращает количество удаленных файлов.
    """
    target_dir = backup_dir or _backup_dir()
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
                    logger.warning(
                        "Не удалось удалить устаревший файл бэкапа %s: %s", entry.name, exc
                    )
    except Exception as e:
        logger.warning("Ошибка сканирования каталога бэкапов %s: %s", target_dir, e)
    return deleted_count


def compute_sha256(data: bytes) -> str:
    """SHA-256 (hex) резервной копии — контроль целостности без передачи дампа."""
    return hashlib.sha256(data).hexdigest()


def _ensure_backup_dir(backup_dir: str | None = None) -> str:
    """Создать каталог резервных копий и ограничить доступ владельцем (0700)."""
    target_dir = backup_dir or _backup_dir()
    os.makedirs(target_dir, exist_ok=True)
    with contextlib.suppress(OSError):  # ФС без поддержки chmod — не критично
        os.chmod(target_dir, BACKUP_DIR_MODE)
    return target_dir


def _save_local_copy(data: bytes, filename: str) -> str:
    """Сохранить дамп в защищённый каталог (файл 0600) и выполнить ротацию.

    Возвращает путь к сохранённому файлу. Ошибки записи пробрасываются
    вызывающему коду: без локальной копии резервное копирование считается
    неуспешным, поскольку дамп больше не пересылается в Telegram.
    """
    target_dir = _ensure_backup_dir()
    path = os.path.join(target_dir, filename)
    with open(path, "wb") as dump_file:
        dump_file.write(data)
    with contextlib.suppress(OSError):
        os.chmod(path, BACKUP_FILE_MODE)
    rotate_old_backups(target_dir, max_days=BACKUP_RETENTION_DAYS)
    return path


def _store_backup(data: bytes, filename: str) -> tuple[bool, bytes | None, str]:
    """Сохранить сжатый дамп локально и вернуть результат в формате perform_database_backup."""
    try:
        _save_local_copy(data, filename)
    except Exception as exc:
        logger.error("Не удалось сохранить резервную копию %s: %s", filename, exc)
        return False, None, f"Не удалось сохранить резервную копию в защищённый каталог: {exc}"
    return True, data, filename


def _find_db_container(containers: list[ContainerInfo]) -> str | None:
    """Найти контейнер PostgreSQL среди запущенных контейнеров (кроме pgbouncer)."""
    for container in containers:
        name_lower = container.name.lower()
        if ("db" in name_lower or "postgres" in name_lower) and "pgbouncer" not in name_lower:
            return container.name
    return None


async def _run_pg_dump(
    docker_client: DockerClient,
    db_container: str,
    user: str,
    db: str,
    password: str,
) -> tuple[int, bytes, bytes]:
    """Выполнить pg_dump в контейнере БД, не раскрывая пароль в env/argv (SEC-06).

    Пароль передаётся исключительно через временный файл ``.pgpass`` с правами
    0600, который загружается в контейнер через Docker Archive API и
    гарантированно удаляется после завершения команды. Передача ``PGPASSWORD``
    через ``Env`` exec-процесса (значение видно в ``/exec/{id}/json``) и через
    аргументы командной строки запрещена.
    """
    if not password:
        # Секрета нет (локальный trust-доступ) — временный .pgpass не требуется.
        return await docker_client.exec_command(
            db_container, ["pg_dump", "-U", user, "-d", db, "--no-owner", "--clean", "--if-exists"]
        )

    pgpass_path = f"/tmp/.oss_bot_backup_{uuid.uuid4().hex[:12]}.pgpass"
    # Формат libpq: host:port:database:user:password ('*' — любое значение поля).
    pgpass_line = f"*:*:*:{user}:{password}\n"
    uploaded = await docker_client.upload_file(
        db_container, pgpass_path, pgpass_line.encode("utf-8"), mode=BACKUP_FILE_MODE
    )
    if not uploaded:
        return -1, b"", b"Failed to upload temporary .pgpass into the database container."

    # $1..$3 передаются как аргументы sh: секрет не попадает в строку команды.
    script = """
chmod 600 "$1" 2>/dev/null || true
test -r "$1" || { echo "temporary .pgpass is not readable by $(id -un)" >&2; exit 90; }
PGPASSFILE="$1" pg_dump -U "$2" -d "$3" --no-owner --clean --if-exists
rc=$?
rm -f "$1"
exit $rc
"""
    try:
        return await docker_client.exec_command(
            db_container, ["sh", "-c", script, "oss-bot-backup", pgpass_path, user, db]
        )
    finally:
        # Двойная гарантия: убираем секрет из контейнера, даже если скрипт прервался.
        with contextlib.suppress(Exception):
            await docker_client.exec_command(db_container, ["rm", "-f", pgpass_path])


async def perform_database_backup(
    docker_client: DockerClient,
    settings: Settings,
) -> tuple[bool, bytes | None, str]:
    """Выполнить резервное копирование базы данных.

    Возвращает (success, compressed_dump_bytes, filename_or_error).
    Сжатый дамп сохраняется ТОЛЬКО в защищённый локальный каталог, а наружу
    (в Telegram) передаётся лишь отчёт о копии — см. SEC-07 / 152-ФЗ.
    """
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"oss_bot_backup_{stamp}.sql.gz"

    user = settings.DB_USER or os.getenv("POSTGRES_USER", "oss_bot")
    db = settings.DB_NAME or os.getenv("POSTGRES_DB", "oss_bot")
    password = settings.DB_PASS or os.getenv("POSTGRES_PASSWORD", "")

    # 1. Попытка создания бэкапа через Docker (production-стек)
    containers = await docker_client.list_containers(all=False)
    db_container = _find_db_container(containers)

    if db_container:
        exit_code, stdout, stderr = await _run_pg_dump(
            docker_client, db_container, user=user, db=db, password=password
        )
        if exit_code == 0 and stdout:
            compressed = gzip.compress(stdout)
            return _store_backup(compressed, filename)
        err_msg = (
            stderr.decode("utf-8", errors="replace").strip() or f"pg_dump exit code {exit_code}"
        )
        return False, None, f"Ошибка в контейнере {db_container}: {err_msg}"

    # 2. Локальный/тестовый фоллбек: если SQLite
    for sqlite_file in ("test_db.sqlite3", "dev.db", "student_bot.db"):
        if os.path.exists(sqlite_file):
            with open(sqlite_file, "rb") as f:
                compressed = gzip.compress(f.read())
            sqlite_filename = f"oss_bot_backup_{stamp}.sqlite.gz"
            return _store_backup(compressed, sqlite_filename)

    return False, None, "Контейнер базы данных не найден или Docker недоступен."


def _build_success_report(data: bytes, filename: str, settings: Settings, digest: str) -> str:
    """Сформировать текстовый отчёт о резервной копии (без самого дампа).

    SEC-07 / 152-ФЗ: сырой дамп с персональными данными не пересылается в
    Telegram — администратор получает только метаданные и SHA-256 файла.
    """
    size_kb = len(data) / 1024
    size_str = f"{size_kb / 1024:.2f} МБ" if size_kb > 1024 else f"{size_kb:.1f} КБ"
    created_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "✅ <b>Резервная копия базы данных успешно создана!</b>\n\n"
        f"📦 <b>Размер архива:</b> {size_str}\n"
        f"💾 <b>База данных:</b> <code>{html.escape(settings.DB_NAME)}</code>\n"
        f"🕒 <b>Дата создания:</b> <code>{created_at} UTC</code>\n"
        f"📁 <b>Имя файла:</b> <code>{html.escape(filename)}</code>\n"
        f"🔐 <b>SHA-256:</b> <code>{digest}</code>\n"
        f"🗂 <b>Каталог хранения:</b> <code>{html.escape(_backup_dir())}</code> (права 0600)\n\n"
        "ℹ️ <i>Дамп с персональными данными не отправляется в чат: файл остаётся на "
        "сервере. Передача копии вовне — только по защищённому каналу (SSH/S3).</i>"
    )


@router.message(Command("backup"))
@router.message(F.text == "💾 Бэкап")
@router.callback_query(F.data == "backup:create")
async def cmd_backup(
    event: Message | CallbackQuery,
    docker_client: DockerClient,
    settings: Settings,
) -> None:
    """Создание резервной копии БД и отправка администратору отчёта (без дампа)."""
    is_callback = isinstance(event, CallbackQuery)
    target_message = (
        event.message
        if is_callback and event.message
        else (event if isinstance(event, Message) else None)
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
        digest = compute_sha256(data)
        report = _build_success_report(data, info, settings, digest=digest)
        logger.info(
            "Резервная копия %s сохранена локально (%.1f КБ, SHA-256 %s)",
            info,
            len(data) / 1024,
            digest,
        )
        delivered = False
        if status_msg:
            try:
                await status_msg.edit_text(report, parse_mode="HTML")
                delivered = True
            except Exception as exc:
                logger.warning("Не удалось обновить сообщение о статусе бэкапа: %s", exc)
        if not delivered and target_message:
            await target_message.answer(report, parse_mode="HTML")
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
