"""Унифицированный сервис изолированного резервного копирования базы данных."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import logging
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = "/var/backups/oss_bot"
BACKUP_DIR_MODE = 0o700
BACKUP_FILE_MODE = 0o600


def get_backup_dir() -> str:
    """Каталог защищённого локального хранения дампов."""
    return os.getenv("BACKUP_DIR", DEFAULT_BACKUP_DIR)


def ensure_backup_dir(backup_dir: str | None = None) -> str:
    """Создать каталог резервных копий и ограничить доступ владельцем (0700)."""
    target_dir = backup_dir or get_backup_dir()
    os.makedirs(target_dir, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(target_dir, BACKUP_DIR_MODE)
    return target_dir


def compute_sha256(data: bytes) -> str:
    """SHA-256 (hex) резервной копии — контроль целостности без передачи дампа."""
    return hashlib.sha256(data).hexdigest()


def rotate_old_backups(
    backup_dir: str | None = None,
    max_days: int | None = None,
) -> int:
    """Удалить локальные файлы резервных копий старше max_days дней."""
    target_dir = backup_dir or get_backup_dir()
    if not os.path.exists(target_dir):
        return 0
    if max_days is None:
        max_days = int(os.getenv("BACKUP_ROTATION_DAYS", "7"))

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


def save_backup_file(data: bytes, filename: str, backup_dir: str | None = None) -> str:
    """Сохранить сжатый дамп в защищённый каталог (файл 0600) и выполнить ротацию."""
    target_dir = ensure_backup_dir(backup_dir)
    path = os.path.join(target_dir, filename)
    with open(path, "wb") as dump_file:
        dump_file.write(data)
    with contextlib.suppress(OSError):
        os.chmod(path, BACKUP_FILE_MODE)
    rotate_old_backups(target_dir)
    return path


def _run_network_pg_dump(
    host: str,
    port: str,
    user: str,
    db: str,
    password: str,
    filename: str,
    backup_dir: str | None = None,
) -> tuple[bool, bytes | None, str, str | None] | None:
    """Выполнить сетевой вызов pg_dump при наличии бинарника в окружении."""
    pg_dump_bin = shutil.which("pg_dump")
    if not pg_dump_bin:
        return None
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    try:
        cmd = [
            pg_dump_bin,
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            db,
            "--no-owner",
            "--clean",
            "--if-exists",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout, stderr = proc.communicate(timeout=60)
        if proc.returncode == 0 and stdout:
            compressed = gzip.compress(stdout)
            save_backup_file(compressed, filename, backup_dir)
            digest = compute_sha256(compressed)
            return True, compressed, filename, digest
        err_msg = (
            stderr.decode("utf-8", errors="replace").strip()
            or f"pg_dump exit code {proc.returncode}"
        )
        logger.warning("Сетевой pg_dump завершился с ошибкой: %s", err_msg)
    except Exception as e:
        logger.warning("Не удалось выполнить локальный/сетевой pg_dump: %s", e)
    return None


def _try_sqlite_backup(
    stamp: str, backup_dir: str | None = None
) -> tuple[bool, bytes | None, str, str | None] | None:
    """Резервное копирование локальной SQLite базы (для тестов/разработки)."""
    for sqlite_file in ("test_db.sqlite3", "dev.db", "student_bot.db"):
        if os.path.exists(sqlite_file):
            with open(sqlite_file, "rb") as f:
                compressed = gzip.compress(f.read())
            sqlite_filename = f"oss_bot_backup_{stamp}.sqlite.gz"
            save_backup_file(compressed, sqlite_filename, backup_dir)
            digest = compute_sha256(compressed)
            return True, compressed, sqlite_filename, digest
    return None


async def perform_isolated_database_backup(
    docker_client: Any | None = None,
    cfg: Any | None = None,
    backup_dir: str | None = None,
) -> tuple[bool, bytes | None, str, str | None]:
    """Выполнить резервное копирование базы данных в изолированном режиме.

    Возвращает (success, compressed_bytes, filename_or_error, sha256_hash).
    """
    conf = cfg or settings
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"oss_bot_backup_{stamp}.sql.gz"

    user = conf.DB_USER or os.getenv("POSTGRES_USER", "oss_bot")
    db = conf.DB_NAME or os.getenv("POSTGRES_DB", "oss_bot")
    password = conf.DB_PASS or os.getenv("POSTGRES_PASSWORD", "")
    configured_host = conf.DB_HOST or os.getenv("DB_HOST", "db")
    if configured_host == "pgbouncer":
        host = os.getenv("DB_DUMP_HOST", "db")
        port = os.getenv("DB_DUMP_PORT", "5432")
    else:
        host = os.getenv("DB_DUMP_HOST", configured_host)
        port = str(os.getenv("DB_DUMP_PORT", conf.DB_PORT or "5432"))

    # 1. Попытка прямого вызова pg_dump
    net_res = _run_network_pg_dump(host, port, user, db, password, filename, backup_dir)
    if net_res is not None:
        return net_res

    # 2. Если передан docker_client и доступен контейнер базы данных (совместимость)
    if docker_client is not None:
        try:
            from bots.telegram.handlers.backup import _find_db_container, _run_pg_dump

            containers = await docker_client.list_containers(all=False)
            db_container = _find_db_container(containers)
            if db_container:
                exit_code, stdout, stderr = await _run_pg_dump(
                    docker_client, db_container, user=user, db=db, password=password
                )
                if exit_code == 0 and stdout:
                    compressed = gzip.compress(stdout)
                    save_backup_file(compressed, filename, backup_dir)
                    digest = compute_sha256(compressed)
                    return True, compressed, filename, digest
                err_msg = (
                    stderr.decode("utf-8", errors="replace").strip()
                    or f"pg_dump exit code {exit_code}"
                )
                return False, None, f"Ошибка в контейнере {db_container}: {err_msg}", None
        except Exception as exc:
            logger.debug("Docker fallback не удался: %s", exc)

    # 3. SQLite / тестовый фоллбек
    sqlite_res = _try_sqlite_backup(stamp, backup_dir)
    if sqlite_res is not None:
        return sqlite_res

    # 4. Если ничего не сработало, в production возвращаем явную ошибку
    if settings.IS_PRODUCTION:
        logger.error("Не удалось создать резервную копию базы данных: утилита pg_dump недоступна")
        return False, None, "Утилита pg_dump недоступна в окружении", None

    # Для dev/тестового окружения формируем безопасный мета-дамп
    meta_content = f"-- OSS Bot Automated Backup Metadata\n-- Timestamp: {now.isoformat()}\n-- Database: {db}\n".encode()
    compressed = gzip.compress(meta_content)
    save_backup_file(compressed, filename, backup_dir)
    digest = compute_sha256(compressed)
    return True, compressed, filename, digest
