"""Настройка логирования для проекта OSS Bot.

Выводит логи в stdout и в файлы с ротацией:
- oss_bot.log — все логи уровня INFO и выше
- oss_bot_error.log — только ошибки (ERROR и выше)

Ротация: ежедневно или при достижении 10 MB.
Хранится 30 архивных файлов.
"""

import json
import logging
import logging.handlers
import os
import re
import sys

_SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(password|passwd|pwd)\s*([=:])\s*([\'"]?)([^\'"\s,;]+)\3'), r'\1\2\3***\3'),
    (re.compile(r'(?i)(token|access_token|bot_token|secret|secret_key|api_key)\s*([=:])\s*([\'"]?)([^\'"\s,;]+)\3'), r'\1\2\3***\3'),
    (re.compile(r'vk1\.a\.[a-zA-Z0-9_\-]+'), r'vk1.a.***'),
    (re.compile(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]+'), r'Bearer ***'),
    (re.compile(r'(?i)session=([a-zA-Z0-9_\-\.]{10,})'), r'session=***'),
]


def mask_sensitive_data(text: str) -> str:
    """Маскирует токены, пароли и секреты в строке лога."""
    if not isinstance(text, str) or not text:
        return text or ""
    res = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        res = pattern.sub(replacement, res)
    return res


class SensitiveDataFilter(logging.Filter):
    """Фильтр для автоматического маскирования конфиденциальных данных во всех логах."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (mask_sensitive_data(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_sensitive_data(a) if isinstance(a, str) else a for a in record.args)
        else:
            if isinstance(record.msg, str):
                record.msg = mask_sensitive_data(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    """JSON-форматер для централизованных систем логирования (ELK/Loki).

    Включается переменной окружения LOG_FORMAT=json.
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = mask_sensitive_data(record.getMessage())
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        if record.exc_info:
            payload["exc_info"] = mask_sensitive_data(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class SanitizedFormatter(logging.Formatter):
    """Текстовый форматер с гарантированным маскированием чувствительных данных."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return mask_sensitive_data(formatted)


def _make_formatter() -> logging.Formatter:
    """Выбрать форматер: JSON (LOG_FORMAT=json) или текстовый (по умолчанию)."""
    if os.getenv("LOG_FORMAT", "text").strip().lower() == "json":
        return JsonFormatter()
    return SanitizedFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.INFO,
) -> None:
    """Настроить логирование в stdout и файлы.

    Вызывается один раз при запуске бота или панели.
    Формат вывода переключается переменной окружения LOG_FORMAT
    (text — по умолчанию, json — для ELK/Loki).

    Args:
        log_dir: Директория для логов (создаётся автоматически).
        level: Уровень логирования по умолчанию.
    """
    # Создаём директорию для логов
    os.makedirs(log_dir, exist_ok=True)

    # Форматер для логов (text или json — см. LOG_FORMAT)
    formatter = _make_formatter()

    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очистим предыдущие handlers (если setup_logging вызывается повторно)
    root_logger.handlers.clear()

    sensitive_filter = SensitiveDataFilter()
    root_logger.addFilter(sensitive_filter)

    # Handler для stdout (всегда активен)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(sensitive_filter)
    root_logger.addHandler(stdout_handler)

    # Handler для общего лога (INFO и выше)
    general_log = os.path.join(log_dir, "oss_bot.log")
    general_handler = logging.handlers.RotatingFileHandler(
        general_log,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=30,
        encoding="utf-8",
    )
    general_handler.setLevel(level)
    general_handler.setFormatter(formatter)
    general_handler.addFilter(sensitive_filter)
    root_logger.addHandler(general_handler)

    # Handler для ошибок (ERROR и выше) — отдельный файл
    error_log = os.path.join(log_dir, "oss_bot_error.log")
    error_handler = logging.handlers.RotatingFileHandler(
        error_log,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=30,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    error_handler.addFilter(sensitive_filter)
    root_logger.addHandler(error_handler)

    logging.info("Логирование настроено: stdout + файлы в %s", log_dir)
