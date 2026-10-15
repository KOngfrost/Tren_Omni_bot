"""Интеграционные тесты веб-маршрутов рассылки (/broadcast/)."""

import re

import pytest
from fastapi.testclient import TestClient

from core.models import Admin, User, WebRole, WebUser
from web.security.passwords import hash_password


def _extract_csrf(html_text: str) -> str:
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if not m:
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html_text)
    return m.group(1) if m else "mock_csrf"


@pytest.fixture
async def setup_users_and_admins(db_session_maker):
    """Создаёт тестовых пользователей, суперадмина и админа отдела."""
    async with db_session_maker() as session:
        # Студенты
        u1 = User(vk_id=501, full_name="Студент 1", dormitory="№16")
        u2 = User(vk_id=502, full_name="Студент 2", dormitory="№15")
        u3 = User(vk_id=503, full_name="Студент 3", dormitory="№16")
        session.add_all([u1, u2, u3])
        await session.flush()

        # Суперадмин
        sa_admin = Admin(user_id=u1.id, role="SUPERADMIN")
        session.add(sa_admin)
        await session.flush()
        sa_web = WebUser(
            username="super_broadcaster",
            password_hash=hash_password("SuperSecretPass123"),
            role=WebRole.SUPERADMIN,
            is_active=True,
            admin_id=sa_admin.id,
        )
        session.add(sa_web)

        # Обычный админ отдела
        dept_admin = Admin(user_id=u2.id, role="ADMIN")
        session.add(dept_admin)
        await session.flush()
        dept_web = WebUser(
            username="dept_admin_user",
            password_hash=hash_password("DeptSecretPass123"),
            role=WebRole.DEPARTMENT_ADMIN,
            is_active=True,
            admin_id=dept_admin.id,
        )
        session.add(dept_web)

        await session.commit()


