# OSS Bot (v0.7.6)

**Версия:** 0.7.6  
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
│   ├── models.py              # SQLAlchemy модели (Users, Tickets, Logs, WebUsers)
│   ├── ticket_service.py      # Бизнес-логика заявок (создание, обновление, статусы)
│   ├── reporting.py           # Генерация ежедневных отчётов
│   ├── vk_client.py           # Клиент для отправки сообщений через VK API
│   ├── outbox.py              # Надёжная доставка ответов через outbox-очередь
│   └── logging_config.py      # Настройка логирования
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
│   ├── main.py                # FastAPI-приложение, middleware, роутеры
│   ├── dependencies.py        # Зависимости FastAPI (сессии, БД)
│   ├── templating.py          # Jinja2 шаблоны
│   ├── constants.py           # Константы (статусы, роли)
│   ├── form_utils.py          # Утилиты для форм
│   ├── static/                # Статические файлы
│   │   ├── app.js             # JavaScript панели
│   │   └── style.css          # CSS стили
│   ├── templates/             # HTML-шаблоны
│   │   ├── base.html          # Базовый шаблон
│   │   ├── dashboard.html     # Главная панель
│   │   ├── login.html         # Страница входа
│   │   ├── tickets.html       # Список заявок
│   │   ├── admins.html        # Управление администраторами
│   │   ├── departments.html   # Управление отделами
│   │   ├── faq.html           # FAQ
│   │   ├── knowledge_base.html# База знаний
│   │   ├── events.html        # События
│   │   ├── logs.html          # Журнал действий
│   │   └── error.html         # Страница ошибки
│   ├── routes/                # Маршруты FastAPI
│   │   ├── __init__.py        # Пакет маршрутов
│   │   ├── auth.py            # Аутентификация (вход, выход, rate limiting)
│   │   ├── admin_panel.py     # CRUD админ-панели
│   │   ├── api.py             # REST API
│   │   ├── dashboard.py       # Главная панель
│   │   ├── tickets.py         # Управление заявками
│   │   ├── admins.py          # Управление администраторами
│   │   ├── departments.py     # Управление отделами
│   │   ├── faq.py             # FAQ
│   │   ├── knowledge_base.py  # База знаний
│   │   ├── events.py          # События
│   │   ├── logs.py            # Журнал
│   │   └── dept_frame.py      # Фрейм отделов
   │   └── security/              # Безопасность
   │       ├── __init__.py        # Пакет безопасности
   │       ├── passwords.py       # Хеширование паролей (PBKDF2)
   │       ├── csrf.py            # CSRF-защита
   │       └── middleware.py      # Rate limiting, middleware
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
│   ├── healthcheck_bot.py     # Проверка здоровья бота
│   ├── backup.sh              # Резервное копирование БД
│   └── restore.sh             # Восстановление БД
│
├── tests/                     # Тесты
│   ├── __init__.py            # Пакет тестов
│   ├── conftest.py            # Фикстуры pytest
│   ├── test_auth.py           # Тесты аутентификации
│   ├── test_config.py         # Тесты конфигурации
│   ├── test_models.py         # Тесты моделей
│   ├── test_tickets.py        # Тесты заявок
│   ├── test_reports.py        # Тесты отчётов
│   ├── test_faq.py            # Тесты FAQ
│   ├── test_outbox.py         # Тесты outbox
│   ├── test_vk_client.py      # Тесты VK-клиента
│   ├── test_security.py       # Тесты безопасности
│   ├── test_form_validation.py# Тесты валидации форм
│   ├── test_ui_elements.py    # Тесты UI-элементов
│   ├── test_e2e_scenarios.py  # Сценарии end-to-end
│   └── test_postgres_integration.py  # Интеграция с PostgreSQL
│
├── docs/                      # Документация
│   ├── DEPLOYMENT.md          # Инструкция по развёртыванию
│   ├── WEB_ADMIN_GUIDE.md     # Руководство администратора панели
│   └── ADMIN_AND_DATABASE_GUIDE.md  # Администрирование и БД
│
├── docker-compose.yml         # Docker Compose (db, migrate, bot, tailscale, web-admin)
├── Dockerfile                 # Образ для бота и миграций
├── Dockerfile.web             # Образ для веб-панели
├── Dockerfile.tailscale       # Образ для Tailscale
└── .dockerignore              # Исключения для Docker
```

## 📦 Описание модулей

| Папка | Назначение |
|---|---|
| `core/` | Ядро: конфигурация, модели БД, бизнес-логика, отправка сообщений |
| `bots/vk/` | VK-бот: приём сообщений, клавиатуры, обработка команд |
| `web/` | Веб-панель: FastAPI-приложение, маршруты, шаблоны, безопасность |
| `alembic/` | Миграции схемы базы данных PostgreSQL |
| `scripts/` | Админ-скрипты: создание пользователей, бэкапы, healthcheck |
| `tests/` | Unit- и интеграционные тесты |
| `docs/` | Документация по развёртыванию и использованию |

## 🚀 Состав стека

| Компонент | Что делает |
|---|---|
| `bot` | VK Long Poll сервис: приём сообщений, создание обращений, админ-команды |
| `web-admin` | Веб-панель на FastAPI (статистика, заявки, FAQ, база знаний, события, логи) |
| `db` | PostgreSQL 16, изолирован внутри docker-сети (порт 5432 наружу не публикуется) |
| `tailscale` | VPN-узел: обеспечивает доступ к панели только из вашего tailnet |
| `migrate` | Одноразовый контейнер Alembic-миграций (выполняется до `bot` и `web-admin`) |

## Требования

- Linux-сервер (Ubuntu 22.04/24.04 или совместимый) с Docker Engine и Docker Compose v2.24+
- Аккаунт Tailscale и Auth key (https://login.tailscale.com/admin/settings/keys)
- Токен сообщества VK с включённым Long Poll API

## Быстрый запуск (Docker)

1. Клонируйте проект и подготовьте конфигурацию:

```bash
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

