"""консольная утилита управления режимом технических работ (maintenance mode)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

# обеспечение видимости корневых пакетов core и web
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.maintenance as maintenance_module


def build_parser() -> argparse.ArgumentParser:
    """построить парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="управление режимом технических работ платформы oss",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # команда status
    status_parser = subparsers.add_parser("status", help="показать текущий статус техработ")
    status_parser.add_argument("--json", action="store_true", help="вывод в формате json")

    # команда enable
    enable_parser = subparsers.add_parser("enable", help="включить режим техработ")
    enable_parser.add_argument(
        "--message", type=str, default="", help="текст сообщения для веб-панели"
    )
    enable_parser.add_argument(
        "--bot-message", type=str, default="", help="текст автоответа в боте"
    )
    enable_parser.add_argument(
        "--by", type=str, default="cli_admin", help="идентификатор инициатора"
    )

    # команда disable
    disable_parser = subparsers.add_parser("disable", help="выключить режим техработ")
    disable_parser.add_argument(
        "--by", type=str, default="cli_admin", help="идентификатор инициатора"
    )

    return parser


async def handle_status(args: argparse.Namespace) -> int:
    """обработать команду status."""
    info = await maintenance_module.get_maintenance_info()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        active = info.get("enabled", False)
        status_label = "включен" if active else "выключен"
        print(f"режим техработ: {status_label}")
        print(f"сообщение web: {info.get('message', '')}")
        print(f"сообщение bot: {info.get('bot_message', '')}")
        print(f"время обновления: {info.get('updated_at', '')}")
        print(f"инициатор: {info.get('updated_by', '')}")
    return 0


async def handle_enable(args: argparse.Namespace) -> int:
    """обработать команду enable."""
    info = await maintenance_module.set_maintenance_mode(
        enabled=True,
        message=args.message,
        bot_message=args.bot_message,
        updated_by=args.by,
    )
    print(f"режим техработ успешно включен. инициатор: {info.get('updated_by')}")
    return 0


async def handle_disable(args: argparse.Namespace) -> int:
    """обработать команду disable."""
    info = await maintenance_module.set_maintenance_mode(
        enabled=False,
        updated_by=args.by,
    )
    print(f"режим техработ успешно выключен. инициатор: {info.get('updated_by')}")
    return 0


async def run_cli(argv: Sequence[str] | None = None) -> int:
    """запустить обработку cli команд."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    if args.command == "status":
        return await handle_status(args)
    if args.command == "enable":
        return await handle_enable(args)
    if args.command == "disable":
        return await handle_disable(args)
    return 1


def main() -> None:
    """точка входа консольной утилиты."""
    sys.exit(asyncio.run(run_cli(sys.argv[1:])))


if __name__ == "__main__":
    main()
