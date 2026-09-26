"""Скрипт аудита истории Git на наличие случайно закоммиченных секретов и токенов.

Используется локально и в CI/CD (GitHub Actions).
"""

import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATTERNS = [
    (re.compile(r'(?i)password\s*[:=]\s*[\'"]([^\'"]{6,})[\'"]'), "Password assignment"),
    (re.compile(r'(?i)secret(_key)?\s*[:=]\s*[\'"]([^\'"]{8,})[\'"]'), "Secret assignment"),
    (re.compile(r"vk1\.a\.[a-zA-Z0-9_\-]{20,}"), "VK token"),
    (re.compile(r"[0-9]{8,10}:[a-zA-Z0-9_\-]{35}"), "Telegram token"),
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY-----"), "Private Key"),
]

IGNORED_SUBSTRINGS = {
    "change_me",
    "your_",
    "test_",
    "dummy",
    "ci_super_secret",
    "placeholder",
    "fake",
    "example",
    "default",
    "none",
    "null",
    "password123",
    "secret_key",
    "test_password",
    "some_secret",
    "mock",
    "hash",
    "sample",
    "correcthorsebatterystaple123!",
    "browserpass123",
    "admin12345",
    "strong-bootstrap-pass",
    "token_urlsafe",
    "${",
    "$${",
}


def scan_git_history() -> int:
    """Сканирует всю историю git на утечки секретов."""
    try:
        log = subprocess.check_output(
            ["git", "log", "-p", "--all"], encoding="utf-8", errors="replace"
        )
    except Exception as e:
        print(f"Ошибка чтения git log: {e}")
        return 0

    findings = []
    for pattern, desc in PATTERNS:
        for m in pattern.finditer(log):
            text = m.group(0)
            lower = text.lower()
            if any(ign in lower for ign in IGNORED_SUBSTRINGS):
                continue
            findings.append((desc, text[:80]))

    if not findings:
        print("Аудит секретов завершён успешно: реальных секретов или токенов в истории Git не обнаружено.")
        return 0

    print(f"ВНИМАНИЕ: Найдено {len(findings)} потенциальных секретов в истории Git:")
    for desc, text in set(findings):
        print(f"  [{desc}] {text}")
    return 1


if __name__ == "__main__":
    sys.exit(scan_git_history())
