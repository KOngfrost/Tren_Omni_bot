# Модуль веб-панели (v0.7.9.9)

Веб-панель построена на FastAPI, Jinja2 и Tailwind-совместимой CSS дизайн-системе (Dark Graphite + Neon Pink). В продакшене запускается в многоворкерном режиме:
```bash
uvicorn web.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-2}
```

---

## 1. Архитектура и структура файлов

- `main.py` — инициализация FastAPI, регистрация middleware (сессии, CSRF, rate limit, CORS, security headers, Prometheus), глобальные обработчики ошибок и маршруты.
- `dependencies.py` — внедрение зависимостей FastAPI: валидация сессий, проверка ролей (`require_auth`, `require_writer`, `require_superadmin`), вычисление административного скоупа (`get_admin_scope`).
- `schemas.py` — Pydantic v2 схемы для версионированного REST API v1.
- `constants.py` — статусы обращений, роли пользователей, ограничения и пути.
- `form_utils.py` — утилиты безопасного парсинга и валидации HTML-форм.
- `templating.py` — конфигурация Jinja2 с кастомными фильтрами форматирования дат и часовых поясов.
- `routes/`:
  - `auth.py` — вход, выход, двухфакторная аутентификация (2FA OTP), lockout bootstrap-пользователя.
  - `legal.py` — публичные страницы соответствия законодательству РФ: `/legal/privacy`, `/legal/consent`, `/legal/terms`.
  - `admin_panel.py` — CRUD операции над сущностями админки.
  - `api_v1.py` — версионированный REST API v1 (`/api/v1/departments`, `/api/v1/stats`).
  - `api.py` — внутренние JSON-эндпоинты для веб-интерфейса.
  - `tickets.py`, `departments.py`, `admins.py`, `faq.py`, `knowledge_base.py`, `events.py`, `logs.py`, `dashboard.py`, `dept_frame.py` — постраничные контроллеры.
- `security/`:
  - `session_store.py` — распределённые сессии в Redis (`RedisSessionMiddleware`) с автоматическим fallback на in-memory при отсутствии Redis.
  - `csrf.py` — генерация и валидация CSRF-токенов в связке с сессией.
  - `passwords.py` — хеширование и верификация паролей (PBKDF2 SHA-256).
  - `middleware.py` — `DBRateLimiter` (защита от подбора паролей и спама запросов) и `SecurityHeadersMiddleware`.
- `static/`:
  - `style.css` — тёмная графитовая тема с неоновым розовым акцентом (`#ff4d9d`), утончённый прозрачный футер и баннер cookie.
  - `app.js`, `store.js` — клиентский JavaScript: живой поиск без перезагрузки, фильтры-пилюли, модальные окна, мобильное бургер-меню, логика согласия на cookie.
  - Фавиконки и логотипы: `favicon.ico`, `favicon.svg`, `favicon-32x32.png`, `favicon-16x16.png`, `apple-touch-icon.png`, `logo.png`, `logo_squircle.png`.
- `templates/` — шаблоны страниц (Jinja2).
  - `legal/` — шаблоны правовых документов (`privacy.html`, `consent.html`, `terms.html`).

---

## 2. Безопасность и контракт доступа

1. **Многоворкерные распределённые сессии**:
   - Сессия хранится в Redis под ключом `session:<id>` (TTL 3600 сек).
   - Cookie шифруются подписью `SESSION_SECRET_KEY` и защищены флагами `HttpOnly`, `SameSite=Strict`, `Secure`.
2. **Двухфакторная аутентификация (2FA)**:
   - При включении `TWO_FACTOR_ENABLED=true` вход требует подтверждения 6-значным OTP кодом, отправляемым в VK администратора.
3. **Защита Bootstrap**:
   - Вход по `.env` блокируется автоматически при наличии суперадминистратора в таблице `web_users`.
4. **CSRF Middleware**:
   - Все POST/PUT/DELETE запросы валидируют CSRF-токен.
5. **Соответствие 152-ФЗ и доступность**:
   - Публичные страницы политики конфиденциальности, согласия на обработку ПДн и правил сервиса.
   - Плавающий cookie-consent баннер, семантическая разметка (WCAG/ГОСТ), контрастные фокусные кольца, мета-теги и канонические URL.
6. **Контракт ошибок**:
   - Все необработанные исключения перехватываются глобальным обработчиком: клиенту отдается дружелюбное сообщение *"Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору"* и уникальный `error_id` для поиска в Sentry/логах.
7. **Наблюдаемость**:
   - `GET /health` — healthcheck приложения и подключения к БД (503 при сбое).
   - `GET /metrics` — экспорт метрик в формате Prometheus.

