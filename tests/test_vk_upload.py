"""Тесты модуля загрузки изображений в сообщения ВКонтакте (core.vk_upload)."""

import pytest

from core.config import settings
from core.vk_upload import (
    MAX_IMAGE_SIZE_BYTES,
    detect_image_type,
    upload_photo_for_messages,
    validate_image_data,
)

SAMPLE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\x00" * 50
SAMPLE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01" + b"\x00" * 50
SAMPLE_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00" + b"\x00" * 50


def test_detect_image_type():
    """Проверка корректного распознавания MIME-типов по magic bytes."""
    assert detect_image_type(SAMPLE_JPEG) == "image/jpeg"
    assert detect_image_type(SAMPLE_PNG) == "image/png"
    assert detect_image_type(SAMPLE_WEBP) == "image/webp"
    assert detect_image_type(b"NOT_AN_IMAGE_DATA") is None
    assert detect_image_type(b"SHORT") is None


def test_validate_image_data():
    """Проверка валидации размера и формата изображения."""
    assert validate_image_data(SAMPLE_JPEG) == "image/jpeg"
    assert validate_image_data(SAMPLE_PNG) == "image/png"
    assert validate_image_data(SAMPLE_WEBP) == "image/webp"

    # Пустой файл
    with pytest.raises(ValueError, match="Файл пуст"):
        validate_image_data(b"")

    # Превышение лимита 50 МБ
    huge_data = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    with pytest.raises(ValueError, match="50 МБ"):
        # Имитируем большой размер без аллокации 50 МБ в ОЗУ
        class DummyBytes(bytes):
            def __len__(self):
                return MAX_IMAGE_SIZE_BYTES + 1024

        validate_image_data(DummyBytes(huge_data))

    # Неподдерживаемый формат
    with pytest.raises(ValueError, match="Недопустимый формат"):
        validate_image_data(b"GIF89a\x01\x00\x01\x00\x80\x00\x00")


@pytest.mark.asyncio
async def test_upload_photo_for_messages_success(monkeypatch):
    """Успешная трёхфазная загрузка фотографии на серверы VK."""
    monkeypatch.setattr(settings, "VK_BOT_TOKEN", "valid_vk_token")

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP error {self.status_code}")

        def json(self):
            return self._json_data

    call_history = []

    class MockHttpClient:
        async def post(self, url, **kwargs):
            call_history.append((url, kwargs))
            if "getMessagesUploadServer" in url:
                return MockResponse(
                    {"response": {"upload_url": "https://upload.vk.com/messages_upload_test"}}
                )
            if "messages_upload_test" in url:
                assert "files" in kwargs
                assert "photo" in kwargs["files"]
                return MockResponse(
                    {
                        "server": 654321,
                        "photo": '[{"photo":"uploaded_payload"}]',
                        "hash": "test_hash_123",
                    }
                )
            if "saveMessagesPhoto" in url:
                assert kwargs["data"]["server"] == 654321
                assert kwargs["data"]["hash"] == "test_hash_123"
                return MockResponse({"response": [{"owner_id": -207038317, "id": 456239123}]})
            raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr("core.vk_upload.get_vk_client", lambda: MockHttpClient())

    attachment = await upload_photo_for_messages(SAMPLE_JPEG, filename="banner.jpg")
    assert attachment == "photo-207038317_456239123"
    assert len(call_history) == 3


@pytest.mark.asyncio
async def test_upload_photo_for_messages_no_token(monkeypatch):
    """Попытка загрузки без настроенного токена сообщества."""
    monkeypatch.setattr(settings, "VK_BOT_TOKEN", "")
    with pytest.raises(ValueError, match="VK_BOT_TOKEN не задан"):
        await upload_photo_for_messages(SAMPLE_PNG)


@pytest.mark.asyncio
async def test_upload_photo_for_messages_server_error(monkeypatch):
    """Ошибка VK API при получении upload_url."""
    monkeypatch.setattr(settings, "VK_BOT_TOKEN", "valid_vk_token")

    class MockHttpClient:
        async def post(self, url, **kwargs):
            return type(
                "Resp",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"error": {"error_msg": "Access denied: bad token"}},
                },
            )()

    monkeypatch.setattr("core.vk_upload.get_vk_client", lambda: MockHttpClient())

    with pytest.raises(RuntimeError, match="bad token"):
        await upload_photo_for_messages(SAMPLE_PNG)
