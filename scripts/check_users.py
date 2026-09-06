#!/usr/bin/env python3
"""Проверка пользователей веб-админки."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import async_session_maker
from core.models import WebUser


async def main():
    async with async_session_maker() as session:
        users = await session.execute(select(WebUser))
        result = users.scalars().all()
        if not result:
            print("❌ Пользователи не найдены!")
            return
        for u in result:
            print(f"Пользователь: {u.username}")
            print(f"  Роль: {u.role.value}")
            print(f"  Активен: {u.is_active}")
            print(f"  Отдел ID: {u.department_id}")
            print(f"  Пароль-хеш: {u.password_hash[:60]}...")
            print()


if __name__ == "__main__":
    asyncio.run(main())
