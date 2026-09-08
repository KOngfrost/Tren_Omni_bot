"""
Блок A.2. Формы ввода: валидация полей.

Проверяет:
- Обязательные и необязательные поля
- Граничные значения (максимальная длина названия отдела 80 символов)
- Спецсимволы и XSS/HTML-инъекции (санитизация через bleach)
- SQL-инъекции (не должны ломать систему)
- Некорректные типы данных (дата события)

Использует общую фикстуру web_client (TestClient + shared in-memory
SQLite, из conftest.py); прямые проверки данных — через stdlib sqlite3.
"""

import re


def _get_session_cookie(web_client):
    """Получить подписанную cookie Starlette-сессии после логина."""
    return web_client.cookies.get("session")


def _login(web_client):
    """Bootstrap-логин в панель."""
    login_page = web_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    resp = web_client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"Login failed: {resp.status_code}"
    return web_client


def _page_csrf(web_client, path):
    """Вернуть CSRF-токен со страницы (по атрибуту value формы)."""
    page = web_client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    if not match:
        raise AssertionError(f"CSRF token not found on {path}")
    return match.group(1)


def _post_form(web_client, path, data, csrf_token=None):
    """Отправить форму с CSRF-токеном и не следовать редиректу."""
    if csrf_token is None:
        csrf_token = _page_csrf(web_client, path)
    data = {**data, "csrf_token": csrf_token}
    return web_client.post(path, data=data, follow_redirects=False)


def _db_query(web_client, sql, params=()):
    """Прямой SELECT к shared in-memory БД теста (stdlib sqlite3)."""
    import sqlite3

    conn = sqlite3.connect(
        f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True
    )
    try:
        cur = conn.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def _db_execute(web_client, sql, params=()):
    """Прямой INSERT/UPDATE к shared in-memory БД теста."""
    import sqlite3

    conn = sqlite3.connect(
        f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True
    )
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _seed_department(web_client, name):
    """Создать отдел напрямую в БД и вернуть его id."""
    _db_execute(web_client, "INSERT INTO departments (name) VALUES (?)", (name,))
    rows = _db_query(web_client, "SELECT id FROM departments WHERE name = ?", (name,))
    return rows[0][0]


# ==========================================
# A.2 — Валидация формы создания отдела (/departments/create)
# ==========================================

class TestDepartmentFormValidation:
    """A.2 — Обязательные поля и граничные значения для отделов."""

    def test_create_department_empty_name_rejected(self, web_client):
        """Пустое название отдела отклоняется с flash-error."""
        _login(web_client)
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": "   "}, csrf)
        assert resp.status_code == 303
        page = web_client.get("/departments/")
        assert "Название отдела не может быть пустым" in page.text
        assert "Отделов пока нет" in page.text

    def test_create_department_81_chars_rejected(self, web_client):
        """Название длиннее 80 символов отклоняется."""
        _login(web_client)
        long_name = "О" * 81
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": long_name}, csrf)
        assert resp.status_code == 303
        page = web_client.get("/departments/")
        assert "длиннее 80 символов" in page.text

    def test_create_department_80_chars_accepted(self, web_client):
        """Граница: ровно 80 символов принимаются."""
        web_client = _login(web_client)
        edge_name = "О" * 80
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": edge_name}, csrf)
        assert resp.status_code == 303
        page = web_client.get("/departments/")
        assert edge_name in page.text
        rows = _db_query(web_client, "SELECT name FROM departments WHERE name = ?", (edge_name,))
        assert len(rows) == 1

    def test_create_department_xss_sanitized(self, web_client):
        """XSS-теги в названии отдела обезврежены (экранированы через bleach).

        Ожидаемое поведение: сырые < и > не сохраняются в БД
        (bleach.clean превращает их в &lt; / &gt;), значит тег не выполнится
        в браузере при отображении.
        """
        _login(web_client)
        xss_name = '<script>alert("xss")</script>Отдел'
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": xss_name}, csrf)
        assert resp.status_code == 303

        names = [r[0] for r in _db_query(web_client, "SELECT name FROM departments")]
        assert names, "Отдел должен быть создан"
        # Критически важно: в БД нет сырых <script>...</script>
        assert not any("<script>" in n for n in names), names
        # Данные хранятся в экранированном виде (безопасно)
        assert all("&lt;" in n or "<" not in n for n in names), names

    def test_create_department_sql_injection_saved_as_text(self, web_client):
        """SQL-инъекция не ломает систему; значение сохраняется как текст.

        Кавычка экранируется (&#x27;), значит инъекция не выполнится
        как SQL-выражение при корректном использовании параметризованных запросов.
        """
        _login(web_client)
        injection = "'; DROP TABLE departments; --"
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": injection}, csrf)
        assert resp.status_code == 303

        # Таблица departments не удалена — можно считать отделы
        names = [r[0] for r in _db_query(web_client, "SELECT name FROM departments")]
        assert len(names) == 1
        # Кавычка экранирована — инъекция обезврежена
        assert "&#x27;" in names[0] or "\\'" in names[0] or "'" not in names[0], names

    def test_create_department_duplicate_rejected(self, web_client):
        """Повторное создание отдела с тем же именем отклоняется."""
        _login(web_client)
        _seed_department(web_client, "Дубликат")

        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": "Дубликат"}, csrf)
        assert resp.status_code == 303
        page = web_client.get("/departments/")
        assert "уже существует" in page.text


