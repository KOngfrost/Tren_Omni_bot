# OSS Bot (v0.7.9.5)

**Версия:** 0.7.9.5  
OSS Bot — сервис приёма и обработки обращений студентов. Обращения приходят через
VK-сообщения, хранятся в PostgreSQL, а администраторы работают с ними в веб-панели
`oss-web-panel` (FastAPI). Ответы панели доставляются студентам через надёжный
outbox-воркер.

Веб-панель доступна только внутри вашей Tailscale-сети (`oss-web-panel.<tailnet>.ts.net`).
Наружу (в интернет) не публикуется ни один порт.

## 📁 Структура проекта

```
student_bot/
├── main.py                    # Точка входа: запуск VK-бота
├── pyproject.toml             # Конфигурация проекта (зависимости, настройки ruff/mypy)
├── mypy.ini                   # Настройки type-checker mypy
├── requirements.txt           # Основные зависимости production
├── requirements-dev.txt       # Зависимости для разработки (тесты, линтеры)
├── .env.example               # Шаблон конфигурации (заполните .env)
├── .env                       # Ваша конфигурация (НЕ коммитьте!)
├── .gitignore                 # Исключения для Git
│
├── core/                      # Ядро приложения
│   ├── __init__.py            # Пакет ядра
│   ├── config.py              # Конфигурация (читает переменные из .env)
│   ├── database.py            # Подключение к PostgreSQL, сессии
│   ├── models.py              # SQLAlchemy 2.0 модели (Users, Tickets, Logs, WebUsers)
│   ├── cache.py               # Сервис кэширования (Redis / InMemory fallback) с TTL
│   ├── redis_client.py        # Клиент и пул подключений Redis
│   ├── sentry.py              # Интеграция с Sentry SDK для мониторинга ошибок
│   ├── ticket_service.py      # Бизнес-логика заявок (создание, обновление, статусы)
│   ├── reporting.py           # Генерация ежедневных отчётов
│   ├── vk_client.py           # Клиент для отправки сообщений через VK API
│   ├── outbox.py              # Надёжная доставка ответов через outbox-очередь
│   ├── task_dispatcher.py     # Advisory locks для распределения задач между воркерами
│   ├── heartbeat.py           # Проверка жизнеспособности сервиса
│   └── logging_config.py      # Настройка структурированного логирования
│
├── bots/                      # VK-бот
│   ├── __init__.py            # Пакет ботов
│   └── vk/                    # VK-бот на vkbottle
│       ├── __init__.py        # Пакет VK-бота
│       ├── keyboards.py       # Клавиатуры для VK-сообщений
│       └── bot.py             # Инициализация VK-бота (vkbottle, long-poll)
│
├── web/                       # Веб-панель администратора
│   ├── __init__.py            # Пакет веб-приложения
│   ├── main.py                # FastAPI-приложение, middleware, роутеры, метрики
│   ├── dependencies.py        # Зависимости FastAPI (сессии, БД, scope)
│   ├── templating.py          # Jinja2 шаблоны
│   ├── constants.py           # Константы (статусы, роли)
│   ├── schemas.py             # Pydantic v2 схемы для API v1
│   ├── form_utils.py          # Утилиты для форм
│   ├── static/                # Статические файлы (Dark Graphite + Neon Pink UI)
│   │   ├── app.js             # JavaScript панели (поиск, фильтры, модалки)
│   │   └── style.css          # CSS стили дизайн-системы
│   ├── templates/             # HTML-шаблоны
│   │   ├── base.html          # Базовый шаблон (сайдбар, мобильная навигация, баннеры)
│   │   ├── dashboard.html     # Главная панель и метрики
│   │   ├── login.html         # Страница входа и 2FA подтверждения
│   │   ├── tickets.html       # Список заявок, фильтрация, быстрый поиск
│   │   ├── admins.html        # Управление администраторами и web-пользователями
│   │   ├── departments.html   # Управление отделами и ключевыми словами
│   │   ├── faq.html           # FAQ
│   │   ├── knowledge_base.html# База знаний
│   │   ├── events.html        # События
│   │   ├── logs.html          # Журнал действий
│   │   └── error.html         # Страница ошибки (фото экрана техадмину)
│   ├── routes/                # Маршруты FastAPI
│   │   ├── __init__.py        # Пакет маршрутов
│   │   ├── auth.py            # Аутентификация, 2FA, lockout, сессии
│   │   ├── admin_panel.py     # CRUD админ-панели
│   │   ├── api.py             # Внутренний REST API для веб-интерфейса
│   │   ├── api_v1.py          # Публичный версионированный REST API v1 (/api/v1)
│   │   ├── dashboard.py       # Главная панель
│   │   ├── tickets.py         # Управление заявками
│   │   ├── admins.py          # Управление администраторами
│   │   ├── departments.py     # Управление отделами
│   │   ├── faq.py             # FAQ
│   │   ├── knowledge_base.py  # База знаний
│   │   ├── events.py          # События
│   │   ├── logs.py            # Журнал аудита
│   │   ├── dept_frame.py      # Фрейм отделов
│   │   └── vk_callback.py     # Обработка VK Callback API (вебхуки)
│   └── security/              # Безопасность
│       ├── __init__.py        # Пакет безопасности
│       ├── passwords.py       # Хеширование паролей (PBKDF2)
│       ├── csrf.py            # CSRF-защита
│       ├── session_store.py   # Redis-хранилище сессий и middleware
│       └── middleware.py      # Rate limiting (DBRateLimiter), security headers
│
├── monitoring/                # Мониторинг и алертинг
│   └── prometheus/            # Конфигурация Prometheus и правила алертов (alerts.yml)
│
├── alembic/                   # Миграции БД
│   ├── alembic.ini            # Конфигурация Alembic
│   ├── env.py                 # Скрипт окружения Alembic
│   ├── script.py.mako         # Шаблон миграций
│   └── versions/              # Файлы миграций
│       └── *.py               # Версии миграций
│
├── scripts/                   # Скрипты администрирования
│   ├── init_superadmin.py     # Создание VK-суперадмина
│   ├── create_web_user.py     # Создание пользователя веб-панели
│   ├── check_db.py            # Проверка доступности БД
│   ├── healthcheck_bot.py     # Проверка здоровья бота
│   ├── backup.sh              # Резервное копирование БД
│   └── restore.sh             # Восстановление БД
│
├── tests/                     # Тесты (245 тестов, pytest-cov >= 60%)
│   ├── __init__.py            # Пакет тестов
│   ├── conftest.py            # Фикстуры pytest
│   ├── test_auth.py           # Тесты аутентификации
│   ├── test_two_factor_and_bootstrap.py # Тесты 2FA и защиты bootstrap
│   ├── test_redis_cache_and_sessions.py # Тесты Redis кэша и сессий
│   ├── test_api_v1.py         # Тесты REST API v1 и Prometheus
│   ├── test_vk_callback.py    # Тесты VK Callback API (вебхуки)
│   ├── test_config.py         # Тесты конфигурации
│   ├── test_models.py         # Тесты моделей SQLAlchemy
│   ├── test_tickets.py        # Тесты заявок и сервиса
│   ├── test_reports.py        # Тесты отчётов
│   ├── test_faq.py            # Тесты FAQ
│   ├── test_outbox.py         # Тесты outbox
│   ├── test_vk_client.py      # Тесты VK-клиента
│   ├── test_security.py       # Тесты безопасности и CSRF
│   ├── test_form_validation.py# Тесты валидации форм
│   ├── test_ui_elements.py    # Тесты UI-элементов
│   ├── test_e2e_scenarios.py  # Сценарии end-to-end
│   ├── test_postgres_integration.py  # Интеграция с PostgreSQL
│   └── load/                  # Нагрузочное тестирование
│       └── locustfile.py      # Сценарии Locust для нагрузочных тестов
│
├── docs/                      # Документация
│   ├── DEPLOYMENT.md          # Инструкция по развёртыванию
│   ├── HA_AND_SCALING_GUIDE.md# Руководство по масштабированию, PgBouncer и HA
│   ├── WEB_ADMIN_GUIDE.md     # Руководство администратора панели
│   └── ADMIN_AND_DATABASE_GUIDE.md  # Администрирование и БД
│
├── docker-compose.yml         # Docker Compose (db, pgbouncer, redis, migrate, bot, tailscale, web-admin, prometheus)
├── Dockerfile                 # Образ для бота и миграций
├── Dockerfile.web             # Образ для веб-панели (многоворкерный Uvicorn)
├── Dockerfile.tailscale       # Образ для Tailscale
└── .dockerignore              # Исключения для Docker
```

