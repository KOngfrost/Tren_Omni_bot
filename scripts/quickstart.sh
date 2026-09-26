#!/usr/bin/env bash
# ============================================================
# OSS Bot - Автоматизированный скрипт быстрого старта (Quickstart)
# ============================================================
set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}       🚀 OSS Bot — Быстрый старт системы (v0.8.8.1)        ${NC}"
echo -e "${BLUE}============================================================${NC}"

# 1. Проверка наличия Docker и Docker Compose
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker не найден. Установите Docker: https://docs.docker.com/engine/install/${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose v2 не найден. Установите плагин docker-compose-plugin.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker и Docker Compose обнаружены.${NC}"

# 2. Создание .env файла при его отсутствии
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚙️  Файл .env не найден. Создаём конфигурацию из .env.example...${NC}"
    cp .env.example .env

    # Генерация криптографически стойких случайных секретов
    PG_PASS=$(openssl rand -hex 16 2>/dev/null || date +%s%N | sha256sum | head -c 32)
    REDIS_PASS=$(openssl rand -hex 16 2>/dev/null || date +%s%N | sha256sum | head -c 32)
    SESSION_SEC=$(openssl rand -hex 24 2>/dev/null || date +%s%N | sha256sum | head -c 48)

    sed -i "s/POSTGRES_PASSWORD=change_me_to_a_long_secret/POSTGRES_PASSWORD=${PG_PASS}/" .env
    sed -i "s/REDIS_PASSWORD=change_me_to_a_long_secret/REDIS_PASSWORD=${REDIS_PASS}/" .env
    sed -i "s/SESSION_SECRET_KEY=change_me_to_a_long_secret/SESSION_SECRET_KEY=${SESSION_SEC}/" .env

    echo -e "${GREEN}✅ Сгенерированы надёжные пароли для PostgreSQL, Redis и сессий в .env.${NC}"
    echo -e "${YELLOW}⚠️  Пожалуйста, укажите токен VK (VK_BOT_TOKEN) в файле .env перед началом работы!${NC}"
else
    echo -e "${GREEN}✅ Конфигурационный файл .env уже существует.${NC}"
fi

# 3. Применение миграций схемы базы данных
echo -e "${BLUE}🔄 Применение миграций базы данных (Alembic)...${NC}"
docker compose run --rm migrate

# 4. Сборка и запуск всех сервисов
echo -e "${BLUE}📦 Запуск контейнеров OSS Bot...${NC}"
docker compose up -d --build

# 5. Проверка статуса
echo -e "${BLUE}🔍 Проверка статуса запущенных сервисов...${NC}"
sleep 3
docker compose ps

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}       🎉 Все сервисы успешно инициализированы!           ${NC}"
echo -e "${GREEN}============================================================${NC}"
echo -e "Веб-панель доступна по адресу: http://localhost:8000"
echo -e "Метрики Prometheus: http://localhost:8000/metrics"
echo -e "Для создания первого суперадминистратора выполните:"
echo -e "  docker compose exec web python scripts/init_superadmin.py --login admin"
echo -e "${GREEN}============================================================${NC}"
