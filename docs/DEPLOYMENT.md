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
systemctl enable --now docker
docker compose version   # ожидается v2.24.0 или новее
```

### 1.3. Firewall

Наружу открыт только SSH (22/tcp). Порты PostgreSQL (5432) и панели (8000)
в интернет НЕ публикуются - панель доступна через Tailscale.

```bash
ufw allow 22/tcp
ufw enable
```

## 2. Установка проекта

```bash
git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
cd /opt/oss_bot
cp .env.example .env
chmod 600 .env
```

## 3. Конфигурация (.env)

Заполните обязательные переменные:

| Переменная | Назначение |
|---|---|
| `POSTGRES_USER` | Пользователь БД (не дефолтный; запрещены `oss_bot`, `student_bot`) |
| `POSTGRES_PASSWORD` | Пароль БД, не короче 12 символов |
| `POSTGRES_DB` | Имя базы (по умолчанию `oss_bot`) |
| `VK_BOT_TOKEN` | Токен сообщества VK (Управление -> Работа с API -> Ключи доступа) |
| `ADMIN_VK_IDS` | VK ID администраторов через запятую |
| `SESSION_SECRET_KEY` | Секрет сессий веб-панели |
| `TAILSCALE_AUTH_KEY` | Auth key Tailscale (https://login.tailscale.com/admin/settings/keys) |
| `REPORT_TIME` | Время ежедневного отчёта (HH:MM), по умолчанию 09:00 |
| `APP_TIMEZONE` | Часовой пояс отчётов, по умолчанию Europe/Moscow |

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

# Пользователь веб-панели
docker compose exec bot python scripts/create_web_user.py --username admin --role SUPERADMIN
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

Расчёт на всплески (начало семестра, аварии): пик 1-2 события/сек и до
нескольких десятков сообщений администраторам в минуту.

Сервер: 4 vCPU / 8 GB RAM. Ключевые переменные `.env`:

| Переменная | По умолчанию | Когда менять |
|---|---|---|
| `WEB_WORKERS` | 1 | По умолчанию 1 (без Redis сессии многопроцессность небезопасна). Увеличивать только при подключении Redis |
| `OUTBOX_BATCH_SIZE` | 20 | Увеличивать, если очередь доставки растёт при пиковой нагрузке |
| `OUTBOX_INTERVAL_SECONDS` | 0.5 | Уменьшение ускоряет доставку, но добавляет нагрузку на VK API |

Узкие места и их решение:

- **VK API rate limits** - главный ограничитель. Все сообщения идут через
  outbox-воркер с очередью и ретраями, поэтому всплеск не теряет сообщения,
  а растягивается во времени. Не отправляйте рассылки напрямую в обход outbox.
- **PostgreSQL** - индексы по статусам и датам заявок уже созданы миграциями.
  Дальнейший рост: вынести БД на отдельный хост (`DB_HOST`), увеличить
  `shared_buffers`. Вертикальное масштабирование покрывает нагрузку в десятки
  тысяч пользователей.
- **Веб-панель** - запускается с 1 воркером (`WEB_WORKERS=1`). Статика
  отдаётся FastAPI; при росте - вынести на CDN или добавить reverse-proxy.

Проверка под нагрузкой ( smoke-тест очереди доставки): создайте несколько
сотен ответов в панели и следите за `docker compose logs -f bot` -
очередь должна разгребаться без ошибок rate limit.

## 10. Мониторинг и диагностика

```bash
docker compose ps                          # все сервисы healthy?
curl -s http://localhost:8000/health       # healthcheck панели (с сервера)
docker compose logs --tail 100 bot         # логи бота
docker compose logs --tail 100 web-admin   # логи панели
docker compose exec tailscale tailscale status
```

- Контейнер `bot` проверяет heartbeat-файл: если обработка событий зависла,
  docker пометит контейнер нездоровым.
- `web-admin` проверяет `GET /health` каждые 30 секунд.
- Частые проблемы: невалидный `VK_BOT_TOKEN` (бот падает на старте),
  истёкший `TAILSCALE_AUTH_KEY` (см. раздел 5), медленные запросы БД при
  пиках (смотрите `docker compose logs -f db` и `pg_stat_activity`).

## 11. Тесты и CI

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
pytest -v            # unit + интеграционные тесты
ruff check .         # линтер
mypy core web        # типизация
```

GitHub Actions (`.github/workflows/ci.yml`) прогоняет линтер и тесты на
каждый push и pull request. Перед деплоем на сервер убедитесь, что CI
зелёный на текущем коммите.