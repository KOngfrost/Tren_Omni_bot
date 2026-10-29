import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from core.services.broadcast_service import (
    compose_broadcast_message_text,
    format_post_url,
)
from core.vk_client import send_vk_message
from core.vk_upload import _convert_webp_to_jpeg, upload_photo_for_messages


class MockHttpxResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_convert_webp_to_jpeg():
    """Тест автоматической конвертации WebP в валидный JPEG через Pillow."""
    img = Image.new("RGB", (100, 100), color="blue")
    webp_io = io.BytesIO()
    img.save(webp_io, format="WEBP")
    webp_bytes = webp_io.getvalue()

    jpeg_bytes = _convert_webp_to_jpeg(webp_bytes)
    assert jpeg_bytes.startswith(b"\xff\xd8\xff")  # Magic bytes JPEG

    result_img = Image.open(io.BytesIO(jpeg_bytes))
    assert result_img.format == "JPEG"
    assert result_img.size == (100, 100)


@pytest.mark.asyncio
async def test_upload_photo_webp_autoconversion():
    """Тест того, что upload_photo_for_messages автоматически конвертирует WebP перед POST в VK."""
    img = Image.new("RGB", (50, 50), color="red")
    webp_io = io.BytesIO()
    img.save(webp_io, format="WEBP")
    webp_bytes = webp_io.getvalue()

    fake_client = AsyncMock()
    server_resp = MockHttpxResponse({"response": {"upload_url": "https://upload.vk.com/test"}})
    upload_resp = MockHttpxResponse({"photo": '[{"id": 123}]', "server": 1, "hash": "abc"})
    save_resp = MockHttpxResponse({"response": [{"owner_id": 999, "id": 888}]})

    fake_client.post.side_effect = [server_resp, upload_resp, save_resp]

    with (
        patch("core.vk_upload.get_vk_client", return_value=fake_client),
        patch("core.vk_upload.settings") as mock_settings,
    ):
        mock_settings.VK_BOT_TOKEN = "valid_token"
        attachment = await upload_photo_for_messages(webp_bytes, filename="banner.webp")
        assert attachment == "photo999_888"

        upload_call = fake_client.post.call_args_list[1]
        files_sent = upload_call.kwargs["files"]["photo"]
        assert files_sent[0].endswith(".jpg")
        assert files_sent[2] == "image/jpeg"
        assert files_sent[1].startswith(b"\xff\xd8\xff")


def test_post_url_formatting():
    """Тест формирования URL поста и склеивания с текстом."""
    assert format_post_url("wall-207038317_1234") == "https://vk.com/wall-207038317_1234"
    assert (
        format_post_url("https://vk.com/wall-207038317_1234")
        == "https://vk.com/wall-207038317_1234"
    )
    assert format_post_url(None) is None

    res = compose_broadcast_message_text("Важное объявление", "wall-207038317_1234")
    assert res == "Важное объявление\n\nhttps://vk.com/wall-207038317_1234"

    res_no_text = compose_broadcast_message_text("", "wall-207038317_1234")
    assert res_no_text == "https://vk.com/wall-207038317_1234"


@pytest.mark.asyncio
async def test_send_vk_message_with_attachment_and_text():
    """Тест того, что send_vk_message корректно формирует payload."""
    fake_client = AsyncMock()
    resp = MockHttpxResponse({"response": 12345})
    fake_client.post.return_value = resp

    with (
        patch("core.vk_client.get_vk_client", return_value=fake_client),
        patch("core.vk_client.settings") as mock_settings,
    ):
        mock_settings.VK_BOT_TOKEN = "valid_token"
        ok = await send_vk_message(
            vk_id=123,
            text="Привет студентам!",
            attachment="photo123_456",
        )
        assert ok is True
        call_data = fake_client.post.call_args.kwargs["data"]
        assert call_data["attachment"] == "photo123_456"
        assert call_data["message"] == "Привет студентам!"
