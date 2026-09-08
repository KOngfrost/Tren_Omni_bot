#!/usr/bin/env bash
# Ежедневное резервное копирование базы oss_bot.
#
# Использование (на сервере):
#   ./scripts/backup.sh
#
# Переменные окружения:
#   BACKUP_DIR      -- каталог для копий (по умолчанию /var/backups/oss_bot)
#   DAILY_KEEP      -- сколько дневных копий хранить (по умолчанию 7)
#   WEEKLY_KEEP     -- сколько недельных копий хранить (по умолчанию 4)
#   MONTHLY_KEEP    -- сколько месячных копий хранить (по умолчанию 3)
#
# Удалённая выгрузка (ОБЯЗАТЕЛЬНА для production):
#   BACKUP_REMOTE=RCLONE_REMOTE:path, например BACKUP_REMOTE=myb2:oss_bot_backups
#   Для этого нужен настроенный rclone (https://rclone.org). Если переменная
#   не задана, скрипт только предупреждает: локальная копия не защищает
#   от потери сервера.
#
# Настройка cron (ежедневно в 03:00):
#   0 3 * * * BACKUP_REMOTE=myb2:oss_bot_backups /opt/oss_bot/scripts/backup.sh >> /var/log/oss_bot_backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/oss_bot}"
DAILY_KEEP="${DAILY_KEEP:-7}"
WEEKLY_KEEP="${WEEKLY_KEEP:-4}"
MONTHLY_KEEP="${MONTHLY_KEEP:-3}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"

# Читаем POSTGRES_* из .env проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/../.env"
    set +a
fi

POSTGRES_USER="${POSTGRES_USER:?POSTGRES_USER is required}"
POSTGRES_DB="${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%F)"
FILE="${BACKUP_DIR}/oss_bot_${STAMP}.sql.gz"

echo "[$(date '+%F %T')] Backup started -> ${FILE}"
echo "[$(date '+%F %T')] Waiting for a healthy db container..."

# Docker может стартовать дольше: ждём готовности БД (до 60 секунд)
for i in $(seq 1 30); do
    if docker compose -f "${SCRIPT_DIR}/../docker-compose.yml" exec -T db \
        pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# PGPASSWORD задаём явно -- защита от настроек trust/peer в нестандартных окружениях.
docker compose -f "${SCRIPT_DIR}/../docker-compose.yml" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists | gzip > "$FILE"

# Проверка целостности архива: битый бэкап хуже отсутствующего
if ! gzip -t "$FILE"; then
    echo "[$(date '+%F %T')] ERROR: архив повреждён: $FILE" >&2
    rm -f "$FILE"
    exit 1
fi

echo "[$(date '+%F %T')] Backup done, verified: $(du -h "$FILE" | cut -f1)"

# Ротация: дневные копии старше DAILY_KEEP дней
find "$BACKUP_DIR" -name "oss_bot_*.sql.gz" -mtime +"$DAILY_KEEP" -delete

echo "[$(date '+%F %T')] INFO: старые дневные копии удалены (keep=${DAILY_KEEP}d)."

for f in "$BACKUP_DIR"/oss_bot_*.sql.gz; do
    d="$(basename "$f" | sed 's/oss_bot_//; s/.sql.gz//')"
    dow="$(date -d "$d" +%u 2>/dev/null || echo 0)"
    day_age=$(( ($(date +%s) - $(date -d "$d" +%s)) / 86400 ))
    if [[ "$dow" == "7" && "$day_age" -le $((WEEKLY_KEEP * 7)) ]]; then
        touch "$f"   # не даём дневной ротации удалить недельную копию
    fi
    # Месячные копии (1-е число) храним дольше всех
    dom="$(date -d "$d" +%d 2>/dev/null || echo 0)"
    if [[ "$dom" == "01" && "$day_age" -le $((MONTHLY_KEEP * 30)) ]]; then
        touch "$f"
    fi
done

echo "[$(date '+%F %T')] Rotation done (daily=${DAILY_KEEP}, weekly=${WEEKLY_KEEP}, monthly=${MONTHLY_KEEP})"

# --- Удалённая выгрузка (rclone; обязательна для production) ---
if [[ -n "$BACKUP_REMOTE" ]]; then
    if command -v rclone >/dev/null 2>&1; then
        echo "[$(date '+%F %T')] Uploading backup to ${BACKUP_REMOTE} ..."
        rclone copy --progress --max-age "${DAILY_KEEP}d" "$BACKUP_DIR" "$BACKUP_REMOTE"
        echo "[$(date '+%F %T')] Upload done"
    else
        echo "[$(date '+%F %T')] WARNING: BACKUP_REMOTE задан, но rclone не установлен -- выгрузка пропущена" >&2
    fi
else
    echo "[$(date '+%F %T')] INFO: BACKUP_REMOTE не задан -- удалённой копии нет. Production-рекомендация: настройте rclone и задайте BACKUP_REMOTE." >&2
fi