Вход - по учётным данным веб-панели. На пустой базе работает bootstrap-вход из
`.env` (`WEB_ADMIN_USERNAME`/`WEB_ADMIN_PASSWORD`); он отключается после создания
первого пользователя в `web_users`.

## Создание администраторов

```bash
# VK-суперадмин (получает ежедневные отчёты)
docker compose exec bot python scripts/init_superadmin.py --vk-id 123456789 --name "Имя Фамилия"

# Пользователь веб-панели
docker compose exec bot python scripts/create_web_user.py --username admin --role SUPERADMIN
docker compose exec bot python scripts/create_web_user.py --username zhilbyt --role DEPARTMENT_ADMIN --department "Жилбыт"
```

Роли и ограничения описаны в [docs/WEB_ADMIN_GUIDE.md](docs/WEB_ADMIN_GUIDE.md).

## Резервное копирование и восстановление

```bash
chmod +x scripts/backup.sh scripts/restore.sh
./scripts/backup.sh                                   # ежедневная копия + ротация
./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-09-01.sql.gz
```

Для production настройте удалённую выгрузку `BACKUP_REMOTE` (rclone) и расписание
cron. Подробнее - в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Проверки (разработка)

```bash
pip install -r requirements-dev.txt
pre-commit install      # хуки ruff/mypy/gitleaks перед каждым коммитом

ruff check .
mypy core web scripts bots
pytest -v
```

## Структура проекта

- `core/` - конфигурация, модели, БД, бизнес-логика заявок и outbox.
- `bots/vk/` - VK-обработчики и клавиатуры.
- `web/` - FastAPI-панель, маршруты, безопасность, шаблоны и стили.
- `alembic/` - миграции схемы БД.
- `scripts/` - администрирование, healthcheck, backup и restore.
- `tests/` - unit- и интеграционные проверки.

## Документация

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - установка на пустой сервер, Tailscale, масштабирование, откат.
- [docs/WEB_ADMIN_GUIDE.md](docs/WEB_ADMIN_GUIDE.md) - ежедневная работа в панели.
- [docs/ADMIN_AND_DATABASE_GUIDE.md](docs/ADMIN_AND_DATABASE_GUIDE.md) - таблицы, роли, администраторы.
- [web/README.md](web/README.md) - устройство web-модуля и security-контракт.
- [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) - историческое техническое задание MVP.

## Производительность

Проект рассчитан на нагрузку более 2500 пользователей. Ключевые настройки:

- Веб-панель запускается с одним воркером (`WEB_WORKERS=1`) — без Redis сессии многопроцессность небезопасна.
- Общий HTTP-клиент VK с keep-alive пулом соединений.
- Outbox-очередь доставки настраивается через `OUTBOX_BATCH_SIZE` и
  `OUTBOX_INTERVAL_SECONDS`.

Для масштабирования веб-панели необходимо:
1. Заменить cookie-сессию на Redis-backed SessionMiddleware
2. Либо использовать внешний store для CSRF-токенов
3. После этого можно увеличить `WEB_WORKERS`

Подробнее о планировании ёмкости - в разделе "Масштабирование" файла
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).