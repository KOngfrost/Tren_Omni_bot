#!/usr/bin/env bash
# ==============================================================================
# Скрипт настройки сетевого экрана (Firewall) и Fail2ban для сервера OSS Bot
# Применяется на Linux-сервере (Ubuntu/Debian) под пользователем root
# ==============================================================================
set -euo pipefail

echo "========================================================"
echo "    Настройка сетевого экрана (UFW) и Fail2ban         "
echo "========================================================"

# Проверка прав root
if [ "$(id -u)" -ne 0 ]; then
    echo "Ошибка: Этот скрипт должен быть запущен с правами root (sudo)." >&2
    exit 1
fi

# 1. Установка необходимых пакетов
echo ""
echo "[1/4] Обновление пакетов и установка ufw, fail2ban, iptables-persistent..."
apt-get update -q
apt-get install -y -q ufw fail2ban iptables

# 2. Определение текущего порта SSH, чтобы не потерять доступ
echo ""
echo "[2/4] Проверка порта SSH..."
SSH_PORT="22"
if [ -f "/etc/ssh/sshd_config" ]; then
    DETECTED_PORT=$(grep -E "^Port [0-9]+" /etc/ssh/sshd_config | awk '{print $2}' || true)
    if [ -n "$DETECTED_PORT" ]; then
        SSH_PORT="$DETECTED_PORT"
        echo "  -> Обнаружен нестандартный порт SSH: $SSH_PORT"
    fi
fi

# 3. Настройка UFW правил
echo ""
echo "[3/4] Конфигурация правил UFW..."
# Сброс правил по умолчанию
ufw --force reset >/dev/null 2>&1 || true

ufw default deny incoming
ufw default allow outgoing

# Разрешаем только критически необходимые порты
ufw allow "${SSH_PORT}/tcp" comment "SSH Remote Management"
ufw allow 80/tcp comment "HTTP (Caddy SSL challenge & redirect)"
ufw allow 443/tcp comment "HTTPS (Caddy secure web panel)"

# ВАЖНО: Закрываем внутренние порты от внешнего мира
# (PostgreSQL 5432, PgBouncer 6432, Redis 6379, Prometheus 9090, Go API 8080)
# Внутренний трафик Docker общается через мостовой интерфейс docker0 / br-*
echo "  -> Все порты СУБД, Redis и метрик закрыты от публичного доступа."

# 4. Защита от обхода UFW демоном Docker (DOCKER-USER chain)
# По умолчанию Docker создаёт правила iptables в обход UFW INPUT.
# Исправляем это добавлением проверки в цепочку DOCKER-USER в /etc/ufw/after.rules
AFTER_RULES="/etc/ufw/after.rules"
if [ -f "$AFTER_RULES" ] && ! grep -q "DOCKER-USER" "$AFTER_RULES"; then
    echo "  -> Настройка защиты DOCKER-USER в $AFTER_RULES..."
    cat << 'EOF' >> "$AFTER_RULES"

# --- Защита Docker от внешнего доступа в обход UFW ---
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A DOCKER-USER -i lo -j ACCEPT
-A DOCKER-USER -i docker0 -j ACCEPT
-A DOCKER-USER -i br-+ -j ACCEPT
-A DOCKER-USER -p tcp -m multiport --dports 80,443 -j ACCEPT
-A DOCKER-USER -j DROP
COMMIT
EOF
fi

# Включение UFW
echo "  -> Включение UFW..."
ufw --force enable
ufw status verbose

# 5. Настройка Fail2ban для защиты от подбора паролей
echo ""
echo "[4/4] Конфигурация Fail2ban (защита SSH)..."
cat << EOF > /etc/fail2ban/jail.local
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled  = true
port     = ${SSH_PORT}
logpath  = %(sshd_log)s
backend  = %(sshd_backend)s
maxretry = 3
bantime  = 24h
EOF

systemctl enable fail2ban
systemctl restart fail2ban

echo ""
echo "========================================================"
echo "    Firewall (UFW) и Fail2ban успешно активированы!    "
echo "========================================================"
echo "Открытые порты:"
echo "  - ${SSH_PORT}/tcp (SSH)"
echo "  - 80/tcp (HTTP -> HTTPS)"
echo "  - 443/tcp (HTTPS)"
echo "Все остальные порты надёжно заблокированы."
