#!/usr/bin/env bash
# Настройка доступа к веб-панели oss-web-panel через Tailscale с HTTPS.
#
# Tailscale-контейнер поднимается вместе со стеком по умолчанию
# (docker compose up -d), поэтому этот скрипт нужен только один раз
# после первого развёртывания, чтобы включить HTTPS-публикацию панели.
#
# Шаги:
#   1. Запускает весь стек (включая tailscale).
#   2. Ждёт, пока tailscale-контейнер зарегистрируется в tailnet.
#   3. Включает HTTPS для веб-панели (tailscale serve).
#   4. Устанавливает SESSION_HTTPS_ONLY=true в .env и перезапускает панель.
#   5. Печатает адрес панели.
#
# Требования:
#   - TAILSCALE_AUTH_KEY в .env (https://login.tailscale.com/admin/settings/keys)
#
# Использование:
#   ./scripts/setup_tailscale.sh
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE=(docker compose -f "${PROJECT_DIR}/docker-compose.yml")

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    echo "ОШИБКА: .env не найден. Скопируйте .env.example в .env и заполните переменные." >&2
    exit 1
fi

if ! grep -q '^TAILSCALE_AUTH_KEY=' .env 2>/dev/null; then
    echo "ОШИБКА: TAILSCALE_AUTH_KEY не задан в .env. Получите ключ: https://login.tailscale.com/admin/settings/keys" >&2
    exit 1
fi

# Secure-cookie для HTTPS через tailscale serve
if grep -q '^SESSION_HTTPS_ONLY=' .env 2>/dev/null; then
    sed -i 's/^SESSION_HTTPS_ONLY=.*/SESSION_HTTPS_ONLY=true/' .env
else
    echo "SESSION_HTTPS_ONLY=true" >> .env
fi
echo "SESSION_HTTPS_ONLY установлен в true (Secure-cookie для HTTPS)"

echo ""
echo "Запускаем стек..."
"${COMPOSE[@]}" up -d --build

echo "Ждём готовности tailscale-контейнера..."
for i in $(seq 1 30); do
    if "${COMPOSE[@]}" exec -T tailscale tailscale status >/dev/null 2>&1; then
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        echo "ОШИБКА: Tailscale не стал активным за 60 секунд. Смотрите: docker compose logs tailscale" >&2
        exit 1
    fi
done

MAGIC_NAME="$("${COMPOSE[@]}" exec -T tailscale tailscale status --json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); self=d.get('Self'); print(self.get('DNSName','').rstrip('.') if self else '')" 2>/dev/null || true)"
if [ -z "$MAGIC_NAME" ]; then
    MAGIC_NAME="oss-web-panel.<tailnet>.ts.net"
fi

echo "Включаем HTTPS для панели (tailscale serve)..."
# Сначала удаляем старую настройку serve (если была), затем настраиваем корректно
"${COMPOSE[@]}" exec -T tailscale tailscale serve --delete http://web-admin:8000 2>/dev/null || true
"${COMPOSE[@]}" exec -T tailscale tailscale serve http://web-admin:8000

echo "Перезапускаем веб-панель для применения SESSION_HTTPS_ONLY=true..."
"${COMPOSE[@]}" up -d --force-recreate web-admin

echo ""
echo "Готово. Панель доступна внутри tailnet по адресу:"
echo "  https://${MAGIC_NAME}/"
echo ""
echo "Полезные команды:"
echo "  docker compose exec oss_bot_tailscale tailscale status"
echo "  docker compose exec oss_bot_tailscale tailscale serve --help"
echo ""
echo "Firewall: откройте только 22/tcp (SSH). Порты 5432 и 8000 наружу не публикуйте."