"""консольная утилита управления администраторами telegram-бота."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

# обеспечение видимости корневых пакетов core и web
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_settings
import core.telegram_admins as admins_module


def build_parser() -> argparse.ArgumentParser:
    """построить парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="управление авторизованными администраторами telegram",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # команда list
    list_parser = subparsers.add_parser("list", help="показать список администраторов")
    list_parser.add_argument("--json", action="store_true", help="вывод в формате json")

    # команда add
    add_parser = subparsers.add_parser("add", help="добавить нового администратора")
    add_parser.add_argument("admin_id", type=int, help="числовой telegram id")
    add_parser.add_argument("--note", type=str, default="", help="примечание")
    add_parser.add_argument("--by", type=str, default="cli", help="инициатор")

    # команда del
    del_parser = subparsers.add_parser("del", help="удалить администратора")
    del_parser.add_argument("admin_id", type=int, help="числовой telegram id")
    del_parser.add_argument("--by", type=str, default="cli", help="инициатор")

    return parser


async def handle_list(args: argparse.Namespace) -> int:
    """обработать команду list."""
    settings = get_settings()
    root_id = settings.TELEGRAM_ADMIN_ID

    if args.json:
        all_ids = await admins_module.get_authorized_telegram_admin_ids(root_id)
        details = await admins_module.get_admin_details(root_id)
        payload = {
            "root_id": root_id,
            "admin_ids": sorted(list(all_ids)),
            "details": details.get("admins", []),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        details = await admins_module.get_admin_details(root_id)
        print("Список администраторов:")
        for idx, adm in enumerate(details.get("admins", []), 1):
            print(f"{idx}. {adm['role']}: ID {adm['id']} ({adm.get('note', '')})")
    return 0


async def handle_add(args: argparse.Namespace) -> int:
    """обработать команду add."""
    settings = get_settings()
    ok, msg = await admins_module.add_telegram_admin_id(
        admin_id=args.admin_id,
        added_by=args.by,
        root_id=settings.TELEGRAM_ADMIN_ID,
        note=args.note,
    )
    print(f"результат: {msg}")
    return 0 if ok else 1


async def handle_del(args: argparse.Namespace) -> int:
    """обработать команду del."""
    settings = get_settings()
    ok, msg = await admins_module.remove_telegram_admin_id(
        admin_id=args.admin_id,
        root_id=settings.TELEGRAM_ADMIN_ID,
        removed_by=args.by,
    )
    print(f"результат: {msg}")
    return 0 if ok else 1


async def run_cli(argv: Sequence[str] | None = None) -> int:
    """запустить обработку cli команд."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    if args.command == "list":
        return await handle_list(args)
    if args.command == "add":
        return await handle_add(args)
    if args.command == "del":
        return await handle_del(args)
    return 1


def main() -> None:
    """точка входа консольной утилиты."""
    sys.exit(asyncio.run(run_cli(sys.argv[1:])))


if __name__ == "__main__":
    main()
