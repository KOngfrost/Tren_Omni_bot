# Руководство по развёртыванию и настройке OSS Bot с нуля

> **Версия системы:** 0.8.4.1  
> **Целевая аудитория:** Системные администраторы, DevOps-инженеры и технические специалисты.

---

## 1. Системные требования к серверу

* **Операционная система:** Ubuntu 22.04 / 24.04 LTS или Debian 12 (x86_64).
* **Процессор (CPU):** от 2 ядер (рекомендуется 4 ядра для пиковых нагрузок во время сессии).
* **Оперативная память (RAM):** от 2 ГБ (рекомендуется 4 ГБ).
* **Дисковое пространство:** от 25 ГБ NVMe/SSD.
* **Сетевые порты:**
  * `80/tcp` и `443/tcp, 443/udp` (HTTP/HTTPS для шлюза Caddy).
  * `22/tcp` (SSH).
  * Все остальные порты (база данных 5432, Redis 6379, PgBouncer 6432, Web 8000) закрыты от внешнего мира внутри виртуальной сети Docker и защищены NetBird VPN.

---

## 2. Подготовка операционной системы

Выполните базовую настройку и установите Docker:

```bash
# 1. Обновление пакетов
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl git ufw htop jq

# 2. Установка официального Docker и Docker Compose V2
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# 3. Настройка базового файрвола UFW
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'Caddy HTTP'
sudo ufw allow 443/tcp comment 'Caddy HTTPS'
sudo ufw allow 443/udp comment 'Caddy HTTP3'
sudo ufw --force enable
```

---

## 3. Клонирование проекта

```bash
sudo mkdir -p /opt/oss_bot
sudo chown -R $USER:$USER /opt/oss_bot
git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
cd /opt/oss_bot
```

---

## 4. Конфигурация переменных окружения (.env)

Скопируйте шаблон конфигурации:
```bash
cp .env.example .env
chmod 600 .env
```

Сгенерируйте надёжные случайные ключи и заполните `.env`:

```bash
# Генерация секретов одной командой:
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24)); print('REDIS_PASSWORD=' + secrets.token_urlsafe(24)); print('SESSION_SECRET_KEY=' + secrets.token_urlsafe(64)); print('CSRF_SECRET_KEY=' + secrets.token_urlsafe(32))"
```

### Обязательные параметры для заполнения в `.env`:
1. **APP_VERSION=0.8.4.1**
2. **База данных:**
   * `POSTGRES_USER=oss_bot`
   * `POSTGRES_PASSWORD=<сгенерированный_стойкий_пароль>`
   * `POSTGRES_DB=oss_bot`
   * `DB_HOST=pgbouncer`
   * `DB_PORT=6432`
   * `DB_USE_PGBOUNCER=true`
3. **VK Бот:**
   * `VK_BOT_TOKEN=<токен_сообщества_из_настроек_API_VK>`
   * `ADMIN_VK_IDS=<ваш_числовой_VK_ID>`
   * `VK_REPORT_ADMIN_ID=<ваш_числовой_VK_ID>`
4. **Веб-панель и безопасность:**
   * `SESSION_SECRET_KEY=<сгенерированный_ключ_64_символа>`
   * `CSRF_SECRET_KEY=<сгенерированный_ключ_32_символа>`
5. **Telegram Бот мониторинга:**
   * `TELEGRAM_BOT_TOKEN=<токен_от_BotFather>`
   * `TELEGRAM_ADMIN_CHAT_ID=<ваш_числовой_Telegram_ID>`
   * `TELEGRAM_WEBAPP_URL=https://example-bot.duckdns.org`
6. **NetBird VPN (Закрытый контур):**
   * `NETBIRD_SETUP_KEY=<ключ_настройки_из_панели_NetBird>`
   * `NETBIRD_MANAGEMENT_URL=https://api.netbird.io:443`

---

## 5. Запуск платформы

```bash
# 1. Параллельная сборка образов
docker compose build --parallel

# 2. Запуск фундаментальных хранилищ (БД, PgBouncer, Redis)
docker compose up -d db pgbouncer redis

# 3. Применение миграций схемы данных Alembic
docker compose run --rm migrate

# 4. Запуск всех остальных сервисов
docker compose up -d --remove-orphans
```

---

## 6. Создание первой учётной записи администратора

Создайте постоянного суперпользователя веб-панели через CLI:

```bash
docker compose exec web-admin python scripts/create_web_user.py \
  --username admin \
  --password "ВашНадежныйПарольБолее10Символов" \
  --role SUPERADMIN
```

После создания учётной записи отключите режим начальной загрузки в `.env`:
```bash
sed -i 's/^BOOTSTRAP_ALLOWED=.*/BOOTSTRAP_ALLOWED=false/' .env
docker compose up -d web-admin
```

---

## 7. Проверка работоспособности (Health Check)

Убедитесь, что все контейнеры работают и имеют статус `(healthy)`:
```bash
docker compose ps
```

Ожидаемый вывод:
```
NAME                 SERVICE      STATUS
oss_bot_app          bot          Up (healthy)
oss_bot_caddy        caddy        Up
oss_bot_db           db           Up (healthy)
oss_bot_netbird      netbird      Up (healthy)
oss_bot_pgbouncer    pgbouncer    Up (healthy)
oss_bot_prometheus   prometheus   Up (healthy)
oss_bot_redis        redis        Up (healthy)
oss_bot_tg_monitor   tg-monitor   Up (healthy)
oss_bot_web          web-admin    Up (healthy)
```

Проверка доступности веб-сервера изнутри:
```bash
docker compose exec web-admin curl -s http://127.0.0.1:8000/health | jq
```

---

## 8. Автоматизация резервного копирования

Настройте ежедневное автоматическое резервное копирование базы данных в cron:
```bash
sudo mkdir -p /var/backups/oss_bot
sudo crontab -e
```
Добавьте строку (ежедневно в 03:00 ночи):
```cron
0 3 * * * /opt/oss_bot/scripts/backup.sh > /var/log/oss_bot_backup.log 2>&1
```
