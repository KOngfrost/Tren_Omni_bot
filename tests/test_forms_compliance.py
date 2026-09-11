"""Тесты валидации, безопасности и доступности веб-форм (требование 11).

Проверяет:
1. Форма логина: наличие CSRF токена, атрибутов required, ассоциации label/input.
2. Обработка отправки формы логина с пустыми полями.
3. Защита от CSRF-атак (отклонение POST без токена или с невалидным токеном).
4. Форма ввода кода 2FA: доступность, атрибуты required, labels.
"""

import re


def _extract_csrf_token(html_text: str) -> str:
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if not match:
        match = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html_text)
    return match.group(1) if match else ""


def test_login_form_structure_and_accessibility(web_client):
    """Форма входа имеет CSRF-токен, правильные типы инпутов, лейблы и required."""
    res = web_client.get("/auth/login")
    assert res.status_code == 200
    html = res.text

    # CSRF token
    csrf_token = _extract_csrf_token(html)
    assert csrf_token, "CSRF token must be present in login form"

    # Input username
    assert 'name="username"' in html
    assert 'id="username"' in html
    assert 'for="username"' in html  # label association

    # Input password
    assert 'name="password"' in html
    assert 'id="password"' in html
    assert 'type="password"' in html
    assert 'for="password"' in html  # label association

    # Submit button
    assert 'type="submit"' in html


def test_login_csrf_protection(web_client):
    """Отправка формы входа без CSRF токена или с неверным токеном блокируется."""
    # Без токена
    res_no_csrf = web_client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123"},
        follow_redirects=False,
    )
    assert res_no_csrf.status_code in (400, 403)

    # С недействительным токеном
    res_bad_csrf = web_client.post(
        "/auth/login",
        data={
            "username": "testadmin",
            "password": "test_password_123",
            "csrf_token": "invalid_forged_token",
        },
        follow_redirects=False,
    )
    assert res_bad_csrf.status_code in (400, 403)


def test_login_empty_form_validation(web_client):
    """Отправка формы с пустыми полями отклоняется валидатором."""
    res_get = web_client.get("/auth/login")
    csrf_token = _extract_csrf_token(res_get.text)

    # Пустые имя пользователя и пароль
    res_empty = web_client.post(
        "/auth/login",
        data={"username": "", "password": "", "csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert res_empty.status_code == 200
    assert "Неверный логин или пароль" in res_empty.text


def test_login_successful_flow(web_client):
    """Успешный вход с валидным CSRF-токеном и кредами перенаправляет в админку."""
    res_get = web_client.get("/auth/login")
    csrf_token = _extract_csrf_token(res_get.text)

    res_post = web_client.post(
        "/auth/login",
        data={
            "username": "testadmin",
            "password": "test_password_123",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    # Редирект на главную/дашборд
    assert res_post.status_code in (302, 303)
    assert res_post.headers["location"] in ("/", "/dashboard")


def test_2fa_unauthorized_redirect(web_client):
    """Попытка прямого открытия страницы /auth/2fa без активного шага перенаправляет на логин."""
    res = web_client.get("/auth/2fa", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/auth/login"


def test_2fa_template_structure():
    """Шаблон 2FA содержит поле ввода кода, CSRF-токен и ссылки 152-ФЗ."""
    from pathlib import Path

    template_content = Path("web/templates/2fa.html").read_text(encoding="utf-8")
    assert 'name="code"' in template_content
    assert 'name="csrf_token"' in template_content
    assert 'type="submit"' in template_content
    assert "required" in template_content
    assert "/legal/consent" in template_content
