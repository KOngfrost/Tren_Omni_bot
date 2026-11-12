# 🚀 Руководство по установке и эксплуатации (v1.0.0)

> **Для кого этот документ:** Системные администраторы, DevOps-инженеры и разработчики.  
> **Цель:** Развернуть проект с нуля на чистом сервере Linux за 10–15 минут и обеспечить надёжную эксплуатацию.

---

## 📋 Чек-лист перед установкой

Перед началом убедитесь, что у вас есть:
1. Выделенный сервер или VDS с операционной системой **Ubuntu 22.04 / 24.04 LTS** (или Debian 12).
2. Минимальные ресурсы: **2 vCPU**, **4 GB RAM**, **20 GB SSD** (для высокой нагрузки рекомендуется 4 vCPU и 8 GB RAM).
3. Доменное имя (`<YOUR_DOMAIN>`), направленное на публичный IP-адрес сервера (A-запись), либо reverse-tunnel / Tailscale Funnel.
4. Токен сообщества ВКонтакте с включёнными сообщениями сообщества и LongPoll / Callback API.

---

## Шаг 1. Подготовка сервера

Подключитесь к серверу по SSH:
```bash
ssh <ADMIN_USER>@<SERVER_HOST>
```

### 1.1. Обновление пакетов ОС
```bash
apt update && apt upgrade -y
apt install -y curl git ufw jq
```

### 1.2. Установка Docker и Docker Compose
```bash
# Установка официального Docker через скрипт вендора
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# Проверка версии Docker Compose (требуется v2.24+)
docker compose version
```

### 1.3. Настройка зеркал Docker Hub (для серверов в РФ)
Если при скачивании базовых образов возникают ошибки таймаута или `connection refused`, настройте доверенные зеркала:
```bash
mkdir -p /etc/docker
tee /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://dockerhub.timeweb.cloud",
    "https://mirror.gcr.io",
    "https://huecker.io"
  ]
}
EOF
systemctl daemon-reload
systemctl restart docker
```

### 1.4. Настройка фаервола (UFW)
Наружу должны быть открыты только SSH (рекомендуется ограничить доверенной подсетью) и веб-шлюз Caddy (HTTP/HTTPS). Все внутренние сервисы (PostgreSQL, Redis, PgBouncer, Uvicorn) изолированы внутри сети Docker:
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow <SSH_PORT>/tcp comment 'SSH'
ufw allow <HTTP_PORT>/tcp comment 'Caddy HTTP'
ufw allow <HTTPS_PORT>/tcp comment 'Caddy HTTPS'
ufw allow <HTTPS_PORT>/udp comment 'Caddy HTTP3'
ufw --force enable
```

---

## Шаг 2. Развёртывание проекта

### 2.1. Клонирование репозитория
Рекомендуется устанавливать проект в отдельный рабочий каталог (`/opt/<project_dir>`):
```bash
git clone <REPOSITORY_URL> /opt/<project_dir>
cd /opt/<project_dir>
```

### 2.2. Настройка переменных окружения (`.env`)
Скопируйте шаблон конфигурации:
```bash
cp .env.example .env
chmod 600 .env
```
Откройте файл в редакторе (`nano .env`) и заполните ключевые переменные:

| Переменная | Назначение | Пример / Пояснение |
|---|---|---|
| `APP_ENV` | Окружение | `production` |
| `DOMAIN` | Ваш публичный домен | `<YOUR_DOMAIN>` |
| `VK_BOT_TOKEN` | Токен доступа сообщества ВК | Секретный ключ сообщества (из `.env`) |
| `VK_GROUP_URL` | Ссылка на сообщество ВКонтакте | `<VK_COMMUNITY_URL>` |
| `POSTGRES_USER` | Пользователь базы данных | `<DB_USER>` (уникальное имя, не по умолчанию) |
| `POSTGRES_PASSWORD` | Пароль базы данных | Сгенерируйте надёжный пароль (от 16 символов) |
| `POSTGRES_DB` | Имя базы данных | `<DB_NAME>` |
| `SECRET_KEY` (или `SESSION_SECRET_KEY`) | Секретный ключ подписи сессий | Сгенерируйте: `openssl rand -hex 32` |
| `WEB_ADMIN_USERNAME` | Логин начального суперадмина | Задайте уникальное имя |
| `WEB_ADMIN_PASSWORD` | Пароль начального суперадмина | Минимум 12 символов, без шаблонных слов |
| `TWO_FACTOR_ENABLED` | Двухфакторная авторизация | `true` (рекомендуется) |
| `TG_MONITOR_BOT_TOKEN` (или `TELEGRAM_BOT_TOKEN`) | Токен бота мониторинга | Получите через Bot API платформы |
| `TG_MONITOR_CHAT_ID` (или `TELEGRAM_ADMIN_ID`) | Числовой ID инженера для алертов | Цифровой идентификатор аккаунта |
| `BANNED_CLIENT_IPS` | IP-адреса для блокировки в Caddy WAF (опц.) | Список IP через пробел |
| `TG_MONITOR_PROXY` | Прокси для `tg-monitor` (опционально) | `http://<PROXY_HOST>:<PROXY_PORT>` |

