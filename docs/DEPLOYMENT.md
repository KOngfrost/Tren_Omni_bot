# Установка на пустой сервер и эксплуатация

Это руководство описывает установку OSS Bot на только что созданный сервер:
с нуля, без предположений о предустановленных пакетах.

## 1. Подготовка сервера

Требования: любой современный Linux с systemd, минимум 2 vCPU и 4 GB RAM
(для нагрузки >2500 пользователей рекомендовано 4 vCPU и 8 GB RAM).

### 1.1. Обновление системы

```bash
apt update && apt upgrade -y
```

### 1.2. Установка Docker Engine и Compose plugin

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# Выдача прав текущему пользователю для работы с Docker без sudo
sudo usermod -aG docker $USER
newgrp docker

docker compose version   # ожидается v2.24.0 или новее
```

### 1.3. Настройка зеркал Docker Hub (для РФ / при connection refused)

Если при загрузке базовых образов возникает ошибка `dial tcp ...:443: connect: connection refused` или таймауты:

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://dockerhub.timeweb.cloud",
    "https://mirror.gcr.io",
    "https://huecker.io"
  ]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 1.4. Firewall

Наружу открыт только SSH (22/tcp). Порты PostgreSQL (5432) и панели (8000)
в интернет НЕ публикуются - панель доступна через Tailscale.

```bash
ufw allow 22/tcp
ufw enable
```

## 2. Установка проекта

Поскольку каталог `/opt` в Linux принадлежит `root`, перед клонированием создайте директорию с правами вашего текущего пользователя:

```bash
# Создание каталога и назначение владельца
sudo mkdir -p /opt/oss_bot
sudo chown -R $USER:$USER /opt/oss_bot

