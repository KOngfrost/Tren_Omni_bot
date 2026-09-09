# Модуль веб-панели

Панель запускается командой `uvicorn web.main:app --host 0.0.0.0 --port 8000` и требует `SESSION_SECRET_KEY`.

## Границы модуля

- `main.py` — FastAPI, middleware, healthcheck и регистрация маршрутов.
- `dependencies.py` — авторизация, роли и актуальный scope из `web_users`.
- `routes/` — заявки, дашборд, контент, логи и управление администраторами.
- `security/` — CSRF, security headers, rate limiting и пароли.
- `templates/` — Jinja2-шаблоны.
- `static/` — CSS админки.

## Контракт доступа

`require_auth`, `require_writer` и `require_superadmin` проверяют базовое право. Для выборок и операций с отделом используется `await get_admin_scope(session, user)`: только `SUPERADMIN` получает общий scope, `DEPARTMENT_ADMIN` получает отдел из БД.

Любой POST обязан пройти CSRF middleware. Изменения заявок пишутся в одной транзакции с записью `VkOutbox`. Доставка выбирает pending-записи через `FOR UPDATE SKIP LOCKED`, поэтому несколько экземпляров воркера не обрабатывают одну запись параллельно.

`GET /health` проверяет соединение с БД и возвращает 503 при ошибке. Тесты панели находятся в `tests/`.
