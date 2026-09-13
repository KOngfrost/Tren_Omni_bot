"""Ядро приложения: конфигурация, модели БД, сервисы, клиент VK, outbox."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("student-bot")
except Exception:
    __version__ = "0.8.0"

PROJECT_VERSION = __version__
