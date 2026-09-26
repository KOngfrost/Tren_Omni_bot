#!/usr/bin/env python3
"""Генерация pgbouncer/userlist.txt со SCRAM-SHA-256 verifier'ами (SEC-12).

Зачем
-----
PgBouncer в режиме ``auth_query`` берёт пароли всех клиентов прямо из
PostgreSQL, поэтому в ``userlist.txt`` достаточно держать verifier
**одной** служебной роли (``auth_user``), от имени которой выполняется
``auth_query``. Открытые пароли при этом нигде не хранятся — только
SCRAM-верификаторы, которые невозможно обратить в пароль.

Два режима работы
-----------------
* ``postgres`` (по умолчанию) — подключается к серверу БД и читает
  готовый верификатор из ``pg_authid``. Точный, ничего не пересчитывает.
* ``derive`` — если прямой доступ к ``pg_authid`` невозможен, верификатор
  вычисляется из пароля локально (реализация RFC 5802, совместимая с
  PostgreSQL). Крайний случай.

Использование
-------------
::

    # Прочитать готовый верификатор из БД (рекомендуется):
    python scripts/generate_pgbouncer_userlist.py

    # Вычислить из пароля без доступа к БД:
    python scripts/generate_pgbouncer_userlist.py --mode derive --password '...'
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import os
import secrets
import stat
import sys
from pathlib import Path

# Значения по умолчанию для PostgreSQL (совпадают с его внутренними
# настройками SCRAM). Менять их не следует: сервер БД проверяет, что
# итерации верификатора не ниже своих собственных.
SCRAM_DEFAULT_ITERATIONS = 4096
SCRAM_SALT_BYTES = 16
SCRAM_KEY_LENGTH = 32

# Стандартные строки протокола SCRAM (RFC 5802, раздел 3).
_CLIENT_KEY = b"Client Key"
_SERVER_KEY = b"Server Key"

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_PATH = _REPO_ROOT / "pgbouncer" / "userlist.txt"


# === Вычисление SCRAM-SHA-256 (RFC 5802) ===


def _hi(password: bytes, salt: bytes, iterations: int) -> bytes:
    """SaltedPassword = Hi(Normalize(password), salt, iterations)."""
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, SCRAM_KEY_LENGTH)


def derive_scram_verifier(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = SCRAM_DEFAULT_ITERATIONS,
) -> str:
    """Вычислить PostgreSQL-совместимый SCRAM-SHA-256 верификатор.

    Возвращает строку того же формата, что PostgreSQL хранит в pg_authid::

        SCRAM-SHA-256$4096:<base64 salt>$<base64 storedkey>:<base64 serverkey>
    """
    if not password:
        raise ValueError("Пароль не может быть пустым")
    if iterations < 4096:
        raise ValueError("Число итераций меньше 4096 ослабляет защиту и не совпадёт с сервером")

    raw_salt = salt if salt is not None else secrets.token_bytes(SCRAM_SALT_BYTES)
    if len(raw_salt) < 8:
        raise ValueError("SALT должен быть не короче 8 байт")

    salted_password = _hi(password.encode("utf-8"), raw_salt, iterations)

    client_key = hmac.new(salted_password, _CLIENT_KEY, hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, _SERVER_KEY, hashlib.sha256).digest()

    b64_salt = base64.b64encode(raw_salt).decode("ascii")
    b64_stored = base64.b64encode(stored_key).decode("ascii")
    b64_server = base64.b64encode(server_key).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64_salt}${b64_stored}:{b64_server}"


# === Чтение готового верификатора из PostgreSQL ===


def fetch_verifier_from_postgres(dsn: str, user: str, *, timeout: float = 10.0) -> str:
    """Прочитать готовый SCRAM-верификатор роли из ``pg_authid``.

    Используется asyncpg из requirements.txt — драйвер уже есть в проекте,
    поэтому дополнительная зависимость не требуется.
    """
    import asyncio

    import asyncpg  # type: ignore[import-not-found]

    async def _query() -> str | None:
        conn = await asyncpg.connect(dsn=dsn, timeout=timeout)
        try:
            return await conn.fetchval(
                "SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = $1",
                user,
            )
        finally:
            await conn.close()

    try:
        verifier = asyncio.run(_query())
    except Exception as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError(f"не удалось подключиться к PostgreSQL: {exc}") from exc

    if not verifier:
        raise RuntimeError(f"у роли {user!r} нет сохранённого пароля (rolpassword пуст)")
    return str(verifier)


# === Запись файла ===


def write_userlist(lines: list[str], out_path: Path) -> None:
    """Записать userlist.txt с правами для чтения PgBouncer."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Права 0644 позволяют процессу PgBouncer (UID 70 в alpine-образе)
    # читать файл, даже если он был сгенерирован под root.
    os.chmod(out_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        os.chown(out_path, 70, 70)
    except (PermissionError, AttributeError):
        pass


def _build_line(user: str, verifier: str) -> str:
    """Строка userlist.txt: ``"user" "SCRAM-SHA-256$..."``."""
    if not verifier.startswith("SCRAM-SHA-256$"):
        raise ValueError(
            f"получен не SCRAM-SHA-256 верификатор для {user!r}: {verifier[:16]}... "
            "Установите пароль роли в PostgreSQL через ALTER ROLE ... PASSWORD, "
            "чтобы сервер использовал SCRAM."
        )
    # Экранирование обязательно: значение подставляется в формат
    # PgBouncer как строка в кавычках.
    escaped = verifier.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{user}" "{escaped}"'


def _read_dsn_from_env() -> str | None:
    """Собрать DSN из переменных окружения проекта (core.config читает .env)."""
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from core.config import settings
    except Exception:  # pragma: no cover - конфиг не нужен для derive-режима
        return None

    host = settings.db_host
    port = settings.DB_PORT or "5432"
    name = settings.DB_NAME
    if not (host and settings.DB_USER and name):
        return None

    from urllib.parse import quote_plus

    auth = quote_plus(settings.DB_USER)
    if settings.DB_PASS:
        auth += f":{quote_plus(settings.DB_PASS)}"
    return f"postgresql://{auth}@{host}:{port}/{name}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Генерация pgbouncer/userlist.txt со SCRAM-SHA-256 verifier'ами (SEC-12).",
    )
    parser.add_argument(
        "--user",
        default=os.getenv("POSTGRES_USER", ""),
        help="роль PostgreSQL, для которой нужен верификатор "
        "(по умолчанию POSTGRES_USER из окружения)",
    )
    parser.add_argument(
        "--dsn",
        default="",
        help="DSN для чтения pg_authid (по умолчанию собирается из core.config)",
    )
    parser.add_argument(
        "--password",
        default="",
        help="пароль для режима --mode derive (иначе запрашивается интерактивно)",
    )
    parser.add_argument(
        "--mode",
        choices=("postgres", "derive"),
        default="postgres",
        help="postgres — прочитать готовый верификатор из БД; " "derive — вычислить его из пароля",
    )
    parser.add_argument(
        "--out",
        default="",
        help="путь к userlist.txt (по умолчанию pgbouncer/userlist.txt); "
        "пустая строка — только вывод в stdout",
    )
    args = parser.parse_args()

    user = args.user.strip()
    if not user:
        print("ОШИБКА: не задана роль (--user или POSTGRES_USER)", file=sys.stderr)
        return 2

    if args.mode == "postgres":
        dsn = args.dsn or _read_dsn_from_env()
        if not dsn:
            print(
                "ОШИБКА: не удалось определить DSN. Передайте --dsn "
                "или используйте --mode derive.",
                file=sys.stderr,
            )
            return 2
        try:
            verifier = fetch_verifier_from_postgres(dsn, user)
        except RuntimeError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 1
    else:
        password = args.password or getpass.getpass(f"Пароль роли {user}: ")
        try:
            verifier = derive_scram_verifier(password)
        except ValueError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 2

    try:
        line = _build_line(user, verifier)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(line)

    if args.out:
        out_path = Path(args.out)
    elif sys.stdout.isatty():
        # Интерактивный запуск: пишем в стандартный путь проекта.
        out_path = DEFAULT_OUT_PATH
    else:
        # Неинтерактивный запуск (например, docker compose run):
        # результат перенаправляет оператор, файл не трогаем.
        out_path = None

    if out_path is not None:
        write_userlist([line], out_path)
        print(f"Записан файл: {out_path} (права 0600)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