## 📦 Описание модулей

| Папка | Назначение |
|---|---|
| `core/` | Ядро: конфигурация, модели БД, бизнес-логика, отправка сообщений, Redis-кэш, Sentry |
| `bots/vk/` | VK-бот: приём сообщений, клавиатуры, обработка команд |
| `web/` | Веб-панель: FastAPI-приложение, API v1, шаблоны, Redis-сессии, 2FA, Prometheus |
| `alembic/` | Миграции схемы базы данных PostgreSQL |
| `scripts/` | Админ-скрипты: создание пользователей, бэкапы, healthcheck |
| `tests/` | Unit- и интеграционные тесты с замером покрытия (pytest-cov) |
| `docs/` | Документация по развёртыванию и использованию |

## 🚀 Состав стека

| Компонент | Что делает |
|---|---|
| `bot` | VK сервис (Long Poll или Callback API): приём сообщений, создание обращений, админ-команды |
| `web-admin` | Веб-панель на FastAPI (статистика, заявки, FAQ, база знаний, события, логи, API v1, метрики) |
| `pgbouncer` | Пулер соединений к PostgreSQL (режим транзакций, порт 6432) |
| `db` | PostgreSQL 16, изолирован внутри docker-сети (порт 5432 наружу не публикуется) |
| `oss_bot_redis` | Redis 7, распределённое сессионное хранилище, общий кэш с TTL, координация воркеров |
| `prometheus` | Сервер сбора метрик и валидации правил алертинга (порт 9090) |
| `tailscale` | VPN-узел: обеспечивает доступ к панели только из вашего tailnet |
| `migrate` | Одноразовый контейнер Alembic-миграций (выполняется до `bot` и `web-admin`) |

