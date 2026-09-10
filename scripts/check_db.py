import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Не заданы переменные: {', '.join(missing)}")

    try:
        conn = await asyncpg.connect(
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            database=os.getenv("POSTGRES_MAINTENANCE_DB", "postgres"),
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
        )
        dbs = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname = $1",
            os.environ["POSTGRES_DB"],
        )
        print("exists", bool(dbs))
        await conn.close()
    except Exception as exc:
        print(type(exc).__name__, str(exc))


asyncio.run(main())
