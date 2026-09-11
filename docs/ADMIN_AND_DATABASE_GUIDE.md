# Управление базой данных, администраторами и безопасностью (v0.7.8)

В руководстве описана архитектура данных, управление учётными записями администраторов, взаимодействие с PostgreSQL и Redis, а также процедуры обслуживания.

---

## 1. Доступ к панели управления

Панель открывается только внутри защищённой сети Tailscale:
```
https://oss-web-panel.<tailnet>.ts.net/
```
Вход осуществляется по учётным данным из таблицы `web_users`. На чистой установке действует bootstrap-вход из `.env` (`WEB_ADMIN_USERNAME`/`WEB_ADMIN_PASSWORD`), который автоматически блокируется после создания первого постоянного суперадмина.

---

## 2. Разделение типов администраторов

В системе разделены два типа администраторов:
1. **Пользователи веб-панели (`web_users`)** — имеют логин и пароль для входа в панель управления через браузер.
2. **VK-администраторы (`admins`)** — пользователи ВКонтакте (`users.vk_id`), имеющие доступ к служебным командам в диалоге с ботом (просмотр очереди, статистика, рассылка).

### Роли пользователей веб-панели (`WebRole`)
- **`SUPERADMIN`**: полный доступ к заявкам всех отделов, анонимным обращениям, управлению отделами, созданию и удалению пользователей панели, полному журналу аудита и API.
- **`DEPARTMENT_ADMIN`**: доступ строго к заявкам, FAQ, базе знаний и событиям своего назначенного отдела.

---

## 3. Управление пользователями веб-панели (`web_users`)

### 3.1. Создание через консоль сервера
```bash
# Создание суперадминистратора (с привязкой к VK ID для 2FA)
docker compose exec bot python scripts/create_web_user.py --username admin --role SUPERADMIN --vk-id 123456789

# Создание администратора отдела
docker compose exec bot python scripts/create_web_user.py --username zhilbyt --role DEPARTMENT_ADMIN --department "Жилбыт" --vk-id 987654321
```

