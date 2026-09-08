#!/usr/bin/env python3
"""Создание/обновление пользователя веб-админки (таблица web_users).

Пароль хранится только в виде хеша:
- Argon2id (предпочтительный, требует argon2-cffi)
- PBKDF2-HMAC-SHA256 (fallback, если argon2-cffi не установлен)

Примеры:
    python scripts/create_web_user.py --username admin --password "S3cret!" --role SUPERADMIN
    python scripts/create_web_user.py --username zhilbyt --password "..." --role DEPARTMENT_ADMIN --department "Жилбыт"
    python scripts/create_web_user.py --username viewer --password "..." --role VIEWER
    python scripts/create_web_user.py --username admin --disable   # отключить пользователя
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import async_session_maker
from core.models import Department, WebRole, WebUser
from web.security.passwords import hash_password


async def create_or_update(
    username: str,
    password: str | None,
    role: WebRole,
    department_name: str | None,
    disable: bool,
) -> None:
    async with async_session_maker() as session:
        web_user = await session.scalar(
            select(WebUser).where(WebUser.username == username)
        )

        department_id = None
        if department_name:
            department = await session.scalar(
                select(Department).where(Department.name == department_name)
            )
            if department is None:
                print(f"❌ Отдел «{department_name}» не найден в departments")
                sys.exit(1)
            department_id = department.id

        if web_user is None:
            if not password:
                print("❌ Для нового пользователя нужен --password")
                sys.exit(1)
            web_user = WebUser(
                username=username,
                password_hash=hash_password(password),
                role=role,
                department_id=department_id,
                is_active=not disable,
            )
            session.add(web_user)
            action = "создан"
        else:
            if password:
                web_user.password_hash = hash_password(password)
            web_user.role = role
            web_user.department_id = department_id
            if disable:
                web_user.is_active = False
            action = "обновлён"

        await session.commit()
        print(f"✅ Пользователь {username} {action}: роль {role.value}, активен: {web_user.is_active}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Создание пользователя веб-админки")
    parser.add_argument("--username", required=True, help="Логин")
    parser.add_argument("--password", help="Пароль (если не указан — спросит скрыто)")
    parser.add_argument(
        "--role",
        default="VIEWER",
        choices=[r.value for r in WebRole],
        help="Роль (по умолчанию VIEWER)",
    )
    parser.add_argument("--department", help="Название отдела (для DEPARTMENT_ADMIN)")
    parser.add_argument("--disable", action="store_true", help="Отключить пользователя")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Пароль: ")
    if len(password) < 8:
        print("❌ Пароль должен быть не короче 8 символов")
        sys.exit(1)

    try:
        asyncio.run(
            create_or_update(
                args.username,
                password,
                WebRole(args.role),
                args.department,
                args.disable,
            )
        )
    except Exception as error:
        print(f"❌ Ошибка: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
