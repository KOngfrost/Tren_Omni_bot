"""Тесты аутентификации: пароли, rate limiting, роли."""

from unittest.mock import MagicMock

from web.dependencies import can_write, is_superadmin, require_superadmin, require_writer, role_of
from web.routes.auth import (
    _clear_attempts,
    _is_rate_limited,
    _record_failed_attempt,
)
from web.security.passwords import HAS_ARGON2, hash_password, verify_password


class TestPasswords:
    def test_hash_and_verify_correct(self):
        stored = hash_password("S3cretPassword!")
        assert verify_password("S3cretPassword!", stored) is True

    def test_verify_wrong_password(self):
        stored = hash_password("correct")
        assert verify_password("wrong", stored) is False

    def test_hash_contains_algorithm_and_iterations(self):
        stored = hash_password("test")
        if HAS_ARGON2:
            assert stored.startswith("$argon2id$")
        else:
            parts = stored.split("$")
            assert parts[0] == "pbkdf2_sha256"
            assert int(parts[1]) >= 100_000

    def test_different_hashes_for_same_password(self):
        assert hash_password("same") != hash_password("same")

    def test_verify_invalid_stored_format(self):
        assert verify_password("test", "not-a-valid-hash") is False

    def test_verify_empty_stored(self):
        assert verify_password("test", "") is False


class TestRateLimiting:
    async def test_not_limited_initially(self, db_session_maker):
        async with db_session_maker() as session:
            assert await _is_rate_limited(session, "1.2.3.4") is False

    async def test_limited_after_five_failures(self, db_session_maker):
        for _ in range(5):
            await _record_failed_attempt("1.2.3.4")
        async with db_session_maker() as session:
            assert await _is_rate_limited(session, "1.2.3.4") is True

    async def test_clear_attempts_resets_limit(self, db_session_maker):
        for _ in range(5):
            await _record_failed_attempt("1.2.3.4")
        async with db_session_maker() as session:
            assert await _is_rate_limited(session, "1.2.3.4") is True
            await _clear_attempts("1.2.3.4")
            assert await _is_rate_limited(session, "1.2.3.4") is False

    async def test_window_is_15_minutes(self):
        from web.routes.auth import _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS

        assert _LOGIN_MAX_ATTEMPTS == 5
        assert _LOGIN_WINDOW_SECONDS == 15 * 60


class TestRoles:
    def test_role_normalization_superadmin(self):
        assert role_of({"role": "SUPERADMIN"}) == is_superadmin_role()

    def test_role_backward_compat_lowercase(self):
        # .upper() делает "superadmin" → "SUPERADMIN" валидным WebRole.
        # Обратная совместимость сохраняется неявно.
        assert is_superadmin({"role": "superadmin"}) is True

    def test_department_admin_cannot_see_all(self):
        user = {"role": "DEPARTMENT_ADMIN", "department_id": 1}
        assert is_superadmin(user) is False
        assert can_write(user) is True

    def test_viewer_read_only(self):
        user = {"role": "VIEWER"}
        assert can_write(user) is False
        assert is_superadmin(user) is False

    def test_no_role(self):
        assert role_of({}) is None
        assert can_write({}) is False


def is_superadmin_role():
    from core.models import WebRole

    return WebRole.SUPERADMIN


class TestSessionHelpers:
    def test_get_current_user_from_session(self):
        from web.dependencies import get_current_user

        request = MagicMock()
        request.session = {"user": {"username": "admin"}}
        assert get_current_user(request)["username"] == "admin"

    def test_get_current_user_missing(self):
        from web.dependencies import get_current_user

        request = MagicMock()
        request.session = {}
        assert get_current_user(request) is None

    async def test_legacy_scope_does_not_trust_session_department(self):
        from web.dependencies import get_admin_scope

        is_super, department_id = await get_admin_scope(
            None, {"role": "DEPARTMENT_ADMIN", "department_id": 999}
        )
        assert is_super is False
        assert department_id is None

    async def test_writer_dependency_awaits_authentication(self):
        request = MagicMock()
        request.session = {"user": {"username": "admin", "role": "SUPERADMIN", "bootstrap": True}}

        user = await require_writer(request)

        assert user["username"] == "admin"

    async def test_superadmin_dependency_awaits_authentication(self):
        request = MagicMock()
        request.session = {"user": {"username": "admin", "role": "SUPERADMIN", "bootstrap": True}}

        user = await require_superadmin(request)

        assert user["role"] == "SUPERADMIN"
