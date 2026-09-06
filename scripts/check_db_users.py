#!/usr/bin/env python3
"""Подключение к БД через Docker и проверка/сброс паролей."""

import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from core.database import async_session_maker
from core.models import WebUser, WebRole
from web.security.passwords import hash_password


async def main():
    # Подключаемся через Docker
    db_url = (
        "postgresql+asyncpg://oss_bot:"
        "@db:5432/oss_bot"
    )
    
    # Читаем пароль из .env
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    postgres_pass = os.getenv("POSTGRES_PASSWORD", "")
    
    db_url = (
        f"postgresql+asyncpg://oss_bot:{postgres_pass}"
        f"@db:5432/oss_bot"
    )
    
    print("=" * 60)
    print("Проверка подключения к БД через Docker")
    print("=" * 60)
    
    try:
        engine = create_async_engine(db_url)
        
        # Тест подключения
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            print("✅ Подключение к БД успешно!")
        
        session_maker = async_sessionmaker(engine)
        async with session_maker() as session:
            # Проверяем пользователей
            users = await session.execute(select(WebUser))
            result = users.scalars().all()
            
            if not result:
                print("\n❌ Пользователи не найдены!")
                print("Создаю первого пользователя...")
                
                password = getpass.getpass("\nВведите пароль для admin: ")
                if len(password) < 8:
                    print("❌ Пароль должен быть не короче 8 символов!")
                    return
                
                new_user = WebUser(
                    username="admin",
                    password_hash=hash_password(password),
                    role=WebRole.SUPERADMIN,
                    is_active=True,
                )
                session.add(new_user)
                await session.commit()
                
                print(f"\n✅ Создан пользователь: admin")
                print(f"   Пароль: {'*' * len(password)}")
                return
            
            print(f"\nНайдено пользователей: {len(result)}")
            print()
            for u in result:
                status = "✅ АКТИВЕН" if u.is_active else "❌ ОТКЛЮЧЁН"
                print(f"  👤 {u.username}")
                print(f"      Роль: {u.role.value}")
                print(f"      Статус: {status}")
                print(f"      Хеш: {u.password_hash[:50]}...")
                print()
            
            # Спрашиваем, нужно ли сбросить пароли
            print("=" * 60)
            choice = input("Сбросить пароли всех пользователей? (y/n): ").strip().lower()
            
            if choice == 'y':
                password = getpass.getpass("\nВведите новый пароль: ")
                if len(password) < 8:
                    print("❌ Пароль должен быть не короче 8 символов!")
                    return
                
                for user in result:
                    user.is_active = True
                    user.password_hash = hash_password(password)
                    print(f"  ✅ Сброшен пароль для: {user.username}")
                
                await session.commit()
                print(f"\n✅ Все пароли сброшены на: {'*' * len(password)}")
    
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
