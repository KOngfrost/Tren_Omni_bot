#!/usr/bin/env python3
"""Скрипт для связывания несвязанных WebUser с Admin.

Скрипт выполняет следующие действия:
1. Находит всех WebUser с admin_id = NULL
2. Для DEPARTMENT_ADMIN ищет соответствующую запись в Admin по department_id
3. Для SUPERADMIN — связывает с первым найденным SUPERADMIN
4. Обновляет admin_id в web_users
5. Показывает статистику до и после

Примеры:
    python scripts/fix_web_user_admin_links.py          # Dry-run (показать что будет)
    python scripts/fix_web_user_admin_links.py --apply   # Применить изменения
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import async_session_maker
from core.models import Admin, UserRole, WebRole, WebUser


async def diagnose() -> dict:
    """Диагностика: показать статистику несвязанных WebUser."""
    async with async_session_maker() as session:
        # Всего WebUser
        all_users = (await session.scalars(select(WebUser))).all()
        all_users_list = list(all_users)

        # Несвязанные (admin_id IS NULL)
        orphaned = (await session.scalars(select(WebUser).where(WebUser.admin_id.is_(None)))).all()

        # По ролям
        by_role = {}
        for role in WebRole:
            count = sum(1 for u in orphaned if u.role == role)
            by_role[role.value] = count

        # С несвязанными Admin (admin_id IS NULL, но user_id не NULL)
        orphaned_admins = (
            await session.scalars(
                select(Admin).where(Admin.web_user.has(WebUser.admin_id.is_(None)))
            )
        ).all()

        return {
            "total_web_users": len(all_users_list),
            "orphaned_web_users": len(orphaned),
            "by_role": by_role,
            "orphaned_admins": len(orphaned_admins),
            "orphaned_details": [
                {
                    "username": u.username,
                    "role": u.role.value if u.role else "UNKNOWN",
                    "department_id": u.department_id,
                    "department_name": u.department.name if u.department else None,
                }
                for u in orphaned
            ],
        }


async def fix_links(apply: bool = False) -> dict:
    """Связать несвязанных WebUser с Admin."""
    async with async_session_maker() as session:
        # Суперадминистраторы для связывания SUPERADMIN
        superadmins = (
            await session.scalars(select(Admin).where(Admin.role == UserRole.SUPERADMIN))
        ).all()

        # Администраторы отделов
        dept_admins = (
            await session.scalars(select(Admin).where(Admin.department_id.isnot(None)))
        ).all()

        # Группируем department admins по department_id
        dept_admin_by_dept = {}
        for admin in dept_admins:
            if admin.department_id not in dept_admin_by_dept:
                dept_admin_by_dept[admin.department_id] = admin

        # Несвязанные WebUser
        orphaned = (await session.scalars(select(WebUser).where(WebUser.admin_id.is_(None)))).all()

        fixed = []
        skipped = []

        for web_user in orphaned:
            new_admin_id = None

            if web_user.role == WebRole.DEPARTMENT_ADMIN and web_user.department_id:
                # Для DEPARTMENT_ADMIN ищем Admin в том же отделе
                target_admin = dept_admin_by_dept.get(web_user.department_id)
                if target_admin:
                    new_admin_id = target_admin.id
                else:
                    skipped.append(
                        {
                            "username": web_user.username,
                            "reason": f"Нет Admin в отделе {web_user.department_id}",
                        }
                    )
                    continue

            elif web_user.role == WebRole.SUPERADMIN:
                # Для SUPERADMIN связываем с первым SUPERADMIN
                if superadmins:
                    new_admin_id = superadmins[0].id
                else:
                    skipped.append(
                        {
                            "username": web_user.username,
                            "reason": "Нет SUPERADMIN для связывания",
                        }
                    )
                    continue

            if new_admin_id:
                web_user.admin_id = new_admin_id
                fixed.append(
                    {
                        "username": web_user.username,
                        "old_admin_id": None,
                        "new_admin_id": new_admin_id,
                        "role": web_user.role.value,
                    }
                )

        if apply:
            await session.commit()

        return {
            "fixed": fixed,
            "skipped": skipped,
            "total_fixed": len(fixed),
            "total_skipped": len(skipped),
        }


async def main():
    parser = argparse.ArgumentParser(description="Диагностика и связывание WebUser с Admin")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения (без --apply только диагностика)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("ДИАГНОСТИКА WebUser -> Admin связей")
    print("=" * 60)

    diag = await diagnose()

    print(f"\nВсего WebUser: {diag['total_web_users']}")
    print(f"Несвязанных (admin_id IS NULL): {diag['orphaned_web_users']}")
    print(f"Несвязанных Admin: {diag['orphaned_admins']}")

    print("\nПо ролям (несвязанные):")
    for role, count in diag["by_role"].items():
        print(f"  {role}: {count}")

    if diag["orphaned_details"]:
        print("\nДетали несвязанных WebUser:")
        for detail in diag["orphaned_details"]:
            dept = detail["department_name"] or f"ID={detail['department_id']}"
            print(f"  - {detail['username']} ({detail['role']}, отдел: {dept})")

    if not args.apply:
        print("\n" + "=" * 60)
        print("Dry-run режим. Добавьте --apply для применения изменений.")
        print("=" * 60)
        return

    print("\n" + "=" * 60)
    print("ПРИМЕНЕНИЕ ИЗМЕНЕНИЙ")
    print("=" * 60)

    result = await fix_links(apply=True)

    print(f"\nИсправлено: {result['total_fixed']}")
    for fix in result["fixed"]:
        print(f"  ✅ {fix['username']} ({fix['role']}) -> admin_id={fix['new_admin_id']}")

    if result["skipped"]:
        print(f"\nПропущено: {result['total_skipped']}")
        for skip in result["skipped"]:
            print(f"  ⚠️  {skip['username']}: {skip['reason']}")

    print("\n" + "=" * 60)
    print("✅ Готово!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