# ==========================================
# A.2 — Валидация FAQ (/faq/)
# ==========================================

class TestFAQFormValidation:
    """A.2 — Обязательные поля FAQ и XSS-санитизация."""

    def test_faq_empty_question_rejected(self, web_client):
        """Пустой вопрос FAQ отклоняется."""
        _login(web_client)
        csrf = _page_csrf(web_client, "/faq/")
        resp = _post_form(
            web_client, "/faq/",
            {"department_id": "", "question": ""},
            csrf,
        )
        assert resp.status_code == 303
        page = web_client.get("/faq/")
        assert "Текст вопроса обязателен" in page.text

    def test_faq_final_without_answer_rejected(self, web_client):
        """Конечный элемент FAQ без ответа отклоняется."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Информ")

        csrf = _page_csrf(web_client, "/faq/")
        resp = _post_form(
            web_client, "/faq/",
            {
                "department_id": str(dept_id),
                "question": "Как получить справку?",
                "is_final": "on",
                "final_answer": "",
            },
            csrf,
        )
        assert resp.status_code == 303
        page = web_client.get("/faq/")
        assert "Для конечного элемента нужен ответ" in page.text

    def test_faq_xss_sanitized_in_question(self, web_client):
        """XSS-теги в вопросе FAQ удаляются."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Информ")

        csrf = _page_csrf(web_client, "/faq/")
        xss = '<img src=x onerror=alert(1)>Как заселиться?'
        resp = _post_form(
            web_client, "/faq/",
            {"department_id": str(dept_id), "question": xss},
            csrf,
        )
        assert resp.status_code == 303

        rows = _db_query(web_client, "SELECT question FROM faq_nodes")
        assert len(rows) == 1
        # Критично: нет сырого тега <img (с угловой скобкой), экранирование безопасно
        assert not any("<img" in n[0] for n in rows), rows
        assert "Как заселиться?" in rows[0][0]


# ==========================================
# A.2 — Валидация событий (/events/)
# ==========================================

class TestEventFormValidation:
    """A.2 — Обязательные поля и корректность даты события."""

    def test_event_empty_title_rejected(self, web_client):
        """Пустое название события отклоняется."""
        _login(web_client)
        csrf = _page_csrf(web_client, "/events/")
        resp = _post_form(
            web_client, "/events/",
            {"title": "", "event_date": "2026-10-01T10:30"},
            csrf,
        )
        assert resp.status_code == 303
        page = web_client.get("/events/")
        assert "Название события обязательно" in page.text

    def test_event_invalid_date_rejected(self, web_client):
        """Некорректная дата события отклоняется."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Культмасс")

        csrf = _page_csrf(web_client, "/events/")
        resp = _post_form(
            web_client, "/events/",
            {
                "department_id": str(dept_id),
                "title": "День открытых дверей",
                "event_date": "не-дата",
            },
            csrf,
        )
        assert resp.status_code == 303
        page = web_client.get("/events/")
        assert "Некорректная дата события" in page.text

    def test_event_xss_sanitized_in_title(self, web_client):
        """XSS-теги в названии события удаляются."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Культмасс")

        csrf = _page_csrf(web_client, "/events/")
        xss = '<script>alert(1)</script>Концерт'
        resp = _post_form(
            web_client, "/events/",
            {
                "department_id": str(dept_id),
                "title": xss,
                "event_date": "2026-10-01T18:00",
            },
            csrf,
        )
        assert resp.status_code == 303

        rows = _db_query(web_client, "SELECT title FROM events")
        assert len(rows) == 1
        # Критично: нет сырого <script>, текст "Концерт" на месте
        assert not any("<script>" in n[0] for n in rows), rows
        assert "Концерт" in rows[0][0]


# ==========================================
# A.2 — Валидация базы знаний (/knowledge/)
# ==========================================

class TestKnowledgeBaseFormValidation:
    """A.2 — Обязательные поля и санитизация базы знаний."""

    def test_kb_xss_sanitized_in_keywords(self, web_client):
        """XSS-теги в ключевых словах удаляются."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Жилбыт")

        csrf = _page_csrf(web_client, "/knowledge/")
        xss_keywords = '<script>alert(1)</script>кран'
        resp = _post_form(
            web_client, "/knowledge/",
            {"department_id": str(dept_id), "keywords": xss_keywords, "answer": "Звоните в Жилбыт"},
            csrf,
        )
        assert resp.status_code == 303

        rows = _db_query(web_client, "SELECT keywords, answer FROM knowledge_base")
        assert len(rows) == 1
        # Критично: нет сырого <script>, ключевое слово "кран" на месте
        assert not any("<script>" in n[0] for n in rows), rows
        assert "кран" in rows[0][0]

    def test_kb_empty_keywords_rejected(self, web_client):
        """Пустые ключевые слова и ответ отклоняются на сервере."""
        _login(web_client)
        dept_id = _seed_department(web_client, "Жилбыт")

        csrf = _page_csrf(web_client, "/knowledge/")
        resp = _post_form(
            web_client, "/knowledge/",
            {"department_id": str(dept_id), "keywords": "", "answer": ""},
            csrf,
        )
        assert resp.status_code == 303

        rows = _db_query(web_client, "SELECT keywords, answer FROM knowledge_base")
        assert len(rows) == 0
