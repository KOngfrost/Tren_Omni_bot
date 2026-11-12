# 🛡️ Инструкция по безопасности периметра и блокировке атак (Security Hardening)

> **Статус:** Реализовано в `feature/security-hardening-and-alerts`  
> **Цель:** Защита от сетевых сканеров, брутфорса учетных записей, атак RCE и зондирования конфигураций.

---

## 1. Встроенный Caddy WAF (Docker)

Защита периметра работает непосредственно в контейнере `caddy` на публичных веб-портах (см. [Caddyfile](../../Caddyfile)):
- **Перманентный бан IP-адресов:**
  IP-адреса из переменной окружения `BANNED_CLIENT_IPS` сбрасываются директивой `abort` без ответа.
- **Защита от прямого доступа по IP:**
  Запросы, у которых заголовок `Host` не совпадает с переменной `DOMAIN` из `.env`, немедленно сбрасываются.
- **WAF-фильтрация системных путей и эксплойтов:**
  Любые запросы к `/.env*`, `/.git*`, `/.svn*`, `/.htaccess*`, `phpunit`, `eval-stdin`, `*.php*` блокируются до передачи в бэкенд.

---

## 2. Системный фаервол хоста (UFW / Iptables)

Для пресечения сетевых атак на уровне ядра Linux (до попадания в сетевой стек Docker) выполните на сервере:

```bash
# Блокировка выявленных IP-сканеров
sudo ufw insert 1 deny from <SUSPICIOUS_IP> to any comment 'Banned scanner'

# Проверка статуса правил
sudo ufw status numbered
```

---

## 3. Альтернатива: Настройка внешнего Nginx (при наличии)

Если перед Docker-контейнерами на хосте используется системный Nginx:

```nginx
# 1. Дроп прямого доступа по IP-адресу
server {
    listen <HTTP_PORT> default_server;
    listen [::]:<HTTP_PORT> default_server;
    server_name _;
    return 444;
}

# 2. Основной блок с ограничением частоты запросов
limit_req_zone $binary_remote_addr zone=login_limit:10m rate=5r/m;

server {
    server_name <YOUR_DOMAIN>;

    # Блокировка атакующих IP
    deny <SUSPICIOUS_IP>;

    # Блокировка системных файлов и скриптов
    location ~ /\.(env|git|svn|htaccess|aws|config) {
        deny all;
        return 404;
    }

    location ~* (phpunit|eval-stdin|wp-login|xmlrpc\.php) {
        deny all;
        return 404;
    }

    # Лимитирование входа
    location = /auth/login {
        limit_req zone=login_limit burst=2 nodelay;
        proxy_pass http://<LOCAL_HOST>:<APP_PORT>;
        include proxy_params;
    }

    location / {
        proxy_pass http://<LOCAL_HOST>:<APP_PORT>;
        include proxy_params;
    }
}
```

---

## 4. Telegram-оповещения информационной безопасности

Модуль `core/security_alerts.py` автоматически отправляет уведомления суперадминистратору в Telegram (`TELEGRAM_ADMIN_ID`) через бота (`TELEGRAM_BOT_TOKEN`) при:
1. **Брутфорсе и превышении лимитов:** блокировка IP при > 5 запросов/мин или 5 неудачных попыток входа за 15 минут.
2. **Серии неудачных входов:** 3+ неверных ввода пароля подряд с одного IP.
3. **Попытках IDOR:** попытка администратора с ограниченными правами открыть тикет чужого отдела.
4. **Эксплойт-зондах:** попытки обращения к `.env`, `.git`, `phpunit`, если они достигли приложения.

Защита от флуда (Anti-Spam Cooldown) гарантирует отправку не более 1 алерта в 5 минут на IP с отчётом о количестве подавленных попыток.
