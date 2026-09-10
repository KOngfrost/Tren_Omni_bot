#!/usr/bin/env python3
"""Генерация хеша пароля Argon2id для вставки в БД."""

import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.security.passwords import hash_password

if len(sys.argv) > 1:
    password = sys.argv[1]
else:
    password = getpass.getpass(
        "Введите пароль для хеширования (или Enter для генерации случайного): "
    )
    if not password:
        password = secrets.token_urlsafe(16)
        print(f"Сгенерирован случайный пароль: {password}")

hashed = hash_password(password)

print()
print(f"Хеш Argon2id: {hashed}")
