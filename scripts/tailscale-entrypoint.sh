#!/bin/sh
set -e

# Параметры из окружения (см. docker-compose.yml и .env)
: "${TS_AUTHKEY:?TS_AUTHKEY is required}"
: "${TS_HOSTNAME:=oss-web-panel}"
: "${TS_STATE_DIR:=/var/lib/tailscale}"
# Адрес веб-панели в compose-сети: по имени сервиса (НЕ localhost!)
WEB_ADMIN_URL="${WEB_ADMIN_URL:-http://web-admin:8000}"

# Запускаем tailscaled в фоне (с учётом TS_USERSPACE из окружения)
if [ "$TS_USERSPACE" = "true" ]; then
    tailscaled --tun=userspace-networking &
else
    tailscaled &
fi
TAILSCALED_PID=$!

# Без явного `tailscale up` узел навсегда остаётся в состоянии NeedsLogin
# (логи не появляются, healthcheck падает, контейнер перезапускается).
# tailscaled поднимает local backend не мгновенно, поэтому повторяем попытки.
echo "Подключаемся к Tailscale (tailscale up)..."
for i in $(seq 1 30); do
    if tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME"; then
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        echo "ОШИБКА: не удалось авторизоваться в Tailscale за 60 секунд." >&2
        echo "Проверьте TAILSCALE_AUTH_KEY в .env (https://login.tailscale.com/admin/settings/keys)." >&2
        kill "$TAILSCALED_PID" 2>/dev/null || true
        exit 1
    fi
done
echo "Tailscale подключён!"

# Запускаем HTTPS serve до веб-панели. Хост "web-admin" резолвится в
# compose-сети только после старта контейнера, поэтому настраиваем с ретраями:
# раньше (network_mode: service:tailscale) serve указывал на localhost и панель
# не могла достучаться до БД; теперь панель сама в compose-сети, а Tailscale
# проксирует на неё по имени сервиса.
echo "Включаем HTTPS для панели (tailscale serve)..."
# Убираем устаревшую запись serve с локального таргета (переходим на web-admin)
tailscale serve --delete http://localhost:8000 2>/dev/null || true
tailscale serve --delete "$WEB_ADMIN_URL" 2>/dev/null || true
for i in $(seq 1 30); do
    if tailscale serve --bg "$WEB_ADMIN_URL"; then
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        echo "ОШИБКА: не удалось настроить tailscale serve за 60 секунд" >&2
        kill "$TAILSCALED_PID" 2>/dev/null || true
        exit 1
    fi
done

# Ждём завершения работы
wait "$TAILSCALED_PID"