> [!IMPORTANT]
> **Startup Guard**: если `APP_ENV=production`, приложение откажется запускаться с дефолтными паролями (`admin123`, `changeme`, `secret` и т.д.) или паролями короче 12 символов.

---

## Шаг 3. Инициализация и первый запуск

### 3.1. Применение миграций базы данных
Выполните миграции схемы базы данных через изолированный контейнер:
```bash
docker compose run --rm migrate
```
> Ожидаемый результат: контейнер выполнит `alembic upgrade head` и завершится со статусом `Exited (0)`.

### 3.2. Сборка образов и фоновый запуск всех сервисов
```bash
docker compose up -d --build
```

### 3.3. Проверка статуса сервисов
```bash
docker compose ps
```
Все ключевые контейнеры должны иметь статус **`Up (healthy)`**:
- сервис шлюза `caddy` — обратный прокси и TLS-шлюз;
- сервис `web-admin` — веб-панель управления на FastAPI;
- сервис `bot` — бот ВКонтакте;
- сервис `go-api` — высокопроизводительный REST API на Go;
- сервис `db` — СУБД PostgreSQL 16 (изолирована в приватной сети `data_net`);
- сервис `pgbouncer` — пул соединений (мост между `backend_net` и `data_net`);
- сервис `redis` — кэш, распределённые сессии, замки и 2FA (`backend_net`);
- сервис `redis-exporter` — экспортёр метрик Redis для Prometheus (`backend_net`);
- сервис `docker-socket-proxy` — безопасный read-only прокси Docker Socket (`socket_net`);
- сервис `tg-monitor` — бот мониторинга состояния (`backend_net` + `socket_net`);
- сервис `prometheus` — мониторинг метрик и алерты (`backend_net`).

> [!NOTE]
> **Сетевая сегментация (INFRA-001)**: Внешний шлюз Caddy физически не имеет доступа к базам данных (Postgres, Redis) или сокету Docker Engine. Все сервисы разнесены по четырём изолированным подсетям.

Проверьте внутренний статус здоровья:
```bash
curl -s http://<LOCAL_HOST>:<APP_PORT>/health | jq .
```

---

## Шаг 4. Настройка администраторов

### 4.1. Вход в веб-панель
Откройте браузер и перейдите по адресу:
```
https://<YOUR_DOMAIN>/
```
1. Войдите под начальными данными из `.env` (`WEB_ADMIN_USERNAME` и `WEB_ADMIN_PASSWORD`).
2. Вверху экрана появится желтый баннер: *«Активен временный bootstrap-вход. Создайте постоянного суперадминистратора»*.

### 4.2. Создание постоянного суперадмина
Выполните команду в консоли сервера (привяжите ваш VK ID для получения кодов 2FA):
```bash
docker compose exec bot python scripts/create_web_user.py \
  --username <ADMIN_USERNAME> \
  --role SUPERADMIN \
  --vk-id <USER_VK_ID>
```
После создания постоянного пользователя bootstrap-вход из `.env` **автоматически блокируется навсегда**.

### 4.3. Назначение VK-суперадминистратора (для отчётов бота)
```bash
docker compose exec bot python scripts/init_superadmin.py \
  --vk-id <USER_VK_ID> \
  --name "<DISPLAY_NAME>"
```

---

## 🛠️ Шпаргалка команд управления (Docker Compose)

| Действие | Команда |
|---|---|
| Просмотр состояния контейнеров | `docker compose ps` |
| Просмотр логов веб-панели | `docker compose logs -f --tail=100 web-admin` |
| Просмотр логов VK-бота | `docker compose logs -f --tail=100 bot` |
| Просмотр логов Go API | `docker compose logs -f --tail=100 go-api` |
| Просмотр логов сетевого шлюза Caddy | `docker compose logs -f --tail=50 caddy` |
| Просмотр логов мониторинга | `docker compose logs -f --tail=50 tg-monitor` |
| Перезапуск одного сервиса | `docker compose restart web-admin` |
| Полная остановка системы | `docker compose down` |
| Запуск после остановки | `docker compose up -d` |
| Проверка связи с Redis | `docker compose exec redis redis-cli ping` (ответ `PONG`) |
| Подключение к консоли PostgreSQL | `docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"` |

