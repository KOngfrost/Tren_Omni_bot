#!/usr/bin/env python3
"""
Скрипт для инициализации первого суперадмина.
Можно запускать для каждого нового суперадмина.

Пример:
    python scripts/init_superadmin.py --vk-id 193626953 --name "Иванов Иван"
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для импорта модулей
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import async_session_maker
from core.models import Admin, User, UserRole


async def init_superadmin(vk_id: int, full_name: str = "") -> None:
    """Создаёт или повышает одного пользователя до суперадмина."""
    async with async_session_maker() as session:
        # Получаем или создаём пользователя
        user_result = await session.execute(select(User).where(User.vk_id == vk_id))
        user: User | None = user_result.scalar_one_or_none()

        if not user:
            user = User(vk_id=vk_id, full_name=full_name or None)
            session.add(user)
            await session.flush()
            print(f"[OK] Создан пользователь: {full_name or f'VK:{vk_id}'}")
        else:
            if full_name and not user.full_name:
                user.full_name = full_name
                print(f"[OK] Обновлено имя: {full_name}")
            else:
                print(f"[ИНФО] Пользователь уже существует: {user.full_name or vk_id}")

        existing_admin: Admin | None = await session.scalar(
            select(Admin).where(Admin.user_id == user.id)
        )
        if existing_admin is not None:
            if existing_admin.role == UserRole.SUPERADMIN:
                print(f"[ИНФО] Пользователь уже является суперадмином: VK {vk_id}")
                return
            existing_admin.role = UserRole.SUPERADMIN
            existing_admin.department_id = None
            await session.commit()
            print(f"[OK] Администратор повышен до суперадмина: VK {vk_id}")
            return

        # Создаём очередного суперадмина
        admin: Admin = Admin(
            user_id=user.id,
            department_id=None,
            role=UserRole.SUPERADMIN,
        )
        session.add(admin)
        await session.commit()

        print("\n[OK] Суперадмин успешно создан!")
        print(f"   Имя: {full_name or 'Не указано'}")
        print(f"   VK ID: {vk_id}")
        print("\nДля входа в веб-админку задайте в .env:")
        print("   WEB_ADMIN_USERNAME=<ваш логин>")
        print("   WEB_ADMIN_PASSWORD=<ваш пароль>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Инициализация суперадмина")
    parser.add_argument("--vk-id", type=int, required=True, help="VK ID пользователя")
    parser.add_argument("--name", type=str, default="", help="ФИО пользователя")
    args = parser.parse_args()

    try:
        asyncio.run(init_superadmin(args.vk_id, args.name))
    except Exception as e:
        print(f"[ОШИБКА] Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
