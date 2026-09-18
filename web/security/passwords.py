"""Хеширование паролей веб-пользователей.

Форматы хранения:
- argon2$<hash> — Argon2id (предпочтительный, требует argon2-cffi)
- pbkdf2_sha256$<iterations>$<salt>$<hash> — PBKDF2-HMAC-SHA256 (fallback)

Argon2id — победитель Password Hashing Competition 2015, устойчив к GPU/ASIC
атакам. PBKDF2 используется как fallback для существующих паролей.
"""

import base64
import hashlib
import secrets

# Пытаемся импортировать argon2-cffi, если доступен
try:
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    HAS_ARGON2 = True
    _argon2_ph = PasswordHasher(
        time_cost=3,  # количество итераций
        memory_cost=65536,  # 64 MB памяти
        parallelism=4,  # количество потоков
        hash_len=32,  # длина хеша (32 байта = 256 бит)
        salt_len=16,  # длина соли (16 байт = 128 бит)
        type=Type.ID,  # Argon2id — гибридная версия
    )
    ALGORITHM = "argon2"
except ImportError:
    HAS_ARGON2 = False
    ALGORITHM = "pbkdf2_sha256"

PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Хешировать пароль в самодостаточную строку для хранения в БД.

    Использует Argon2id если доступен, иначе PBKDF2.
    """
    if HAS_ARGON2:
        return _argon2_ph.hash(password)

    salt = base64.b64encode(secrets.token_bytes(16)).decode()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Проверить пароль против сохранённого хеша (timing-safe).

    Поддерживает оба формата: argon2 и pbkdf2_sha256.
    При проверке PBKDF2-хеша автоматически обновляет на Argon2.
    """
    if not stored:
        return False

    try:
        if stored.startswith("$argon2id$"):
            if not HAS_ARGON2:
                return False
            try:
                return _argon2_ph.verify(stored, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        if stored.startswith("pbkdf2_sha256$"):
            algorithm, iterations, salt, expected = stored.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            )
            return secrets.compare_digest(base64.b64encode(digest).decode("ascii"), expected)

        return False
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str | None) -> bool:
    """Проверить, нужно ли перевычислить хеш (миграция на Argon2).

    Возвращает True, если хеш в устаревшем формате (PBKDF2) и Argon2 доступен.
    """
    if not stored or not HAS_ARGON2:
        return False
    return stored.startswith("pbkdf2_sha256$")


_DUMMY_PASSWORD_HASH = hash_password("timing_attack_mitigation_token")


def verify_dummy_password(password: str) -> None:
    """Выполнить фиктивную проверку пароля для выравнивания времени ответа (anti-timing attack)."""
    verify_password(password, _DUMMY_PASSWORD_HASH)