### 3.2. Создание и удаление через веб-интерфейс
Суперадминистратор может управлять учётными записями в разделе **/admin/admins/**:
- **Добавление**: форма создания нового пользователя с автоматической проверкой уникальности логина и валидацией сложности пароля.
- **Привязка VK ID**: необходима, если в системе включена двухфакторная аутентификация (`TWO_FACTOR_ENABLED=true`). При входе на указанный VK ID бот отправит одноразовый 6-значный код.
- **Удаление**: кнопка удаления напротив пользователя (`POST /admin/web-users/{id}/delete`) с защитой от удаления собственного аккаунта.

---

## 4. Управление VK-администраторами (`admins`)

VK-суперадминистраторы получают ежедневные сводные отчёты бота и могут выполнять текстовые команды управления в личных сообщениях.

```bash
# Назначение VK-суперадмина
docker compose exec bot python scripts/init_superadmin.py --vk-id 123456789 --name "Иван Иванов"
```

Удаление VK-администратора выполняется через интерфейс страницы администраторов или удалением записи из таблицы `admins`. Связанный профиль в `users` и история обращений студента сохраняются. Запрещено удалять последнюю оставшуюся запись суперадминистратора.

---

## 5. Схема базы данных (PostgreSQL + SQLAlchemy 2.0)

Схема построена на SQLAlchemy 2.0 (`DeclarativeBase`, `mapped_column`, каскадное удаление):

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `users` | Профили студентов ВКонтакте | `id`, `vk_id`, `full_name`, `dormitory`, `created_at` |
| `departments` | Отделы обработки обращений | `id`, `name`, `head_admin_id`, `created_at` |
| `department_keywords` | Ключевые слова отделов для авто-маршрутизации (без N+1) | `id`, `department_id`, `keyword` |
| `admins` | Роли администраторов в боте ВК | `id`, `user_id`, `department_id`, `role` |
| `web_users` | Учётные записи входа в веб-панель | `id`, `username`, `password_hash`, `role`, `department_id`, `is_active` |
| `tickets` | Заявки студентов | `id`, `user_id`, `department_id`, `topic`, `description`, `status`, `is_anonymous` |
| `ticket_messages` | История переписки по обращению | `id`, `ticket_id`, `sender_type`, `text`, `created_at` |
| `vk_outbox` | Очередь гарантированной доставки в VK | `id`, `user_id`, `message_text`, `status`, `retry_count`, `scheduled_at` |
| `report_runs` | Фиксация времени ежедневных отчётов | `id`, `report_date`, `status`, `sent_at` |
| `faq_nodes` | Интерактивное дерево FAQ | `id`, `title`, `content`, `parent_id`, `department_id` |
| `knowledge_base` | Статьи автоответов базы знаний | `id`, `title`, `content`, `keywords`, `department_id` |
| `events` | Анонсы событий и мероприятий | `id`, `title`, `description`, `event_date`, `department_id` |
| `logs` | Журнал аудита действий | `id`, `admin_id`, `action`, `details`, `ip_address`, `created_at` |
| `login_attempts` | Фиксация неудачных попыток входа | `id`, `ip_address`, `attempt_time` |
| `crud_attempts` | Ограничение частоты изменений (DBRateLimiter) | `id`, `ip_address`, `action`, `attempted_at` |

### Миграции Alembic
Любые изменения схемы выполняются исключительно через миграции Alembic:
```bash
# Применение миграций вручную (в контейнере)
docker compose exec bot python -m alembic upgrade head

# Создание новой ревизии миграции
docker compose exec bot python -m alembic revision -m "add_new_feature_table"
```

---

## 6. Сервис Redis (Сессии, Кэш, 2FA)

В версии v0.7.8 в стек включён сервис `oss_bot_redis` (`redis:7-alpine`, AOF persistence):

### 6.1. Структура ключей
- `session:<session_id>` — данные сессии пользователя веб-панели (роль, username, CSRF-токены). TTL: 3600 сек.
- `cache:dept:*`, `cache:faq:*`, `cache:stats:*` — кэшированные ответы сервисов. TTL: `CACHE_DEFAULT_TTL` (300 сек).
- `2fa:<temp_token>` — временное состояние проверки двухфакторного кода (хэш кода, `user_id`, срок действия 300 сек).

### 6.2. Диагностика Redis
```bash
# Проверка пинга
docker compose exec oss_bot_redis redis-cli ping

# Просмотр статистики памяти и ключей
docker compose exec oss_bot_redis redis-cli info memory
docker compose exec oss_bot_redis redis-cli info keyspace

# Сброс кэша при необходимости
docker compose exec oss_bot_redis redis-cli --scan --pattern "cache:*" | xargs -r docker compose exec -T oss_bot_redis redis-cli del
```

---

## 7. Мониторинг, метрики и аудит

1. **Метрики Prometheus**:
   - URL: `http://localhost:8000/metrics`
   - Отслеживает: количество HTTP-запросов по статусам, время отклика, активные сессии, метрики очереди Outbox.
2. **Sentry SDK**:
   - При настройке переменной `SENTRY_DSN` в `.env` любые ошибки приложения автоматически регистрируются в Sentry с привязкой контекста и окружения.
3. **Единый Error ID**:
   - При любой необработанной ошибке формируется идентификатор `error_id`.
   - Пользователю выводится инструкция: *"Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору"*.
   - Администратор может мгновенно локализовать проблему по коду:
     ```bash
     docker compose logs --tail 500 web-admin | grep "err_a1b2c3d4"
     ```

---

## 8. Полезные скрипты обслуживания

```bash
# Проверка доступности базы данных
docker compose exec bot python scripts/check_db.py

# Проверка здоровья бота (heartbeat)
docker compose exec bot python scripts/healthcheck_bot.py

# Ручное создание резервной копии БД
./scripts/backup.sh

# Восстановление базы из архива
./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-09-01.sql.gz
```

