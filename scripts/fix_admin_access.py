#!/usr/bin/env python3
"""Скрипт для диагностики и восстановления доступа к веб-админке.

Делает следующее:
1. Проверяет подключение к базе данных
2. Проверяет существование пользователей
3. Создаёт/сбрасывает пароли для указанных пользователей
4. Включает всех пользователей

Примеры:
    python scripts/fix_admin_access.py
    python scripts/fix_admin_access.py --password "NewPassword123" --username admin
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.config import settings
from core.database import async_session_maker
from core.models import WebRole, WebUser
from web.security.passwords import hash_password, verify_password


async def check_db_connection():
    """Проверить подключение к базе данных."""
    print("=" * 60)
    print("ШАГ 1: Проверка подключения к базе данных")
    print("=" * 60)

    print(f"  DB_HOST: {settings.DB_HOST}")
    print(f"  DB_PORT: {settings.DB_PORT}")
    print(f"  DB_NAME: {settings.DB_NAME}")

    try:
        async with async_session_maker() as session:
            await session.execute(select(1))
        print("  ✅ Подключение к БД успешно!")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка подключения к БД: {e}")
        print()
        print("  Возможные причины:")
        print("    1. PostgreSQL не запущен")
        print("    2. Неправильный DB_HOST (проверьте .env)")
        print("    3. БД доступна только через Docker (нужен docker compose up)")
        print()
        print("  Если вы запускаете локально без Docker, убедитесь что:")
        print("    - PostgreSQL установлен и запущен")
        print("    - В .env DB_HOST=localhost (а не 'db')")
        return False


async def check_users():
    """Проверить пользователей в БД."""
    print()
    print("=" * 60)
    print("ШАГ 2: Проверка пользователей")
    print("=" * 60)

    try:
        async with async_session_maker() as session:
            users = await session.execute(select(WebUser))
            result = users.scalars().all()

            if not result:
                print("  ❌ Пользователи не найдены!")
                return []

            print(f"  Найдено пользователей: {len(result)}")
            print()
            for u in result:
                status = "✅ АКТИВЕН" if u.is_active else "❌ ОТКЛЮЧЁН"
                print(f"  👤 {u.username}")
                print(f"      Роль: {u.role.value}")
                print(f"      Статус: {status}")
                print(f"      Хеш пароля: {u.password_hash[:50]}...")
                print()

            return result

    except Exception as e:
        print(f"  ❌ Ошибка при проверке пользователей: {e}")
        return []


async def fix_users(username: str, password: str, role: str = "SUPERADMIN"):
    """Сбросить пароли и включить указанных пользователей."""
    print()
    print("=" * 60)
    print("ШАГ 3: Сброс паролей и включение пользователей")
    print("=" * 60)

    try:
        async with async_session_maker() as session:
            # Получаем всех пользователей или только указанного
            if username:
                users_query = await session.execute(
                    select(WebUser).where(WebUser.username == username)
                )
                users = [users_query.scalar()]
                users = [u for u in users if u is not None]
            else:
                users_query = await session.execute(select(WebUser))
                users = users_query.scalars().all()

            if not users:
                print(f"  ⚠️ Пользователь '{username}' не найден. Создаю нового...")
                # Создаём нового пользователя
                new_user = WebUser(
                    username=username or "admin",
                    password_hash=hash_password(password),
                    role=WebRole[role],
                    is_active=True,
                )
                session.add(new_user)
                await session.commit()
                print(f"  ✅ Создан новый пользователь: {new_user.username}")
                print(f"      Роль: {role}")
                print(f"      Пароль: {password}")
                return

            for user in users:
                old_status = "АКТИВЕН" if user.is_active else "ОТКЛЮЧЁН"
                user.is_active = True
                user.password_hash = hash_password(password)
                user.role = WebRole[role]
                print(f"  👤 {user.username}")
                print(f"      Старый статус: {old_status}")
                print("      Новый статус: АКТИВЕН ✅")
                print(f"      Новая роль: {role}")
                print(f"      Пароль установлен: {'*' * len(password)}")
                print()

            await session.commit()
            print("  ✅ Все изменения сохранены!")

    except Exception as e:
        print(f"  ❌ Ошибка при сбросе паролей: {e}")
        import traceback
        traceback.print_exc()


async def test_password(username: str, password: str):
    """Протестировать, что пароль корректно хешируется и проверяется."""
    print()
    print("=" * 60)
    print("ШАГ 4: Тестирование пароля")
    print("=" * 60)

    hashed = hash_password(password)
    is_valid = verify_password(password, hashed)

    print(f"  Пользователь: {username}")
    print(f"  Пароль: {'*' * len(password)}")
    print(f"  Хеш: {hashed[:60]}...")
    print(f"  Проверка: {'✅ Успешно' if is_valid else '❌ Ошибка'}")


async def main():
    parser = argparse.ArgumentParser(description="Восстановление доступа к веб-админке")
    parser.add_argument("--password", help="Новый пароль (если не указан — спросит)")
    parser.add_argument("--username", help="Имя пользователя (если не указан — сбросит всех)")
    parser.add_argument("--role", default="SUPERADMIN", choices=["SUPERADMIN", "DEPARTMENT_ADMIN", "VIEWER"],
                       help="Роль для пользователя (по умолчанию SUPERADMIN)")
    args = parser.parse_args()

    import getpass

    # Шаг 1: Проверка БД
    db_ok = await check_db_connection()
    if not db_ok:
        print()
        print("⚠️  База данных недоступна. Не могу выполнить сброс паролей.")
        print("   Сначала решите проблему с подключением к БД.")
        sys.exit(1)

    # Шаг 2: Проверка пользователей
    users = await check_users()

    # Шаг 3: Запрос пароля
    if not args.password:
        password = getpass.getpass("\nВведите новый пароль: ")
        confirm = getpass.getpass("Подтвердите пароль: ")
        if password != confirm:
            print("  ❌ Пароли не совпадают!")
            sys.exit(1)
        if len(password) < 8:
            print("  ❌ Пароль должен быть не короче 8 символов!")
            sys.exit(1)
    else:
        password = args.password

    # Шаг 4: Сброс паролей
    await fix_users(args.username, password, args.role)

    # Шаг 5: Тест пароля
    username = args.username or (users[0].username if users else "admin")
    await test_password(username, password)

    print()
    print("=" * 60)
    print("✅ Готово! Теперь можете войти в веб-админку:")
    print(f"   Логин: {username}")
    print(f"   Пароль: {'*' * len(password)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