---

## 💾 Резервное копирование и восстановление

### Ручное создание бэкапа
```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```
Скрипт создаёт сжатый дамп `pg_dump`, проверяет его целостность и сохраняет в каталог `${BACKUP_DIR}`. Ротация сохраняет: 7 ежедневных, 4 еженедельных и 3 ежемесячных копии.

### Автоматизация через Cron (ежедневно)
Откройте планировщик `crontab -e` и добавьте строку:
```cron
0 3 * * * /opt/<project_dir>/scripts/backup.sh >> /var/log/backup.log 2>&1
```

### Восстановление из бэкапа
```bash
./scripts/restore.sh <BACKUP_DIR>/<BACKUP_FILE>.sql.gz
docker compose start bot web-admin go-api
```

---

## 🔄 Обновление проекта до новой версии

При выходе обновления выполните следующие шаги:
```bash
cd /opt/<project_dir>

# 1. Загрузка свежего кода из репозитория
git pull origin master

# 2. Применение новых миграций БД
docker compose run --rm migrate

# 3. Пересборка и запуск обновлённых контейнеров
docker compose up -d --build

# 4. Проверка статуса
docker compose ps
curl -s http://<LOCAL_HOST>:<APP_PORT>/health
```

---

## ❓ Часто встречающиеся вопросы и решение проблем (Troubleshooting)

### 1. Caddy не может получить SSL-сертификат
- **Причина**: Домен не направлен на адрес сервера или входящий HTTP/HTTPS-трафик заблокирован фаерволом.
- **Решение**: Проверьте `nslookup <YOUR_DOMAIN>` и убедитесь, что внешний трафик не заблокирован. Проверьте логи: `docker compose logs caddy`.

### 2. Ошибка "Too many requests" при входе в панель
- **Причина**: Сработал `DBRateLimiter` после превышения лимита неверных попыток входа.
- **Решение**: Подождите 15 минут или очистите историю попыток в базе:
  ```bash
  docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "DELETE FROM login_attempts;"
  ```

### 3. Не приходит код 2FA в личные сообщения ВК
- **Причина**: Вы не разрешили сообществу отправлять вам сообщения или диалог с группой ещё не открыт.
- **Решение**: Откройте диалог с сообществом ВКонтакте с вашего аккаунта и отправьте любое текстовое сообщение (например, «Привет»). После этого нажмите в веб-панели кнопку «Отправить код повторно».

### 4. Контейнер bot переходит в статус unhealthy
- **Причина**: Проблемы со связью с VK API или невалидный токен в `.env`.
- **Решение**: Посмотрите последние сообщения об ошибках:
  ```bash
  docker compose logs --tail=100 bot
  ```

---

## 🚀 Автоматический CI/CD (GitHub Actions)

В проекте настроен пайплайн автоматического деплоя `.github/workflows/deploy.yml` при каждом push в ветку `master`:

1. **Подключение к прод-серверу** по SSH (`appleboy/ssh-action`).
2. **Синхронизация кода**: `git fetch origin master && git reset --hard origin/master`.
3. **Обновление контейнеров и запуск миграций**:
   ```bash
   docker compose build
   docker compose run --rm migrate
   docker compose up -d --remove-orphans
   ```
4. **Проверка работоспособности**: автоматический healthcheck `/health`.

### Настройка GitHub Secrets в репозитории

В настройках GitHub-репозитория (**Settings -> Secrets and variables -> Actions**) добавьте следующие переменные:

| Secret | Описание | Пример значения |
|---|---|---|
| `PROD_HOST` | Адрес или домен сервера | `<SERVER_HOST>` |
| `PROD_USER` | Пользователь SSH | `<SSH_USER>` |
| `PROD_PASSWORD` | Пароль SSH (при парольном доступе) | `<SSH_PASSWORD>` |
| `PROD_SSH_KEY` | Приватный ключ SSH (при ключевом доступе) | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `PROD_PORT` | SSH порт | `<SSH_PORT>` |
| `PROD_DOMAIN` | Домен для проверки внешнего SSL/Healthcheck | `<YOUR_DOMAIN>` |