def test_broadcast_unauthorized_redirect(web_client: TestClient):
    """Неавторизованный пользователь перенаправляется на логин."""
    resp = web_client.get("/broadcast/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["location"]


def test_broadcast_department_admin_denied(web_client: TestClient, setup_users_and_admins):
    """Админ отдела не имеет доступа к рассылке (только суперадмин)."""
    # Логин админа отдела
    login_page = web_client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    res = web_client.post(
        "/auth/login",
        data={"username": "dept_admin_user", "password": "DeptSecretPass123", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res.status_code == 200

    # Попытка открыть /broadcast/
    bc_resp = web_client.get("/broadcast/", follow_redirects=False)
    assert bc_resp.status_code == 303
    assert bc_resp.headers["location"] == "/"


def _login_superadmin(web_client: TestClient) -> str:
    """Авторизует суперадмина и возвращает CSRF-токен формы рассылки."""
    login_page = web_client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    web_client.post(
        "/auth/login",
        data={
            "username": "super_broadcaster",
            "password": "SuperSecretPass123",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    bc_page = web_client.get("/broadcast/")
    return _extract_csrf(bc_page.text)


def test_broadcast_superadmin_flow(web_client: TestClient, setup_users_and_admins, monkeypatch):
    """Суперадмин успешно открывает страницу, смотрит предпросмотр и запускает рассылку."""
    fired = []
    monkeypatch.setattr(
        "core.services.broadcast_service.fire_outbox_delivery", lambda: fired.append(True)
    )

    csrf = _login_superadmin(web_client)

    # 1. AJAX предпросмотр
    prev_resp = web_client.get("/broadcast/preview?count=2")
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert prev_data["count"] == 2
    assert len(prev_data["users"]) == 2

    # 2. Валидация: пустой контент (нет текста и нет вложений)
    err_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "   ", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert err_resp.status_code == 200
    assert "Укажите текст сообщения или прикрепите пост/изображение." in err_resp.text

    # 3. Успешная отправка
    send_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "Важное объявление!", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert send_resp.status_code == 200
    assert "Рассылка успешно сформирована" in send_resp.text
    assert len(fired) >= 1


def test_broadcast_send_with_excluded_ids(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка отправки рассылки с исключением отдельных пользователей."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    prev_resp = web_client.get("/broadcast/preview?count=3")
    users = prev_resp.json()["users"]
    assert len(users) == 3

    excluded_id = users[0]["id"]

    # Отправка 3 пользователям с исключением 1
    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "3",
            "text": "Сообщение без одного студента",
            "excluded_ids": str(excluded_id),
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert (
        "Рассылка успешно сформирована! Поставлено в очередь отправки: 2 пользователей."
        in resp.text
    )

    # Исключение всех пользователей
    all_excluded = f"{users[0]['id']},{users[1]['id']},{users[2]['id']}"
    resp_all = web_client.post(
        "/broadcast/send",
        data={
            "count": "3",
            "text": "Сообщение никому",
            "excluded_ids": all_excluded,
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert resp_all.status_code == 200
    assert "Все выбранные пользователи были исключены из списка получателей." in resp_all.text


def test_broadcast_send_with_post_url(web_client: TestClient, setup_users_and_admins, monkeypatch):
    """Проверка отправки рассылки с ссылкой на пост ВКонтакте (без текста)."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "2",
            "text": "",
            "post_url": "https://vk.com/wall-207038317_1234",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert (
        "Рассылка успешно сформирована! Поставлено в очередь отправки: 2 пользователей."
        in resp.text
    )


def test_broadcast_send_invalid_post_url(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка ошибки при передаче некорректной ссылки на пост ВКонтакте."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "2",
            "text": "Тест",
            "post_url": "https://vk.com/not_a_wall_post",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert (
        "Некорректная ссылка на пост ВКонтакте. Поддерживаются ссылки формата vk.com/wall-XXX_YYY."
        in resp.text
    )


def test_broadcast_send_with_image_upload(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка отправки рассылки с загрузкой изображения через мок VK upload."""
    from unittest.mock import AsyncMock

    mock_upload = AsyncMock(return_value="photo-207038317_9999")
    monkeypatch.setattr("web.routes.broadcast.upload_photo_for_messages", mock_upload)
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    fake_image_bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb"
    )
    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "2",
            "text": "Фотокарточка",
            "csrf_token": csrf,
        },
        files={
            "image": ("announcement.jpg", fake_image_bytes, "image/jpeg"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert (
        "Рассылка успешно сформирована! Поставлено в очередь отправки: 2 пользователей."
        in resp.text
    )
    mock_upload.assert_awaited_once()


def test_broadcast_send_with_post_and_image(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка комбинированной отправки: ссылка на пост + файл изображения."""
    from unittest.mock import AsyncMock

    mock_upload = AsyncMock(return_value="photo-207038317_8888")
    monkeypatch.setattr("web.routes.broadcast.upload_photo_for_messages", mock_upload)
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    fake_image_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "1",
            "text": "Пост и картинка вместе",
            "post_url": "https://vk.com/wall-207038317_555",
            "csrf_token": csrf,
        },
        files={
            "image": ("banner.png", fake_image_bytes, "image/png"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert (
        "Рассылка успешно сформирована! Поставлено в очередь отправки: 1 пользователей."
        in resp.text
    )


def test_broadcast_send_image_upload_failure(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка обработки ошибки загрузки изображения в VK API."""
    from unittest.mock import AsyncMock

    mock_upload = AsyncMock(side_effect=ValueError("Недопустимый формат файла."))
    monkeypatch.setattr("web.routes.broadcast.upload_photo_for_messages", mock_upload)
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    resp = web_client.post(
        "/broadcast/send",
        data={
            "count": "2",
            "text": "Ошибка файла",
            "csrf_token": csrf,
        },
        files={
            "image": ("corrupted.bin", b"xyz", "application/octet-stream"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Ошибка загрузки изображения: Недопустимый формат файла." in resp.text


def test_broadcast_no_emojis_in_messages_and_ui(
    web_client: TestClient, setup_users_and_admins, monkeypatch
):
    """Проверка строгого делового стиля: отсутствие эмодзи в интерфейсе и флеш-сообщениях."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)
    csrf = _login_superadmin(web_client)

    emoji_pattern = re.compile(r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]")

    # 1. UI страницы рассылки
    bc_page = web_client.get("/broadcast/")
    # Проверяем заголовки, кнопки и лейблы (исключая технические данные)
    clean_text = re.sub(r"<svg[\s\S]*?</svg>", "", bc_page.text)
    assert not emoji_pattern.search(clean_text)

    # 2. Ошибка валидации
    err_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "", "csrf_token": csrf},
        follow_redirects=True,
    )
    clean_err = re.sub(r"<svg[\s\S]*?</svg>", "", err_resp.text)
    assert not emoji_pattern.search(clean_err)
    assert "Укажите текст сообщения или прикрепите пост/изображение." in clean_err

    # 3. Успешное сообщение
    succ_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "Текст без эмодзи", "csrf_token": csrf},
        follow_redirects=True,
    )
    clean_succ = re.sub(r"<svg[\s\S]*?</svg>", "", succ_resp.text)
    assert not emoji_pattern.search(clean_succ)
    assert "Рассылка успешно сформирована" in clean_succ
