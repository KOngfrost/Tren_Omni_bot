# Руководство по масштабированию, высокой доступности (HA) и мониторингу

Настоящее руководство описывает архитектурные решения, пулинг соединений, переключение на VK Callback API, репликацию баз данных, мониторинг и нагрузочное тестирование в проекте **student_bot**.

---

## 1. Пулер соединений: PgBouncer

### Зачем нужен PgBouncer:
В многоворкерном режиме (Uvicorn с 4+ воркерами + фоновый воркер бота + Outbox-доставка) приложение создает множество параллельных пулов соединений (`asyncpg`). Без пулера это приводит к быстрому исчерпанию лимита соединений PostgreSQL (`max_connections`) и высокому потреблению оперативной памяти.

PgBouncer включен в `docker-compose.yml` в режиме **Transaction Pooling** (`pool_mode = transaction`):
- Слушает на порту: `6432`.
- Входящие соединения от приложения транслируются в небольшой пул постоянных соединений к PostgreSQL.
- Для совместимости `asyncpg` с `pool_mode=transaction` в `core/database.py` автоматически отключается кэширование подготовленных выражений (`statement_cache_size = 0`).

### Настройки в `.env`:
```env
# Использовать PgBouncer (по умолчанию порт 6432)
DB_USE_PGBOUNCER=true
DB_PORT=6432
DB_HOST=pgbouncer
```

---

## 2. VK Callback API (Webhooks) против Long Poll

### Сравнение режимов:
- **Long Poll (`VK_MODE=longpoll`)**:
  - Используется по умолчанию.
  - Подходит для локальной разработки и закрытых контуров (Tailscale VPN без публичного IP).
  - Бот сам держит исходящее соединение к серверам VK.
- **Callback API (`VK_MODE=callback`)**:
  - Рекомендуется для production с высокой нагрузкой.
  - Серверы VK присылают входящие push-уведомления (HTTP POST) прямо на эндпоинт `/api/v1/vk/callback` (или `/webhooks/vk`).
  - Обеспечивает масштабирование на любое число HTTP-воркеров и мгновенную доставку.

### Настройка в сообществе ВКонтакте:
1. Зайдите в **Управление сообществом** > **Работа с API** > **Callback API**.
2. В поле URL сервера укажите: `https://<ваш_домен_или_туннель>/api/v1/vk/callback`.
3. Скопируйте строку подтверждения и укажите её в `.env`:
   ```env
   VK_MODE=callback
   VK_CONFIRMATION_TOKEN=d789abc1
   VK_CALLBACK_SECRET=super_secret_vk_salt_42
   ```
4. В поле "Секретный ключ" в VK укажите то же значение, что и в `VK_CALLBACK_SECRET`.
5. Во вкладке **Типы событий** включите: "Входящие сообщения", "Редактирование сообщений".

---

## 3. Нагрузочное тестирование (Locust)

Для проверки устойчивости API, скорости ответа и стабильности пула соединений подготовлен тестовый набор Locust.

### Установка:
```bash
pip install -r requirements-dev.txt
```

### Запуск в headless-режиме (нагрузка 50 пользователей, 10 пользователей в секунду):
```bash
locust -f tests/load/locustfile.py --headless -u 50 -r 10 -t 1m --host http://localhost:8000
```

### Интерактивный запуск через Web UI:
```bash
locust -f tests/load/locustfile.py --host http://localhost:8000
```
После запуска откройте в браузере `http://localhost:8089` и задайте желаемое количество пользователей (Concurrent Users) и скорость прироста (Ramp-up).

---

## 4. Мониторинг и алертинг (Prometheus / Sentry)

### Метрики:
Веб-панель автоматически экспортирует метрики в формате Prometheus по адресу `GET /metrics`.

### Правила алертов (`monitoring/prometheus/alerts.yml`):
1. **`FastApiInstanceDown`**: веб-панель не отвечает более 1 минуты.
2. **`HighHttpErrorRate`**: процент ответов 5xx превышает 2% за 5 минут.
3. **`HighHttpLatency`**: 95-й перцентиль задержки (p95) превышает 1.0 сек.
4. **`BotHeartbeatMissing`**: бот не обновлял heartbeat более 3 минут (сигнал зависания или обрыва связи с VK).
5. **`OutboxQueueBacklog`**: в очереди Outbox скопилось более 50 неотправленных уведомлений.
6. **`RedisMemoryHigh`**: использование памяти Redis превысило 85%.

---

## 5. Устранение Single Point of Failure (SPOF)

### 1. Резервное копирование PostgreSQL:
В проекте реализован скрипт `scripts/backup.sh`:
- Автоматически создает сжатый дамп `oss_bot_YYYY-MM-DD.sql.gz`.
- Поддерживает ротацию (7 дневных, 4 недельных, 3 месячных копии).
- Поддерживает выгрузку в удаленное S3/B2 хранилище через `rclone`.

### 2. Потоковая репликация PostgreSQL (Primary -> Standby):
Для настройки горячего резерва (Hot Standby) на основном сервере:
- `wal_level = replica`
- `max_wal_senders = 5`
- Создание пользователя репликации:
  ```sql
  CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD 'replicator_password';
  ```
- Резервный сервер инициализируется через `pg_basebackup`:
  ```bash
  pg_basebackup -h pg-primary -D /var/lib/postgresql/data -U replicator -Fp -Xs -R
  ```

### 3. Отказоустойчивость Redis (Redis Sentinel):
В высоконагруженной среде рекомендуется развертывание 3 узлов Redis Sentinel, которые автоматически отслеживают состояние мастера и в случае сбоя за доли секунды назначают реплику новым мастером.
Клиент `core/redis_client.py` подготовлен для работы с адресами Sentinel.
