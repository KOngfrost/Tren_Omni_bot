"""Тесты для проверки исправлений аудита безопасности и функциональности (SEC-01..07, DB-01, OPS-01..03)."""

from starlette.requests import Request

from core.audit import get_client_ip
from web.routes.admin_panel import _validate_creator_permissions


def _make_dummy_request(client_host: str, headers: dict[str, str]) -> Request:
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    scope = {
        "type": "http",
        "client": (client_host, 12345),
        "headers": raw_headers,
    }
    return Request(scope)


def test_right_to_left_xff_parsing_prevents_spoofing():
    """SEC-07: Проверка, что поддельный левый IP в X-Forwarded-For не принимается."""
    # Запрос пришёл с доверенного loopback-прокси (127.0.0.1)
    # Злоумышленник передал: X-Forwarded-For: 1.1.1.1 (spoofed), 203.0.113.195 (настоящий IP клиента)
    req = _make_dummy_request(
        "127.0.0.1",
        {"X-Forwarded-For": "1.1.1.1, 203.0.113.195"},
    )
    detected_ip = get_client_ip(req)
    # Должен быть определён правый недоверенный IP (203.0.113.195), а не spoofed 1.1.1.1
    assert detected_ip == "203.0.113.195"


def test_privilege_escalation_prevented_for_dept_admin():
    """SEC-03: Администратор отдела не может наделять правами, которых у него нет."""
    dept_admin_user = {
        "username": "dept_head",
        "role": "department_admin",
        "can_access_partnerships": False,
        "can_access_broadcast": False,
        "can_access_logs": False,
    }

    # Попытка выдать can_access_logs
    err = _validate_creator_permissions(
        dept_admin_user,
        can_partnerships=False,
        can_broadcast=False,
        can_logs=True,
    )
    assert err == "У вас нет прав для предоставления доступа к журналу аудита"

    # Попытка выдать can_access_broadcast
    err_broadcast = _validate_creator_permissions(
        dept_admin_user,
        can_partnerships=False,
        can_broadcast=True,
        can_logs=False,
    )
    assert err_broadcast == "У вас нет прав для предоставления доступа к массовой рассылке"

    # Без повышенных прав — разрешено
    assert (
        _validate_creator_permissions(
            dept_admin_user,
            can_partnerships=False,
            can_broadcast=False,
            can_logs=False,
        )
        is None
    )


def test_superadmin_can_grant_any_permissions():
    """SEC-03: Суперадминистратор может выдавать любые права."""
    superadmin_user = {
        "username": "superadmin",
        "role": "SUPERADMIN",
    }
    assert (
        _validate_creator_permissions(
            superadmin_user,
            can_partnerships=True,
            can_broadcast=True,
            can_logs=True,
        )
        is None
    )
