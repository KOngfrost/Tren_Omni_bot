#!/usr/bin/env python3
"""Генерация хеша пароля Argon2id для вставки в БД."""

import sys
sys.path.insert(0, '.')

from web.security.passwords import hash_password

# Создаём хеш для указанного пароля
password = "REDACTED_PASSWORD"  # Новый пароль по умолчанию
hashed = hash_password(password)

print(f"Пароль: {password}")
print(f"Хеш: {hashed}")
print()
print(f'UPDATE web_users SET password_hash = \'{hashed}\' WHERE username = \'dummy_admin\';')
print(f'INSERT INTO web_users (username, password_hash, role, is_active) VALUES (\'admin\', \'{hashed}\', \'SUPERADMIN\', true);')
