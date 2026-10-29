"""Асинхронная загрузка изображений для сообщений через VK API (Messages Upload).

Спецификация загрузки фотографий в сообщения сообщества:
1. Вызов photos.getMessagesUploadServer (peer_id=0).
2. Multipart POST байтов изображения на полученный upload_url с полем 'photo'.
3. Вызов photos.saveMessagesPhoto с параметрами photo, server, hash.
4. Формирование строки вложения photo{owner_id}_{id}.

Требования:
- Валидация формата (JPEG, PNG, WEBP) по magic bytes.
- Валидация размера файла (до 50 МБ).
- Строгий деловой стиль сообщений и логов (без эмодзи).
"""

from __future__ import annotations

import logging

from core.config import settings
from core.vk_client import VK_API_VERSION, get_vk_client

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE_BYTES = 50 * 1024 * 1024  # 50 МБ
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

GET_MESSAGES_UPLOAD_SERVER_URL = "https://api.vk.com/method/photos.getMessagesUploadServer"
SAVE_MESSAGES_PHOTO_URL = "https://api.vk.com/method/photos.saveMessagesPhoto"


def _convert_webp_to_jpeg(file_bytes: bytes) -> bytes:
    """Конвертировать изображение WebP в JPEG для совместимости с upload-сервером VK."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(file_bytes)) as img:
        rgb_img = img.convert("RGB")
        out_buf = io.BytesIO()
        rgb_img.save(out_buf, format="JPEG", quality=92)
        return out_buf.getvalue()


def detect_image_type(data: bytes) -> str | None:
    """Определить MIME-тип изображения по magic bytes."""
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image_data(data: bytes, filename: str | None = None) -> str:
    """Проверить размер и сигнатуру изображения. Возвращает валидный MIME-тип.

    Вызывает ValueError при нарушении ограничений.
    """
    if not data:
        raise ValueError("Файл пуст.")
    if len(data) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError("Размер файла превышает допустимый лимит 50 МБ.")

    mime_type = detect_image_type(data)
    if not mime_type or mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError("Недопустимый формат файла. Поддерживаются только JPEG, PNG и WEBP.")
    return mime_type


async def upload_photo_for_messages(
    file_bytes: bytes,
    filename: str = "image.jpg",
    peer_id: int = 0,
) -> str:
    """Загрузить фотографию для отправки в сообщения через VK API.

    Возвращает строку attachment вида photo{owner_id}_{id}.
    """
    mime_type = validate_image_data(file_bytes, filename=filename)

    if mime_type == "image/webp":
        try:
            file_bytes = _convert_webp_to_jpeg(file_bytes)
            mime_type = "image/jpeg"
            safe_name_base = (
                filename.rsplit(".", 1)[0] if filename and "." in filename else "upload"
            )
            filename = f"{safe_name_base}.jpg"
            logger.info("Изображение WebP автоматически сконвертировано в JPEG для VK.")
        except Exception as conv_err:
            logger.warning("Не удалось автоматически сконвертировать WebP в JPEG: %s", conv_err)

    if not settings.VK_BOT_TOKEN:
        raise ValueError("VK_BOT_TOKEN не задан в конфигурации системы.")

    client = get_vk_client()

    # Шаг 1: Получение URL сервера загрузки
    server_resp = await client.post(
        GET_MESSAGES_UPLOAD_SERVER_URL,
        data={
            "access_token": settings.VK_BOT_TOKEN,
            "v": VK_API_VERSION,
            "peer_id": peer_id,
        },
    )
    server_resp.raise_for_status()
    server_payload = server_resp.json()
    if "error" in server_payload:
        error_msg = server_payload["error"].get("error_msg", "Неизвестная ошибка VK API")
        logger.error("VK API error при photos.getMessagesUploadServer: %s", error_msg)
        raise RuntimeError(f"Ошибка получения сервера загрузки VK: {error_msg}")

    upload_url = server_payload.get("response", {}).get("upload_url")
    if not upload_url:
        raise RuntimeError("VK API не вернул upload_url для загрузки фотографии.")

    # Шаг 2: Загрузка файла на сервер VK
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    safe_filename = (
        filename if filename and "." in filename else f"upload{ext_map.get(mime_type, '.jpg')}"
    )

    upload_resp = await client.post(
        upload_url,
        files={"photo": (safe_filename, file_bytes, mime_type)},
    )
    upload_resp.raise_for_status()
    upload_data = upload_resp.json()

    photo_param = upload_data.get("photo")
    server_param = upload_data.get("server")
    hash_param = upload_data.get("hash")
    if not photo_param or photo_param == "[]" or server_param is None or hash_param is None:
        logger.error(
            "VK upload server вернул некорректный ответ: status=%s, payload=%s",
            upload_resp.status_code,
            upload_data,
        )
        raise RuntimeError("Сервер загрузки VK вернул некорректный ответ.")

    # Шаг 3: Сохранение фотографии
    save_resp = await client.post(
        SAVE_MESSAGES_PHOTO_URL,
        data={
            "access_token": settings.VK_BOT_TOKEN,
            "v": VK_API_VERSION,
            "photo": photo_param,
            "server": server_param,
            "hash": hash_param,
        },
    )
    save_resp.raise_for_status()
    save_payload = save_resp.json()
    if "error" in save_payload:
        error_msg = save_payload["error"].get("error_msg", "Неизвестная ошибка VK API")
        logger.error("VK API error при photos.saveMessagesPhoto: %s", error_msg)
        raise RuntimeError(f"Ошибка сохранения фотографии VK: {error_msg}")

    items = save_payload.get("response")
    if not items or not isinstance(items, list):
        raise RuntimeError("VK API вернул пустой список сохраненных фотографий.")

    photo_info = items[0]
    owner_id = photo_info.get("owner_id")
    photo_id = photo_info.get("id")
    if owner_id is None or photo_id is None:
        raise RuntimeError("VK API вернул некорректные идентификаторы фотографии.")

    attachment = f"photo{owner_id}_{photo_id}"
    logger.info("Фотография успешно загружена в VK: %s", attachment)
    return attachment
