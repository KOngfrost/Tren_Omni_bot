# 🚀 Руководство по установке и эксплуатации OSS Bot (v0.8.8.4)

> **Для кого этот документ:** Системные администраторы, DevOps-инженеры и разработчики.  
> **Цель:** Развернуть проект с нуля на чистом сервере Linux за 10–15 минут и обеспечить надёжную эксплуатацию.

---

## 📋 Чек-лист перед установкой

Перед началом убедитесь, что у вас есть:
1. Выделенный сервер или VDS с операционной системой **Ubuntu 22.04 / 24.04 LTS** (или Debian 12).
2. Минимальные ресурсы: **2 vCPU**, **4 GB RAM**, **20 GB SSD** (для нагрузки > 2500 пользователей рекомендуется 4 vCPU и 8 GB RAM).
3. Доменное имя (например, бесплатный поддомен на DuckDNS: `your-subdomain.duckdns.org`), направленное на публичный IP-адрес сервера (A-запись).
4. Токен сообщества ВКонтакте с включёнными сообщениями сообщества и LongPoll API.

---

## Шаг 1. Подготовка сервера

Подключитесь к серверу по SSH:
```bash
ssh root@<IP_АДРЕС_СЕРВЕРА>
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
Наружу должны быть открыты только SSH и шлюз Caddy (порты 80 и 443). Все внутренние порты (PostgreSQL, Redis, PgBouncer, Uvicorn) изолированы внутри сети Docker:
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'Caddy HTTP'
ufw allow 443/tcp comment 'Caddy HTTPS'
ufw allow 443/udp comment 'Caddy HTTP3'
ufw --force enable
```

---

## Шаг 2. Развёртывание проекта

### 2.1. Клонирование репозитория
Рекомендуется устанавливать проект в каталог `/opt/oss_bot`:
```bash
git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
cd /opt/oss_bot
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
| `DOMAIN` | Ваш публичный домен | `your-domain.duckdns.org` |
| `VK_BOT_TOKEN` | Токен доступа сообщества ВК | Строка вида `vk1.a.XXXX...` |
| `VK_GROUP_ID` | ID сообщества ВКонтакте | Цифровой ID группы |
| `POSTGRES_PASSWORD` | Пароль базы данных | Сгенерируйте надёжный пароль (от 16 символов) |
| `SECRET_KEY` | Секретный ключ подписи сессий | Сгенерируйте командой: `openssl rand -hex 32` |
| `WEB_ADMIN_PASSWORD` | Пароль начального суперадмина | Минимум 12 символов, без шаблонных слов |
| `TWO_FACTOR_ENABLED` | Двухфакторная авторизация | `true` (рекомендуется) |
| `TG_MONITOR_BOT_TOKEN` | Токен Telegram-бота инженера | Получите у `@BotFather` |
| `TG_MONITOR_CHAT_ID` | Telegram ID инженера для алертов | Ваш цифровой ID Telegram |

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
- `oss_bot_caddy` — обратный прокси и TLS-шлюз;
- `oss_bot_web` — веб-панель управления на FastAPI;
- `oss_bot_app` — бот ВКонтакте;
- `oss_bot_go_api` — высоконагруженный API на Go;
- `oss_bot_db` — СУБД PostgreSQL 16;
- `oss_bot_pgbouncer` — пул соединений (порт 6432);
- `oss_bot_redis` — кэш, сессии и хранилище 2FA;
- `oss_bot_tg_monitor` — бот мониторинга состояния.

Проверьте внутренний статус здоровья:
```bash
curl -s http://127.0.0.1:8000/health | jq .
```

---

## Шаг 4. Настройка администраторов

### 4.1. Вход в веб-панель
Откройте браузер и перейдите по адресу:
```
https://<ваш_домен>/
```
1. Войдите под начальными данными из `.env` (`WEB_ADMIN_USERNAME` и `WEB_ADMIN_PASSWORD`).
2. Вверху экрана появится желтый баннер: *«Активен временный bootstrap-вход. Создайте постоянного суперадминистратора»*.

### 4.2. Создание постоянного суперадмина
Выполните команду в консоли сервера (привяжите ваш VK ID для получения кодов 2FA):
```bash
docker compose exec bot python scripts/create_web_user.py \
  --username admin \
  --role SUPERADMIN \
  --vk-id <ВАШ_VK_ID>
```
После создания постоянного пользователя bootstrap-вход из `.env` **автоматически блокируется навсегда**.

### 4.3. Назначение VK-суперадминистратора (для отчётов бота)
```bash
docker compose exec bot python scripts/init_superadmin.py \
  --vk-id <ВАШ_VK_ID> \
  --name "Главный Администратор"
```

---

## 🛠️ Шпаргалка команд управления (Docker Compose)

| Действие | Команда |
|---|---|
| Просмотр состояния контейнеров | `docker compose ps` |
| Просмотр логов веб-панели | `docker compose logs -f --tail=100 web-admin` |
| Просмотр логов VK-бота | `docker compose logs -f --tail=100 bot` |
| Просмотр логов сетевого шлюза Caddy | `docker compose logs -f --tail=50 caddy` |
| Перезапуск одного сервиса | `docker compose restart web-admin` |
| Полная остановка системы | `docker compose down` |
| Запуск после остановки | `docker compose up -d` |
| Проверка связи с Redis | `docker compose exec redis redis-cli ping` (ответ `PONG`) |
| Подключение к консоли PostgreSQL | `docker compose exec db psql -U student_user -d student_db` |

---

## 💾 Резервное копирование и восстановление

### Ручное создание бэкапа
```bash
chmod +x scripts/backup.sh
./scripts/backup.sh
```
Скрипт создаёт сжатый дамп `pg_dump | gzip`, проверяет его целостность и сохраняет в `/var/backups/oss_bot/`. Ротация сохраняет: 7 ежедневных, 4 еженедельных и 3 ежемесячных копии.

### Автоматизация через Cron (ежедневно в 03:00)
Откройте планировщик `crontab -e` и добавьте строку:
```cron
0 3 * * * /opt/oss_bot/scripts/backup.sh >> /var/log/oss_bot_backup.log 2>&1
```

### Восстановление из бэкапа
```bash
./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-09-26.sql.gz
docker compose restart bot web-admin
```

---

## 🔄 Обновление проекта до новой версии

При выходе обновления выполните следующие шаги:
```bash
cd /opt/oss_bot

# 1. Загрузка свежего кода из репозитория
git pull origin master

# 2. Применение новых миграций БД
docker compose run --rm migrate

# 3. Пересборка и запуск обновлённых контейнеров
docker compose up -d --build

# 4. Проверка статуса
docker compose ps
curl -s http://127.0.0.1:8000/health
```

---

## ❓ Часто встречающиеся вопросы и решение проблем (Troubleshooting)

### 1. Caddy не может получить SSL-сертификат
- **Причина**: Домен не направлен на IP сервера или порт 80/443 закрыт у хостинг-провайдера.
- **Решение**: Проверьте `nslookup <ваш_домен>` — IP должен совпадать с сервером. Убедитесь, что в панели провайдера не включен внешний файрвол на порты 80/443. Проверьте логи: `docker compose logs caddy`.

### 2. Ошибка "Too many requests" при входе в панель
- **Причина**: Сработал `DBRateLimiter` после 5 неверных попыток ввода пароля с вашего IP.
- **Решение**: Подождите 15 минут или очистите историю попыток в базе:
  ```bash
  docker compose exec db psql -U student_user -d student_db -c "DELETE FROM login_attempts;"
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

