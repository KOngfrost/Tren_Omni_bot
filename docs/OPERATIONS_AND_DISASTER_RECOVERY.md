# 🛡️ Эксплуатация, регламент резервного копирования и аварийное восстановление (v0.8.8.1)

> **Версия документа:** 0.8.8.1  
> **Целевая аудитория:** Системные администраторы, инженеры эксплуатации (SRE), DevOps.

---

## 🧭 Содержание
1. [Стратегия и регламент резервного копирования (Backup Policy)](#1-стратегия-и-регламент-резервного-копирования-backup-policy)
2. [Аварийное восстановление после сбоев (Disaster Recovery Runbook)](#2-аварийное-восстановление-после-сбоев-disaster-recovery-runbook)
3. [Система мониторинга и оповещений (Alerting & Monitoring)](#3-система-мониторинга-и-оповещений-alerting--monitoring)
4. [Регламент обновления версий и миграции данных](#4-регламент-обновления-версий-и-миграции-данных)

---

## 1. Стратегия и регламент резервного копирования (Backup Policy)

### 🎯 Целевые показатели надёжности:
- **RPO (Recovery Point Objective)**: не более 1 часа (максимально допустимая потеря данных при глобальной аварии).
- **RTO (Recovery Time Objective)**: не более 15 минут (время полного восстановления работоспособности сервиса).

### 🗄️ Регулярность создания резервных копий:
| Тип бэкапа | Периодичность | Инструмент | Срок хранения (Retention) | Место хранения |
|---|---|---|---|---|
| **Полный дамп PostgreSQL** | Ежедневно в 03:00 UTC | `scripts/backup.sh` (`pg_dump -Fc`) | 14 дней | Локально `/var/backups/oss_bot` + Удалённое S3-хранилище |
| **Еженедельный архив** | Каждое воскресенье | `scripts/backup.sh` | 8 недель | Защищённое холодное хранилище |
| **Снапшот Redis (RDB + AOF)** | Каждый час | Redis `BGSAVE` + AOF (everysec) | 48 часов | Каталог `redis_data` на NVMe |
| **Файлы конфигурации и сертификаты** | При каждом изменении / раз в сутки | `tar` архивация `/opt/oss_bot/.env` и `caddy_data` | 30 дней | Зашифрованный архив |

### 🛠️ Автоматизация через Crontab:
```bash
# Добавить в crontab суперпользователя на сервере (crontab -e):
0 3 * * * /opt/oss_bot/scripts/backup.sh >> /var/log/oss_bot_backup.log 2>&1
```

Ручной запуск бэкапа:
```bash
cd /opt/oss_bot
bash scripts/backup.sh
```

---

## 2. Аварийное восстановление после сбоев (Disaster Recovery Runbook)

### Сценарий 1: Сбой контейнеров приложения (Бот / Веб-панель)
1. Проверьте статус контейнеров:
   ```bash
   docker compose ps
   ```
2. Посмотрите последние логи упавшего сервиса:
   ```bash
   docker compose logs --tail=100 bot
   docker compose logs --tail=100 web
   ```
3. Перезапустите проблемный сервис:
   ```bash
   docker compose restart web bot
   ```

### Сценарий 2: Повреждение базы данных PostgreSQL
1. Остановите сервисы, производящие запись, для предотвращения дальнейших повреждений:
   ```bash
   docker compose stop bot web backend_go
   ```
2. Запустите скрипт восстановления из последней резервной копии:
   ```bash
   bash scripts/restore.sh /var/backups/oss_bot/oss_bot_backup_YYYYMMDD_HHMMSS.sql.gz
   ```
3. Проверьте целостность таблиц и миграций:
   ```bash
   docker compose run --rm migrate alembic current
   ```
4. Запустите стек сервисов:
   ```bash
   docker compose start bot web backend_go
   ```

### Сценарий 3: Полная гибель хоста / перенос на чистый сервер
1. Разверните чистый сервер с Ubuntu 22.04 / 24.04 LTS.
2. Установите Docker и Docker Compose (см. [DEPLOYMENT.md](DEPLOYMENT.md)).
3. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/KOngfrost/Student_bot.git /opt/oss_bot
   cd /opt/oss_bot
   ```
4. Восстановите `.env` из резервной копии.
5. Запустите контейнеры СУБД: `docker compose up -d db redis pgbouncer`.
6. Выполните восстановление дампа через `scripts/restore.sh`.
7. Запустите приложение: `docker compose up -d --build`.

---

## 3. Система мониторинга и оповещений (Alerting & Monitoring)

Система мониторинга состоит из трёх уровней:
1. **Prometheus**: сбор системных метрик (`/metrics`) с интервалом скрейпа 15 секунд.
2. **Docker Healthchecks**: непрерывный опрос состояния здоровья контейнеров (`scripts/healthcheck_bot.py`, `curl /health`).
3. **Telegram Monitor Bot (`oss_bot_tg_monitor`)**:
   - Автоматически отслеживает падения контейнеров.
   - Мгновенно отправляет оповещения дежурным администраторам в Telegram при возникновении критических событий.
   - Позволяет администраторам включать режим технического обслуживания в один клик.

### Критические алерты (Alerting Rules):
- **ServiceDown**: Недоступность контейнера `web`, `bot` или `db` более 60 секунд.
- **High5xxErrorRate**: Доля ошибок 5xx превышает 1% за 5 минут.
- **HighLatency**: Время ответа p95 превышает 1 секунду.
- **OutboxQueueLag**: В очереди Transactional Outbox скопилось более 50 неотправленных сообщений (признак недоступности VK/Telegram API).
- **DiskSpaceLow**: Остаток свободного места на диске менее 15%.

---

## 4. Регламент обновления версий и миграции данных

### Стандартная процедура обновления (Zero-Downtime Rolling Update):

1. **Создайте контрольную резервную копию**:
   ```bash
   cd /opt/oss_bot
   bash scripts/backup.sh
   ```

2. **Загрузите обновления кодовой базы**:
   ```bash
   git fetch origin
   git pull origin master
   ```

3. **Примените миграции базы данных**:
   ```bash
   docker compose run --rm migrate
   ```

4. **Выполните поочерёдный перезапуск сервисов**:
   ```bash
   # Сборка и перезапуск без разрыва соединений
   docker compose up -d --build --no-deps web
   docker compose up -d --build --no-deps bot
   docker compose up -d --build --no-deps backend_go
   docker compose up -d --build --no-deps tg-monitor
   ```

5. **Проверьте работоспособность**:
   ```bash
   docker compose ps
   curl -f http://127.0.0.1:8000/health
   ```

### Процедура отката (Rollback Procedure):
В случае обнаружения критической регрессии в новой версии:
1. Верните Git-репозиторий к предыдущему релизному тегу/коммиту:
   ```bash
   git checkout v0.8.7
   ```
2. Откатите миграции схемы БД (при необходимости):
   ```bash
   docker compose run --rm migrate alembic downgrade -1
   ```
3. Пересоберите и перезапустите контейнеры:
   ```bash
   docker compose up -d --build
   ```