# Клонирование репозитория
git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
cd /opt/oss_bot
cp .env.example .env
chmod 600 .env
```

### 2.1. Развертывание в локальной виртуальной машине (VirtualBox / NAT)

Если вы запускаете проект в виртуальной машине VirtualBox в режиме сети NAT:
1. Откройте **Настройки ВМ** -> **Сеть** -> **Адаптер 1 (NAT)** -> **Проброс портов**.
2. Добавьте правило для SSH:
   - **Имя**: `SSH`
   - **Протокол**: `TCP`
   - **Адрес хоста**: *(оставьте пустым)*
   - **Порт хоста**: `2222`
   - **Адрес гостя**: *(оставьте пустым)*
   - **Порт гостя**: `22`
3. Подключение с хостовой машины (Windows PowerShell / macOS / Linux):
   ```powershell
   ssh -p 2222 <имя_пользователя_в_виртуалке>@127.0.0.1
   ```
   > **Важно:** Подключайтесь к `127.0.0.1` (или `localhost`), а **не** к внутреннему IP виртуалки (например `10.0.2.15`), так как в режиме NAT внутренний адрес недоступен напрямую с хоста.

## 3. Конфигурация (.env)

Заполните обязательные переменные:

| Переменная | Назначение |
|---|---|
| `POSTGRES_USER` | Пользователь БД (не дефолтный; запрещены `oss_bot`, `student_bot`) |
| `POSTGRES_PASSWORD` | Пароль БД, не короче 12 символов |
| `POSTGRES_DB` | Имя базы (по умолчанию `oss_bot`) |
| `DB_USE_PGBOUNCER` | Использовать ли пулер PgBouncer (`true`/`false`, по умолчанию `false`) |
| `REDIS_URL` | URL Redis для распределённых сессий и кэша (`redis://oss_bot_redis:6379/0`) |
| `CACHE_DEFAULT_TTL` | Время жизни кэша в секундах (по умолчанию 300) |
| `TWO_FACTOR_ENABLED` | Двухфакторная аутентификация в веб-панель через VK OTP (`true`/`false`) |
| `WEB_WORKERS` | Количество рабочих процессов веб-панели (по умолчанию 2) |
| `VK_MODE` | Режим работы VK-бота: `longpoll` (по умолчанию) или `callback` |
| `VK_CONFIRMATION_TOKEN` | Строка подтверждения для Callback API (если `VK_MODE=callback`) |
| `VK_CALLBACK_SECRET` | Секретный ключ сообщества Callback API (если `VK_MODE=callback`) |
| `VK_BOT_TOKEN` | Токен сообщества VK (Управление -> Работа с API -> Ключи доступа) |
| `ADMIN_VK_IDS` | VK ID администраторов через запятую |
| `SESSION_SECRET_KEY` | Секрет сессий веб-панели |
| `TAILSCALE_AUTH_KEY` | Auth key Tailscale (https://login.tailscale.com/admin/settings/keys) |
| `REPORT_TIME` | Время ежедневного отчёта (HH:MM), по умолчанию 09:00 |
| `APP_TIMEZONE` | Часовой пояс отчётов, по умолчанию Europe/Moscow |
| `SENTRY_DSN` | (Опционально) DSN проекта Sentry для мониторинга ошибок |

Генерация секретов:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'   # SESSION_SECRET_KEY
python3 -c 'import secrets; print(secrets.token_urlsafe(24))'   # POSTGRES_PASSWORD
```

В production `APP_ENV=production` (по умолчанию). Приложение откажется
запускаться с дефолтными или слабыми учётными данными.

## 4. Запуск

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 migrate      # миграции должны завершиться успешно
docker compose logs --tail 100 bot web-admin
curl -s http://localhost:8000/health        # {"status":"ok"} (с самого сервера)
```

> [!TIP]
> **Решение проблем со сборкой:**
> - Если сборка обрывается с `connection refused` или таймаутом при загрузке базовых образов, скачайте их предварительно:
>   ```bash
>   docker pull python:3.11-slim-bookworm
>   docker pull tailscale/tailscale:latest
>   ```
> - Если сборка подвисает на параллельных шагах BuildKit:
>   ```bash
>   DOCKER_BUILDKIT=0 docker compose build
>   docker compose up -d
>   ```

Tailscale-контейнер поднимается вместе со стеком автоматически. Схема БД
изменяется только через Alembic (контейнер `migrate`).

## 5. Доступ к панели (Tailscale)

Веб-панель работает в общей compose-сети (как bot/db) и НЕ публикуется на хост:
Tailscale-контейнер проксирует HTTPS на неё по имени сервиса (`http://web-admin:8000`).
Чтобы открыть доступ по HTTPS, выполните один раз:

```bash
chmod +x scripts/setup_tailscale.sh
./scripts/setup_tailscale.sh
```

Скрипт:
1. Запускает стек и ждёт регистрации Tailscale-узла `oss-web-panel`.
2. Включает `tailscale serve` (HTTPS).
3. Выставляет `SESSION_HTTPS_ONLY=true` в `.env` и перезапускает панель.

После этого панель доступна внутри tailnet:

```
https://oss-web-panel.<tailnet>.ts.net/
```

`<tailnet>` - имя вашей Tailscale-сети (видно в Tailscale Admin Console).

Для Windows-серверов используйте `scripts/setup_tailscale.ps1`.

Диагностика Tailscale:

```bash
docker compose logs --tail 200 tailscale
docker compose exec tailscale tailscale status
docker compose exec tailscale tailscale serve status
```

Если регистрация не удалась (ошибка `register request`): проверьте интернет и
DNS в docker-сети, срок действия Auth key, затем обновите ключ в `.env` и
пересоздайте контейнеры: `docker compose up -d --force-recreate tailscale`.

## 6. Первые администраторы

```bash
# VK-суперадмин (получает ежедневные отчёты)
docker compose exec bot python scripts/init_superadmin.py --vk-id 123456789 --name "Имя Фамилия"

# Пользователь веб-панели (с привязкой к VK ID для 2FA)
docker compose exec bot python scripts/create_web_user.py --username admin --role SUPERADMIN --vk-id 123456789
```

Bootstrap-вход из `.env` отключается после создания первого активного
пользователя в `web_users`.

## 7. Резервное копирование и восстановление

Локальная копия не спасает от потери сервера, поэтому в production обязательно
настраивается удалённая выгрузка (rclone на S3-совместимое хранилище).

### 7.1. Ручная копия

```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```

Скрипт ждёт готовности БД, делает `pg_dump | gzip`, проверяет целостность
архива (`gzip -t`) и выполняет ротацию: 7 дневных, 4 недельных (воскресенье),
3 месячных (1-е число) копий.

### 7.2. Удалённая выгрузка и расписание

Установите rclone и настройте remote (`rclone config`), затем добавьте cron:

```bash
apt install -y rclone
crontab -e
# Ежедневно в 03:00 (подставьте свой remote):
0 3 * * * BACKUP_REMOTE=myb2:oss_bot_backups /opt/oss_bot/scripts/backup.sh >> /var/log/oss_bot_backup.log 2>&1
```

Без `BACKUP_REMOTE` скрипт только предупреждает в stderr.

### 7.3. Восстановление

```bash
./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-09-01.sql.gz
# после успешного восстановления:
docker compose start bot web-admin
```

Скрипт проверяет архив, останавливает `bot` и `web-admin`, пересоздаёт базу и
восстанавливает дамп. Подтверждение (`yes`) обязательно. Раз в месяц
проверяйте восстановление на тестовой базе — бэкап, который ни разу не
восстанавливали, нельзя считать рабочим.

## 8. Обновление и откат

### 8.1. Обновление

```bash
cd /opt/oss_bot
git pull
docker compose build
docker compose up -d
docker compose logs --tail 50 migrate   # миграции должны пройти без ошибок
```

Схема БД меняется только миграциями Alembic (контейнер `migrate`), `bot` и
`web-admin` стартуют строго после их успешного завершения.

### 8.2. Откат версии

```bash
git log --oneline -5
git checkout <предыдущий_коммит>
docker compose build && docker compose up -d
```

Если миграция необратима (удаляет/переименовывает данные), сначала
восстановите базу из копии на момент до обновления (раздел 7.3), затем
запускайте предыдущую версию.

## 9. Масштабирование (нагрузка >2500 пользователей)

Архитектура v0.8.1 полностью готова к горизонтальному масштабированию благодаря распределённому хранилищу Redis:

Сервер: 4 vCPU / 8 GB RAM. Ключевые переменные `.env`:

| Переменная | По умолчанию | Когда менять |
|---|---|---|
| `WEB_WORKERS` | 2 | Увеличивать до 4–8 в зависимости от количества доступных ядер CPU |
| `REDIS_URL` | `redis://oss_bot_redis:6379/0` | Менять при выносе Redis на отдельный сервер или в Managed-кластер |
| `CACHE_DEFAULT_TTL` | 300 | Регулировать длительность кэширования для снижения нагрузки на БД |
| `OUTBOX_BATCH_SIZE` | 20 | Увеличивать, если очередь доставки растёт при пиковой нагрузке |
| `OUTBOX_INTERVAL_SECONDS` | 0.5 | Уменьшение ускоряет доставку, но добавляет нагрузку на VK API |

Ключевые решения для высокой производительности:

- **Redis Session Middleware**: состояние сессий пользователей и CSRF-токены хранятся в Redis. Запросы от одного пользователя прозрачно распределяются между любым числом воркеров Uvicorn без потери авторизации.
- **Распределённый кэш с TTL**: часто запрашиваемые сущности (списки отделов, ключевые слова, FAQ, статистика) кэшируются через `core/cache.py` со сбросом по шаблонам (wildcard invalidation) при любых мутациях.
- **Distributed Locks для Outbox**: доставка сообщений из очереди координируется через распределённую блокировку Redis (`core/task_dispatcher.py`): атомарный захват `SET NX PX`, автопродление TTL и освобождение через Lua-скрипт с проверкой владельца. В отличие от `pg_try_advisory_lock`, Redis-замок не зависит от PgBouncer (transaction pooling) и не держит соединение к PostgreSQL. Даже при десятках запущенных инстансов веб-панели и бота одно и то же сообщение никогда не будет отправлено дважды.
- **VK API rate limits**: фоновый воркер с очередью и плавными ретраями гарантирует отсутствие превышения лимитов API ВКонтакте.

## 10. Мониторинг и диагностика

```bash
docker compose ps                          # статус всех сервисов (db, redis, bot, web-admin, tailscale)
curl -s http://localhost:8000/health       # healthcheck веб-панели и БД
curl -s http://localhost:8000/metrics      # метрики Prometheus (запросы, задержки, ошибки)
docker compose exec oss_bot_redis redis-cli ping # проверка Redis (ожидается PONG)
docker compose logs --tail 100 bot         # логи бота
docker compose logs --tail 100 web-admin   # логи панели
docker compose exec tailscale tailscale status
```

- **Sentry**: при указании `SENTRY_DSN` все необработанные исключения автоматически отправляются в Sentry с полным стектрейсом, окружением и контекстом запроса.
- **Prometheus**: эндпоинт `/metrics` предоставляет стандартные метрики для сбора в Prometheus / Grafana.
- **Дружелюбная обработка ошибок (Фото для техадмина)**: при возникновении ошибки на стороне сервера пользователю показывается понятное сообщение с просьбой сфотографировать экран и отправить техническому администратору. В веб-панели выводится уникальный `error_id`, по которому администратор может мгновенно найти запись в логах или в Sentry.
- **Heartbeat бота**: контейнер `bot` обновляет файл проверки жизнеспособности; при зависании фонового цикла Docker пометит контейнер как `unhealthy`.

## 11. Тесты и CI/CD

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
pytest --cov=core --cov=web --cov=bots   # запуск 240 тестов с замером покрытия (>= 60%)
ruff check .                              # проверка линтером
ruff format --check .                     # проверка форматирования
mypy core web scripts bots                # строгая статическая типизация
```

GitHub Actions (`.github/workflows/ci.yml`):
- Матрица тестирования: Python 3.11 и Python 3.12.
- Запуск сервиса Redis в контейнере для полноценных интеграционных тестов сессий и кэша.
- Автоматическая проверка форматирования, линтера, валидации docker-compose и контроль порога покрытия кода тестами.