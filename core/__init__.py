"""Ядро приложения: конфигурация, модели БД, сервисы, клиент VK, outbox."""

import importlib.metadata
import os
from pathlib import Path


def _resolve_project_version() -> str:
    """Определить версию приложения.
    
    Приоритет:
    1. Переменные окружения APP_VERSION / PROJECT_VERSION / VERSION.
    2. Файл .env в корне проекта (если переменная не была экспортирована в OS env).
    3. Файл pyproject.toml в корне проекта.
    4. Метаданные установленного пакета (importlib.metadata).
    5. Запасное значение по умолчанию.
    """
    # 1. Проверяем окружение процесса
    env_ver = os.getenv("APP_VERSION") or os.getenv("PROJECT_VERSION") or os.getenv("VERSION")
    if env_ver and env_ver.strip():
        return env_ver.strip()

    # 2. Проверяем локальный файл .env
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        if k in ("APP_VERSION", "PROJECT_VERSION", "VERSION"):
                            val = v.strip().strip("'\"")
                            if val:
                                return val
        except Exception:
            pass

    # 3. Читаем из pyproject.toml через tomllib
    try:
        import tomllib

        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject_path.is_file():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                ver = data.get("project", {}).get("version")
                if ver:
                    return str(ver).strip()
    except Exception:
        pass

    # 4. Метаданные пакета
    try:
        return importlib.metadata.version("student-bot")
    except Exception:
        pass

    # 5. Фоллбэк
    return "0.8.4"


__version__ = _resolve_project_version()
PROJECT_VERSION = __version__
APP_VERSION = __version__

__all__ = ["__version__", "PROJECT_VERSION", "APP_VERSION"]
