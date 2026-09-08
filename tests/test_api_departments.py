"""Тесты JSON API отделов (web/routes/api.py).

Проверяют контракт ``{success: bool, data?: any, error?: str}`` и защита
от класса багов, ради которого заведена задача: ручной разбор
``request.json()`` падал с AttributeError/TypeError (ответ 500) на
невалидном теле — массив вместо объекта, null в поле name, битый JSON.
После перехода на Pydantic-схемы (web/schemas.py) такие тела обязаны
давать 400 с JSON-ошибкой.

Покрытие:
- авторизация (401 без сессии), CSRF (403 без токена), rate limit (429);
- CRUD: список с usage, получение по id, создание, переименование, удаление;
- IDOR-скоупинг: админ отдела видит только свой отдел.
"""

import re

import pytest


def _login(web_client, username="testadmin", password="test_password_123"):
    """Bootstrap-логин в панель (или вход заведённым веб-пользователем)."""
    login_page = web_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    resp = web_client.post(
        "/auth/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"Login failed: {resp.status_code}"
    return web_client


def _logout(web_client):
    """Выйти из панели (POST /auth/logout с CSRF-заголовком)."""
    page = web_client.get("/departments/")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    resp = web_client.post("/auth/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert resp.status_code in (302, 303), f"Logout failed: {resp.status_code}"


def _api_csrf(web_client):
    """CSRF-токен сессии со страницы отделов (доступной суперадмину)."""
    page = web_client.get("/departments/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, "CSRF token not found on /departments/"
    return match.group(1)


def _api_post(web_client, path, json=None, csrf=None, raw=None):
    """POST к JSON API с CSRF-заголовком (так же делает store.js)."""
    if csrf is None:
        csrf = _api_csrf(web_client)
    headers = {"X-CSRF-Token": csrf, "Accept": "application/json"}
    kwargs = {"headers": headers, "follow_redirects": False}
    if raw is not None:
        kwargs["content"] = raw.encode("utf-8")
        kwargs["headers"] = {**headers, "Content-Type": "application/json"}
    elif json is not None:
        kwargs["json"] = json
    return web_client.post(path, **kwargs)


def _api_get(web_client, path):
    return web_client.get(path, headers={"Accept": "application/json"})


def _db_execute(web_client, sql, params=()):
    """Прямой INSERT/UPDATE в shared in-memory БД теста (stdlib sqlite3)."""
    import sqlite3

    conn = sqlite3.connect(f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _create_department_api(web_client, name):
    """Создать отдел через API и вернуть его id."""
    resp = _api_post(web_client, "/api/departments/create", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _create_department_admin(web_client, username, password, department_id):
    """Завести веб-пользователя-админа отдела напрямую в БД."""
    from web.security.passwords import hash_password

    _db_execute(
        web_client,
        "INSERT INTO web_users (username, password_hash, role, department_id, is_active) "
        "VALUES (?, ?, 'DEPARTMENT_ADMIN', ?, 1)",
        (username, hash_password(password), department_id),
    )


# ==========================================
# Авторизация, CSRF, rate limit
# ==========================================


class TestApiAccessControl:
    def test_list_requires_auth_401(self, web_client):
        """Без сессии список отделов недоступен (JSON 401, не редирект)."""
        resp = _api_get(web_client, "/api/departments/")
        assert resp.status_code == 401
        assert resp.json()["detail"]

    def test_create_without_csrf_token_403(self, web_client):
        """POST без X-CSRF-Token отклоняется middleware (403)."""
        _login(web_client)
        resp = web_client.post(
            "/api/departments/create",
            json={"name": "Отдел"},
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_create_rate_limited_429(self, web_client, monkeypatch):
        """21-я CRUD-операция подряд попадает в rate limit (20 за 5 минут)."""
        _login(web_client)
        csrf = _api_csrf(web_client)

        # Мокаем DBRateLimiter: после 20 вызовов возвращает False
        call_count = [0]

        async def mock_is_allowed(session, ip, action):
            call_count[0] += 1
            return call_count[0] <= 20

        monkeypatch.setattr(
            "web.routes.auth._crud_rate_limiter.is_allowed",
            mock_is_allowed,
        )

        responses = [
            _api_post(
                web_client,
                "/api/departments/create",
                json={"name": f"Отдел {i}"},
                csrf=csrf,
            )
            for i in range(21)
        ]
        assert responses[-2].status_code == 200  # 20-я прошла
        assert responses[-1].status_code == 429  # 21-я заблокирована


# ==========================================
# GET: список и получение отдела
# ==========================================


class TestApiGetDepartments:
    def test_list_empty(self, web_client):
        _login(web_client)
        resp = _api_get(web_client, "/api/departments/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_list_includes_usage_counts(self, web_client):
        """usage считается одним запросом на сущность и отдаётся в списке."""
        _login(web_client)
        dept_id = _create_department_api(web_client, "Отдел с данными")
        _db_execute(
            web_client,
            "INSERT INTO knowledge_base (department_id, keywords, answer) "
            "VALUES (?, 'пароль, доступ', 'Сброс пароля — в IT-отделе')",
            (dept_id,),
        )
        resp = _api_get(web_client, "/api/departments/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["usage"]["knowledge"] == 1
        assert data[0]["usage"]["tickets"] == 0

    def test_get_department_by_id(self, web_client):
        _login(web_client)
        dept_id = _create_department_api(web_client, "Один отдел")
        resp = _api_get(web_client, f"/api/departments/{dept_id}/")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Один отдел"

    def test_get_department_not_found_404(self, web_client):
        _login(web_client)
        resp = _api_get(web_client, "/api/departments/999999/")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_department_admin_sees_only_own_department(self, web_client):
        """IDOR: DEPARTMENT_ADMIN не видит чужие отделы в списке."""
        _login(web_client)
        dept_a_id = _create_department_api(web_client, "Отдел А")
        _create_department_api(web_client, "Отдел Б")
        _create_department_admin(web_client, "deptadmin", "admin_pass_123", dept_a_id)
        _logout(web_client)
        _login(web_client, "deptadmin", "admin_pass_123")
        resp = _api_get(web_client, "/api/departments/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [d["name"] for d in data] == ["Отдел А"]


# ==========================================
# POST: создание отдела
# ==========================================


class TestApiCreateDepartment:
    def test_create_ok(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", json={"name": "Новый отдел"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "Новый отдел"
        assert body["data"]["id"] > 0
        assert body["data"]["usage"] == {
            "tickets": 0,
            "knowledge": 0,
            "faq": 0,
            "events": 0,
            "web_users": 0,
        }

    def test_create_duplicate_400(self, web_client):
        _login(web_client)
        _create_department_api(web_client, "Существующий")
        resp = _api_post(web_client, "/api/departments/create", json={"name": "Существующий"})
        assert resp.status_code == 400
        assert "уже существует" in resp.json()["error"]

    def test_create_empty_name_400(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", json={"name": "   "})
        assert resp.status_code == 400
        assert "пустым" in resp.json()["error"]

    def test_create_name_too_long_400(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", json={"name": "О" * 81})
        assert resp.status_code == 400
        assert "80" in resp.json()["error"]

    def test_create_null_name_400(self, web_client):
        """Regression: null в name → 400, а не отдел с именем «None»."""
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", json={"name": None})
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_create_array_body_400_not_500(self, web_client):
        """Regression: массив вместо объекта → 400, а не AttributeError → 500."""
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", raw='["name"]')
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert "error" in body

    def test_create_malformed_json_400(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", raw="{oops")
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_create_sanitizes_html(self, web_client):
        """XSS: теги экранируются sanitize_html (strip=False)."""
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/create", json={"name": "<b>Отдел</b>"})
        assert resp.status_code == 200
        name = resp.json()["data"]["name"]
        assert "<b>" not in name
        assert "Отдел" in name


# ==========================================
# POST: переименование отдела
# ==========================================


class TestApiRenameDepartment:
    def test_rename_ok(self, web_client):
        _login(web_client)
        dept_id = _create_department_api(web_client, "Старое имя")
        resp = _api_post(
            web_client, f"/api/departments/{dept_id}/rename", json={"name": "Новое имя"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Новое имя"

    def test_rename_not_found_404(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/999999/rename", json={"name": "Как угодно"})
        assert resp.status_code == 404

    def test_rename_duplicate_400(self, web_client):
        _login(web_client)
        _create_department_api(web_client, "Дубль")
        other_id = _create_department_api(web_client, "Другой")
        resp = _api_post(web_client, f"/api/departments/{other_id}/rename", json={"name": "Дубль"})
        assert resp.status_code == 400
        assert "уже существует" in resp.json()["error"]

    def test_rename_to_same_name_ok(self, web_client):
        """Переименование в собственное имя разрешено (проверка исключает себя)."""
        _login(web_client)
        dept_id = _create_department_api(web_client, "То же имя")
        resp = _api_post(
            web_client, f"/api/departments/{dept_id}/rename", json={"name": "То же имя"}
        )
        assert resp.status_code == 200

    def test_rename_malformed_body_400(self, web_client):
        _login(web_client)
        dept_id = _create_department_api(web_client, "Отдел")
        resp = _api_post(web_client, f"/api/departments/{dept_id}/rename", raw="[1]")
        assert resp.status_code == 400
        assert resp.json()["success"] is False


# ==========================================
# POST: удаление отдела
# ==========================================


class TestApiDeleteDepartment:
    def test_delete_ok(self, web_client):
        _login(web_client)
        dept_id = _create_department_api(web_client, "На удаление")
        resp = _api_post(web_client, f"/api/departments/{dept_id}/delete", json={})
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True
        after = _api_get(web_client, f"/api/departments/{dept_id}/")
        assert after.status_code == 404

    def test_delete_not_found_404(self, web_client):
        _login(web_client)
        resp = _api_post(web_client, "/api/departments/999999/delete", json={})
        assert resp.status_code == 404

    def test_delete_non_empty_400(self, web_client):
        """Отдел с привязанными данными удалить нельзя."""
        _login(web_client)
        dept_id = _create_department_api(web_client, "Непустой")
        _db_execute(
            web_client,
            "INSERT INTO knowledge_base (department_id, keywords, answer) "
            "VALUES (?, 'ключ', 'ответ')",
            (dept_id,),
        )
        resp = _api_post(web_client, f"/api/departments/{dept_id}/delete", json={})
        assert resp.status_code == 400
        assert "не пуст" in resp.json()["error"]
