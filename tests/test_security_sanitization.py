"""Тесты санитизации чувствительных данных (PII, токены, пароли) в логах и Sentry."""

import logging
from core.logging_config import (
    JsonFormatter,
    SanitizedFormatter,
    SensitiveDataFilter,
    mask_sensitive_data,
)
from core.sentry import _scrub_sentry_event


def test_mask_sensitive_data_tokens_and_passwords():
    raw = (
        "User logged in with password=SuperSecretPassword123! "
        "and direct token vk1.a.abcdefghijklmnopqrstuvwxyz0123456789. "
        "Session: session=secret_session_data_1234567890."
    )
    masked = mask_sensitive_data(raw)

    assert "SuperSecretPassword123!" not in masked
    assert "vk1.a.abcdefghijklmnopqrstuvwxyz0123456789" not in masked
    assert "secret_session_data_1234567890" not in masked
    assert "password=***" in masked
    assert "vk1.a.***" in masked
    assert "session=***" in masked


def test_sanitized_formatter_masks_formatted_record():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="API error for user with password=%s and token=%s",
        args=("MySecretPassword999", "vk1.a.abcdefgh123456"),
        exc_info=None,
    )
    formatter = SanitizedFormatter(fmt="%(message)s")
    formatted = formatter.format(record)

    assert "MySecretPassword999" not in formatted
    assert "password=***" in formatted
    assert "token=***" in formatted or "vk1.a.***" in formatted


def test_json_formatter_masks_json_payload():
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="Failed auth: secret=top_secret_value",
        args=(),
        exc_info=None,
    )
    formatter = JsonFormatter()
    output = formatter.format(record)

    assert "top_secret_value" not in output
    assert "secret=***" in output


def test_sentry_scrub_pii_headers_and_body():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer sensitive_token_xyz",
                "Cookie": "session=sensitive_cookie_val; other=123",
                "X-Csrf-Token": "csrf_token_secret",
                "User-Agent": "Mozilla/5.0",
            },
            "data": {
                "username": "admin",
                "password": "SuperSecretPassword123",
                "token": "api_secret_key",
                "code": "123456",
            },
        },
        "user": {
            "id": 1,
            "ip_address": "127.0.0.1",
            "email": "admin@example.com",
        },
    }

    scrubbed = _scrub_sentry_event(event, {})
    assert scrubbed is not None
    req = scrubbed["request"]
    headers = req["headers"]
    data = req["data"]

    assert headers["Authorization"] == "[FILTERED]"
    assert headers["Cookie"] == "[FILTERED]"
    assert headers["X-Csrf-Token"] == "[FILTERED]"
    assert headers["User-Agent"] == "Mozilla/5.0"

    assert data["password"] == "[FILTERED]"
    assert data["token"] == "[FILTERED]"
    assert data["code"] == "[FILTERED]"
    assert data["username"] == "admin"

    assert "ip_address" not in scrubbed["user"]
    assert "email" not in scrubbed["user"]
