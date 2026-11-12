# 🛡️ Эксплуатация, регламент резервного копирования и аварийное восстановление (v1.0.0)

> **Версия документа:** 1.0.0  
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
| **Полный дамп PostgreSQL** | Ежедневно (по расписанию) | `scripts/backup.sh` (`pg_dump`) | 14 дней | Локально `${BACKUP_DIR}` + Удалённое хранилище |
| **Еженедельный архив** | Каждое воскресенье | `scripts/backup.sh` | 8 недель | Защищённое холодное хранилище |
| **Снапшот Redis (RDB + AOF)** | Каждый час | Redis `BGSAVE` + AOF (everysec) | 48 часов | Каталог `redis_data` на NVMe |
| **Файлы конфигурации и сертификаты** | При каждом изменении / раз в сутки | `tar` архивация `/opt/<project_dir>/.env` и `caddy_data` | 30 дней | Зашифрованный архив |

### 🛠️ Автоматизация через Crontab:
```bash
# Добавить в crontab (crontab -e):
0 3 * * * /opt/<project_dir>/scripts/backup.sh >> /var/log/backup.log 2>&1
```

Ручной запуск бэкапа:
```bash
cd /opt/<project_dir>
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
   docker compose logs --tail=100 web-admin
   ```
3. Перезапустите проблемный сервис:
   ```bash
   docker compose restart web-admin bot
   ```

### Сценарий 2: Повреждение базы данных PostgreSQL
1. Остановите сервисы, производящие запись, для предотвращения дальнейших повреждений:
   ```bash
   docker compose stop bot web-admin go-api
   ```
2. Запустите скрипт восстановления из последней резервной копии:
   ```bash
   bash scripts/restore.sh <BACKUP_DIR>/<BACKUP_FILE>.sql.gz
   ```
3. Проверьте целостность таблиц и миграций:
   ```bash
   docker compose run --rm migrate alembic current
   ```
4. Запустите стек сервисов:
   ```bash
   docker compose start bot web-admin go-api
   ```

### Сценарий 3: Полная гибель хоста / перенос на чистый сервер
1. Разверните чистый сервер с Ubuntu 22.04 / 24.04 LTS.
2. Установите Docker и Docker Compose (см. [DEPLOYMENT.md](DEPLOYMENT.md)).
3. Клонируйте репозиторий:
   ```bash
   git clone <REPOSITORY_URL> /opt/<project_dir>
   cd /opt/<project_dir>
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
3. **Сервис мониторинга (`tg-monitor`)**:
   - Автоматически отслеживает падения контейнеров.
   - Мгновенно отправляет оповещения дежурному инженеру при возникновении критических событий.
   - Позволяет включать режим технического обслуживания в один клик.

### Критические алерты (Alerting Rules):
- **ServiceDown**: Недоступность контейнера `web-admin`, `bot` или `db` более 60 секунд.
- **High5xxErrorRate**: Доля ошибок 5xx превышает 1% за 5 минут.
- **HighLatency**: Время ответа p95 превышает 1 секунду.
- **OutboxQueueLag**: В очереди Transactional Outbox скопилось более 50 неотправленных сообщений (признак недоступности VK/Telegram API).
- **DiskSpaceLow**: Остаток свободного места на диске менее 15%.

---

## 4. Регламент обновления версий и миграции данных

### Стандартная процедура обновления (Zero-Downtime Rolling Update):

1. **Создайте контрольную резервную копию**:
   ```bash
   cd /opt/<project_dir>
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
   docker compose up -d --build --no-deps web-admin
   docker compose up -d --build --no-deps bot
   docker compose up -d --build --no-deps go-api
   docker compose up -d --build --no-deps tg-monitor
   ```

5. **Проверьте работоспособность**:
   ```bash
   docker compose ps
   curl -f http://<LOCAL_HOST>:<APP_PORT>/health
   ```

### Процедура отката (Rollback Procedure):
В случае обнаружения критической регрессии в новой версии:
1. Верните Git-репозиторий к предыдущему релизному тегу/коммиту:
   ```bash
   git checkout <PREVIOUS_TAG>
   ```
2. Откатите миграции схемы БД (при необходимости):
   ```bash
   docker compose run --rm migrate alembic downgrade -1
   ```
3. Пересоберите и перезапустите контейнеры:
   ```bash
   docker compose up -d --build
   ```


