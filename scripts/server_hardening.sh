#!/usr/bin/env bash
# ==============================================================================
# Скрипт серверной оптимизации и харденинга безопасности для OSS Bot
# Применяется на целевом сервере под пользователем root
# ==============================================================================
set -euo pipefail

echo "========================================================"
echo "    Начало настройки и харденинга сервера OSS Bot       "
echo "========================================================"

# --- 1. Настройка файла подкачки (SWAP 2 ГБ) ---
echo ""
echo "[1/3] Проверка и настройка файла подкачки (SWAP)..."
CURRENT_SWAP=$(free -m | awk '/^Swap:/ {print $2}')

if [ "$CURRENT_SWAP" -gt 0 ]; then
    echo "  -> SWAP уже активен (${CURRENT_SWAP} МБ). Пропуск создания."
else
    echo "  -> SWAP отсутствует. Создаём /swapfile размером 2 ГБ..."
    if ! fallocate -l 2G /swapfile 2>/dev/null; then
        echo "  -> fallocate не сработал, используем dd..."
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
    fi
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile

    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
        echo "  -> /swapfile добавлен в /etc/fstab для автоподключения при перезагрузке."
    fi

    # Оптимизируем swappiness (использовать swap только при нехватке памяти)
    sysctl vm.swappiness=10
    echo "vm.swappiness=10" > /etc/sysctl.d/99-swappiness.conf
    echo "  -> SWAP успешно создан и активирован:"
    free -h
fi

# --- 2. Отключение BOOTSTRAP в .env ---
echo ""
echo "[2/3] Отключение BOOTSTRAP-режима в .env..."
ENV_FILE="/opt/oss_bot/.env"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "./.env" ]; then
        ENV_FILE="./.env"
    fi
fi

if [ -f "$ENV_FILE" ]; then
    echo "  -> Найден файл окружения: $ENV_FILE"
    if grep -q "^BOOTSTRAP_ALLOWED=" "$ENV_FILE"; then
        sed -i 's/^BOOTSTRAP_ALLOWED=.*/BOOTSTRAP_ALLOWED=false/' "$ENV_FILE"
        echo "  -> Параметр BOOTSTRAP_ALLOWED изменён на false."
    else
        echo "BOOTSTRAP_ALLOWED=false" >> "$ENV_FILE"
        echo "  -> Добавлен параметр BOOTSTRAP_ALLOWED=false."
    fi
    
    # Перезапуск сервисов приложения для подхвата .env
    COMPOSE_DIR=$(dirname "$ENV_FILE")
    if command -v docker &>/dev/null && [ -f "$COMPOSE_DIR/docker-compose.yml" ]; then
        echo "  -> Перезапуск контейнеров bot и web-admin..."
        (cd "$COMPOSE_DIR" && docker compose restart bot web-admin)
        echo "  -> Сервисы перезапущены с обновлённой конфигурацией."
    fi
else
    echo "  -> ВНИМАНИЕ: Файл .env не найден в /opt/oss_bot/.env или текущей директории."
fi

# --- 3. Настройка сетевого экрана (Firewall) и Fail2ban ---
echo ""
echo "[3/4] Настройка сетевого экрана (UFW) и Fail2ban..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/setup_firewall.sh" ]; then
    bash "$SCRIPT_DIR/setup_firewall.sh"
else
    echo "  -> scripts/setup_firewall.sh не найден, пропускаем автоматическую настройку UFW."
fi

# --- 4. Рекомендации по безопасности SSH ---
echo ""
echo "[4/4] Памятка по безопасности SSH:"
echo "  1. Смените пароль root:"
echo "     passwd root"
echo ""
echo "  2. Убедитесь, что ваш SSH-ключ добавлен в ~/.ssh/authorized_keys"
echo "  3. Отключите парольную аутентификацию в /etc/ssh/sshd_config:"
echo "     PasswordAuthentication no"
echo "     systemctl reload sshd"
echo ""
echo "========================================================"
echo "          Харденинг сервера успешно завершён!           "
echo "========================================================"
