#!/usr/bin/env bash
# Восстановление базы oss_bot из резервной копии.
#
# Использование:
#   ./scripts/restore.sh /var/backups/oss_bot/oss_bot_2026-08-31.sql.gz
#
# Что делает:
#   1. Останавливает bot и web-admin (чтобы не было записи во время restore).
#   2. Принудительно завершает все сессии к базе (pg_terminate_backend)
#      и останавливает PgBouncer, чтобы пулер не держал/не плодил
#      соединения во время пересоздания базы.
#   3. Пересоздаёт базу (dropdb + createdb).
#   4. Восстанавливает данные из дампа.
#   5. Возвращает PgBouncer в строй; bot и web-admin остаются
#      остановленными — запустите их вручную командой
#      docker compose start bot web-admin.
#
# Подтверждение вручную обязательно: операция полностью перезаписывает базу.
#
# Раз в месяц ОБЯЗАТЕЛЬНО проверяйте восстановление на тестовой базе.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Использование: $0 <backup.sql.gz>"
    exit 1
fi

BACKUP_FILE="$1"
[[ -f "$BACKUP_FILE" ]] || { echo "Файл не найден: $BACKUP_FILE"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "${SCRIPT_DIR}/../docker-compose.yml")
if [[ -f "${SCRIPT_DIR}/../.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/../.env"
    set +a
fi

POSTGRES_USER="${POSTGRES_USER:?POSTGRES_USER is required}"
POSTGRES_DB="${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

echo "Восстановление ${BACKUP_FILE} в базу ${POSTGRES_DB}."
read -r -p "Текущие данные будут удалены. Продолжить? (yes/no): " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { echo "Отменено"; exit 1; }

# Проверка целостности архива перед началом
if ! gzip -t "$BACKUP_FILE"; then
    echo "ОШИБКА: архив повреждён: $BACKUP_FILE" >&2
    exit 1
fi

echo "[$(date '+%F %T')] Останавливаем bot и web-admin..."
"${COMPOSE[@]}" stop bot web-admin || true

echo "[$(date '+%F %T')] Останавливаем PgBouncer (пауза пулера)..."
"${COMPOSE[@]}" stop pgbouncer || true

echo "[$(date '+%F %T')] Принудительно завершаем все сессии к базе ${POSTGRES_DB}..."
# pg_terminate_backend отключает клиентов; pid <> pg_backend_pid() оставляет
# текущее соединение psql к обслуживающей БД postgres нетронутым.
"${COMPOSE[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" \
    >/dev/null || true
sleep 2

echo "[$(date '+%F %T')] Пересоздаём базу..."
"${COMPOSE[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"
"${COMPOSE[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"

echo "[$(date '+%F %T')] Restore started..."
gunzip -c "$BACKUP_FILE" | "${COMPOSE[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q

echo "[$(date '+%F %T')] Возвращаем PgBouncer в строй..."
"${COMPOSE[@]}" start pgbouncer

echo "[$(date '+%F %T')] Restore finished. Проверьте данные:"
echo "  docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c 'SELECT count(*) FROM tickets;'"
echo ""
echo "PgBouncer уже работает. Запуск сервисов:"
echo "  docker compose start bot web-admin"
