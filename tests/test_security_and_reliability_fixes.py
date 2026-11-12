"""Регрессионные тесты исправлений безопасности и надёжности."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from bots.telegram.docker_client import DockerClient
from core.models import Log, WebRole, WebUser
from core.rate_limit_cleanup import purge_stale_attempts
from core.vk_upload import upload_photo_for_messages
from web.dependencies import _verify_web_user


async def test_verify_web_user_rejects_expired_account(db_session_maker):
    """Истёкшая учётная запись отклоняется сразу при следующем запросе, а не только при логине."""
    async with db_session_maker() as session:
        user = WebUser(
            username="expired_temp",
            password_hash="hash",
            role=WebRole.DEPARTMENT_ADMIN,
            is_active=True,
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    request = MagicMock()
    request.session = {"user": {"web_user_id": user_id}}

    async with db_session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await _verify_web_user(session, {"web_user_id": user_id}, is_api=True, request=request)
        assert exc_info.value.status_code == 401
        assert request.session == {}


async def test_docker_exec_command_treats_403_as_failure():
    """HTTP 403 от docker-socket-proxy при start exec не маскируется под exit_code=0."""
    client = DockerClient(base_url="http://docker-socket-proxy:2375")

    create_resp = MagicMock(status_code=201)
    create_resp.json.return_value = {"Id": "exec123"}
    start_resp = MagicMock(status_code=403, content=b"<html>403 Forbidden</html>")

    mock_http = AsyncMock()
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = False
    mock_http.post.side_effect = [create_resp, start_resp]

    with patch.object(client, "_get_client", return_value=mock_http):
        code, stdout, stderr = await client.exec_command("oss_bot_db", ["echo", "hi"])

    assert code == 403
    assert stdout == b""
    assert b"403 Forbidden" in stderr


async def test_upload_photo_webp_raises_when_conversion_fails():
    """Если конвертация WebP в JPEG не удалась, сырой WebP не уходит в VK."""
    fake_webp = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
    with (
        patch("core.vk_upload._convert_webp_to_jpeg", side_effect=RuntimeError("no PIL")),
        pytest.raises(RuntimeError, match="Ошибка конвертации WebP"),
    ):
        await upload_photo_for_messages(fake_webp, filename="test.webp")


async def test_purge_stale_attempts_preserves_audit_logs_for_archival(db_session_maker):
    """Почасовая очистка не удаляет logs — их должен предварительно выгрузить архиватор."""
    old_time = datetime.now(UTC) - timedelta(days=120)
    async with db_session_maker() as session:
        session.add(Log(action="old_audit", created_at=old_time, is_mutation=False))
        await session.commit()

    await purge_stale_attempts(now=datetime.now(UTC))

    async with db_session_maker() as session:
        count = await session.scalar(text("SELECT COUNT(*) FROM logs WHERE action = 'old_audit'"))
        assert count == 1