## Требования

- Linux-сервер (Ubuntu 22.04/24.04 или совместимый) с Docker Engine и Docker Compose v2.24+
- Аккаунт Tailscale и Auth key (https://login.tailscale.com/admin/settings/keys)
- Токен сообщества VK с включённым Long Poll API или настроенным Callback API

## Быстрый запуск (Docker)

1. Создайте каталог проекта и клонируйте репозиторий:

```bash
# Создание каталога и выдача прав пользователю
sudo mkdir -p /opt/oss_bot
sudo chown -R $USER:$USER /opt/oss_bot

git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
cd /opt/oss_bot
cp .env.example .env
chmod 600 .env            # ограничьте права на файл с секретами
```

2. Заполните `.env`:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` - доступы к БД
  (пользователь и пароль НЕ должны быть дефолтными; пароль - не короче 12 символов).
- `VK_BOT_TOKEN` - токен сообщества VK.
- `ADMIN_VK_IDS` - VK ID администраторов через запятую.
- `SESSION_SECRET_KEY` - сгенерируйте случайное значение:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

- `TAILSCALE_AUTH_KEY` - ключ из Tailscale Admin Console.

3. Запустите стек (Tailscale поднимается вместе со всеми сервисами):

```bash
docker compose up -d --build
docker compose ps
curl -s http://localhost:8000/health   # ожидается {"status":"ok"} (с самого сервера)
```

*(При ошибках связи с Docker Hub или `connection refused` настройте зеркала по инструкции в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#13-настройка-зеркал-docker-hub-для-рф--при-connection-refused)).*

4. Включите HTTPS-доступ к панели через Tailscale:

```bash
./scripts/setup_tailscale.sh
```

После этого панель доступна по адресу `https://oss-web-panel.<tailnet>.ts.net/`.

## Доступ к панели

Панель открывается только на устройствах, подключённых к тому же tailnet:

```
https://oss-web-panel.<tailnet>.ts.net/
```

Вход — по учётным данным веб-панели.
- **Двухфакторная аутентификация (2FA)**: при включении `TWO_FACTOR_ENABLED=true` после ввода логина/пароля бот автоматически присылает 6-значный одноразовый код в личные сообщения VK администратора (`core/outbox.py`).
- **Bootstrap-защита**: на чистой базе доступен вход по `.env` (`WEB_ADMIN_USERNAME`/`WEB_ADMIN_PASSWORD`). Как только в `web_users` появляется постоянный пользователь со статусом `SUPERADMIN`, bootstrap-вход **автоматически блокируется** для предотвращения несанкционированного доступа. При активной сессии bootstrap вверху панели отображается яркий предупреждающий баннер.
- **Дружелюбная обработка ошибок**: при возникновении непредвиденных сбоев бот и панель выводят понятное сообщение с просьбой сфотографировать экран и направить техническому администратору (в веб-панели генерируется `error_id`).

## 🔑 Ключевые возможности v0.7.9.5

- 🎨 **Современный адаптивный UI**: тёмная графитовая тема с неоновым акцентом (`#ff4d9d`), утончённый прозрачный футер, адаптивный сайдбар, мобильное меню, живой поиск и фильтры по статусам без перезагрузки страниц.
- 🏛️ **Соответствие законодательству РФ (152-ФЗ)**: официальные разделы политики обработки персональных данных (`/legal/privacy`), согласия субъекта (`/legal/consent`) и правил сервиса (`/legal/terms`), компактный плавающий баннер согласия с cookie (pill-dock).
- ♿ **Цифровая доступность и SEO**: полная семантическая разметка (WCAG/ГОСТ), skip-links для навигации с клавиатуры, контрастные фокусные кольца, альтернативный текст для графических элементов, канонические ссылки (`canonical_url`) и уникальные мета-теги на всех страницах.
- ⚡ **Redis сессии и кэш**: распределённое хранение сессий (`RedisSessionMiddleware`) и многоуровневый кэш данных с TTL и инвалидацией по шаблонам (`core/cache.py`).
- 🚀 **Многоворкерный режим**: поддержка масштабирования Uvicorn (`WEB_WORKERS=2+`) благодаря внешнему сессионному хранилищу и координации задач через PostgreSQL advisory locks.
- 🔐 **Безопасность корпоративного уровня**: PBKDF2 хеширование, CSRF-токены, защита от подбора (DBRateLimiter), строгие CSP/HSTS заголовки, 2FA через VK.
- 📊 **Наблюдаемость и мониторинг**: экспорт метрик Prometheus (`/metrics`), интеграция с Sentry SDK для отслеживания ошибок и трассировки, структурированные логи (JSON/Text).
- 🔌 **REST API v1**: версионированный API (`/api/v1/`) со строгой валидацией входных/выходных данных на Pydantic v2 и интерактивной документацией Swagger UI (`/docs`) и ReDoc (`/redoc`).

## Создание администраторов

```bash
# VK-суперадмин (получает ежедневные отчёты)
docker compose exec bot python scripts/init_superadmin.py --vk-id 123456789 --name "Имя Фамилия"

# Пользователь веб-панели (с привязкой к VK ID для двухфакторной аутентификации 2FA)
docker compose exec bot python scripts/create_web_user.py --username admin --role SUPERADMIN --vk-id 123456789
docker compose exec bot python scripts/create_web_user.py --username zhilbyt --role DEPARTMENT_ADMIN --department "Жилбыт" --vk-id 987654321
```

Роли и ограничения описаны в [docs/WEB_ADMIN_GUIDE.md](docs/WEB_ADMIN_GUIDE.md).

## Резервное копирование и восстановление

```bash
chmod +x scripts/backup.sh scripts/restore.sh
./scripts/backup.sh                                   # ежедневная копия + ротация
./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-09-01.sql.gz
```

Для production настройте удалённую выгрузку `BACKUP_REMOTE` (rclone) и расписание
cron. Подробнее — в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Проверки и CI/CD (разработка)

```bash
pip install -r requirements-dev.txt
pre-commit install      # хуки ruff/mypy/gitleaks перед каждым коммитом

ruff check .            # проверка линтером
ruff format --check .   # проверка форматирования
mypy core web scripts bots
pytest --cov=core --cov=web --cov=bots  # запуск тестов с отчётом о покрытии
```

Пайплайн GitHub Actions (`.github/workflows/ci.yml`) автоматически проверяет форматирование, линтер и запускает тесты на Python 3.11 и 3.12 с запущенным сервисом Redis.

## Документация

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — установка на пустой сервер, Tailscale, Redis, масштабирование, откат.
- [docs/WEB_ADMIN_GUIDE.md](docs/WEB_ADMIN_GUIDE.md) — руководство по работе с обновлённой веб-панелью, 2FA, тикетами и отделами.
- [docs/ADMIN_AND_DATABASE_GUIDE.md](docs/ADMIN_AND_DATABASE_GUIDE.md) — схема БД, таблицы, управление доступом, Redis и аудит.
- [web/README.md](web/README.md) — архитектура веб-модуля, API v1, security-контракт и сессии.
- [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) — исходное ТЗ проекта и хроника архитектурных изменений.

## Производительность и масштабирование

Проект оптимизирован под высокие пиковые нагрузки (>2500 пользователей):

- **Многоворкерный режим веб-панели**: включён по умолчанию (`WEB_WORKERS=2`). Сессии пользователей сохраняются в Redis через `RedisSessionMiddleware`, поэтому запросы пользователя могут без потери контекста обрабатываться любым воркером.
- **Распределённое кэширование**: часто запрашиваемые данные (отделы, ключевые слова, FAQ) кэшируются в Redis с настраиваемым TTL (`CACHE_DEFAULT_TTL=300`) и автоматической wildcard-инвалидацией при изменениях.
- **Координация фоновых задач**: фоновый воркер Outbox в веб-панели использует распределённые блокировки PostgreSQL (`pg_try_advisory_lock`), гарантируя, что доставку сообщений выполняет ровно один процесс.
- **Пул соединений к VK API**: постоянный HTTP-клиент с keep-alive соединениями и настраиваемыми интервалами отправки (`OUTBOX_BATCH_SIZE`, `OUTBOX_INTERVAL_SECONDS`).

Подробнее о планировании ёмкости и тюнинге — в разделе "Масштабирование" файла
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).