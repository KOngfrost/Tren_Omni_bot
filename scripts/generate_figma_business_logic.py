"""
Генератор дизайн-схемы бизнес-логики бота OSS Bot для импорта в Figma.
Создаёт масштабируемый SVG-артборд высокого разрешения (2880 x 1800 px)
со всеми ключевыми процессами:
- VK LongPoll и фильтрация событий (включая silent ignore для произвольного текста)
- FSM-машина создания и ведения обращений студентов (SEC-08 Rate Limit, анонимность)
- Панель кураторов и админов (ролевая модель, 2FA OTP, тикетная триажная система)
- Transactional Outbox Pattern и надёжная доставка сообщений
- Инфраструктура, Redis-кэш, PII-санитизация и Telegram Sentinel
"""

import os
from pathlib import Path

FIGMA_DIR = Path(__file__).resolve().parent.parent / "figma"
FIGMA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = FIGMA_DIR / "bot_business_logic.svg"

WIDTH = 2880
HEIGHT = 1800

SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
<defs>
    <!-- Цветовые градиенты -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#07070A" />
        <stop offset="50%" stop-color="#0C0C12" />
        <stop offset="100%" stop-color="#08080C" />
    </linearGradient>

    <linearGradient id="primaryGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#FF5EA6" />
        <stop offset="100%" stop-color="#EA2679" />
    </linearGradient>

    <linearGradient id="purpleGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#B855F6" />
        <stop offset="100%" stop-color="#7C3AED" />
    </linearGradient>

    <linearGradient id="cyanGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#06B6D4" />
        <stop offset="100%" stop-color="#0284C7" />
    </linearGradient>

    <linearGradient id="greenGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#10B981" />
        <stop offset="100%" stop-color="#059669" />
    </linearGradient>

    <linearGradient id="amberGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#F59E0B" />
        <stop offset="100%" stop-color="#D97706" />
    </linearGradient>

    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#161620" />
        <stop offset="100%" stop-color="#111118" />
    </linearGradient>

    <linearGradient id="cardGradAlt" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#1C1C28" />
        <stop offset="100%" stop-color="#13131D" />
    </linearGradient>

    <linearGradient id="cardHighlight" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#FF4D9D" stop-opacity="0.3" />
        <stop offset="100%" stop-color="#EA2679" stop-opacity="0.05" />
    </linearGradient>

    <!-- Сетка точек -->
    <pattern id="dotGrid" x="0" y="0" width="32" height="32" patternUnits="userSpaceOnUse">
        <circle cx="2" cy="2" r="1.2" fill="#252536" fill-opacity="0.4" />
    </pattern>

    <!-- Тени и свечения -->
    <filter id="cardShadow" x="-10%" y="-10%" width="120%" height="125%">
        <feDropShadow dx="0" dy="10" stdDeviation="16" flood-color="#000000" flood-opacity="0.6" />
    </filter>

    <filter id="pinkGlow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="12" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>

    <!-- Маркеры стрелок -->
    <marker id="arrowPink" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9 z" fill="#FF4D9D" />
    </marker>
    <marker id="arrowCyan" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9 z" fill="#06B6D4" />
    </marker>
    <marker id="arrowPurple" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9 z" fill="#B855F6" />
    </marker>
    <marker id="arrowGreen" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9 z" fill="#10B981" />
    </marker>
    <marker id="arrowMuted" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9 z" fill="#6B7280" />
    </marker>

    <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&amp;display=swap');
        text {{ font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .header-title {{ font-size: 38px; font-weight: 800; fill: #FFFFFF; letter-spacing: -0.5px; }}
        .header-sub {{ font-size: 16px; font-weight: 500; fill: #9E9EB2; }}
        .frame-title {{ font-size: 20px; font-weight: 700; fill: #FFFFFF; }}
        .frame-sub {{ font-size: 12px; font-weight: 500; fill: #8F8FA4; }}
        .node-title {{ font-size: 15px; font-weight: 700; fill: #FFFFFF; }}
        .node-desc {{ font-size: 12px; font-weight: 400; fill: #A5A5B8; line-height: 1.4; }}
        .badge-text {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }}
        .code-pill {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 11px; fill: #FF85BA; }}
        .flow-line {{ stroke-width: 2.2; fill: none; stroke-linecap: round; stroke-linejoin: round; }}
        .flow-line-dashed {{ stroke-width: 2; fill: none; stroke-dasharray: 6 6; stroke-linecap: round; }}
    </style>
</defs>

<!-- Фоновые плоскости -->
<rect width="{width}" height="{height}" fill="url(#bgGrad)" />
<rect width="{width}" height="{height}" fill="url(#dotGrid)" />

<!-- Декоративные световые ореолы -->
<circle cx="250" cy="200" r="300" fill="#FF4D9D" fill-opacity="0.04" filter="blur(80px)" />
<circle cx="2600" cy="400" r="350" fill="#7C3AED" fill-opacity="0.05" filter="blur(100px)" />
<circle cx="1400" cy="1500" r="450" fill="#06B6D4" fill-opacity="0.04" filter="blur(120px)" />

{content}

</svg>
"""


def create_board() -> str:
    parts = []

    # =========================================================================
    # 1. ШАПКА ХОЛСТА (HEADER BAR)
    # =========================================================================
    parts.append("""
    <g id="Canvas_Header" transform="translate(60, 50)">
        <!-- Логотип OB -->
        <rect x="0" y="0" width="64" height="64" rx="16" fill="url(#primaryGrad)" filter="url(#pinkGlow)"/>
        <text x="32" y="40" font-size="24" font-weight="800" fill="#FFFFFF" text-anchor="middle">OB</text>

        <!-- Заголовок и метаданные -->
        <text x="84" y="28" class="header-title">OSS Bot — Архитектура и бизнес-логика системы</text>
        <text x="84" y="52" class="header-sub">Полная схема обработки событий, жизненного цикла тикетов, очередей Outbox и ролевой модели • Версия v0.8.6</text>

        <!-- Технологические бейджи -->
        <g transform="translate(1800, 14)">
            <!-- Badge 1: VK Bottle -->
            <rect x="0" y="0" width="130" height="34" rx="8" fill="#1E1E2C" stroke="#2D2D42" stroke-width="1"/>
            <circle cx="16" cy="17" r="5" fill="#0077FF"/>
            <text x="30" y="22" font-size="12" font-weight="600" fill="#E2E8F0">VK Bottle 2.7</text>

            <!-- Badge 2: PostgreSQL & PgBouncer -->
            <rect x="142" y="0" width="165" height="34" rx="8" fill="#1E1E2C" stroke="#2D2D42" stroke-width="1"/>
            <circle cx="158" cy="17" r="5" fill="#336791"/>
            <text x="172" y="22" font-size="12" font-weight="600" fill="#E2E8F0">Postgres + PgBouncer</text>

            <!-- Badge 3: Redis 7.0 -->
            <rect x="319" y="0" width="115" height="34" rx="8" fill="#1E1E2C" stroke="#2D2D42" stroke-width="1"/>
            <circle cx="335" cy="17" r="5" fill="#DC382D"/>
            <text x="349" y="22" font-size="12" font-weight="600" fill="#E2E8F0">Redis 7.0</text>

            <!-- Badge 4: Outbox Pattern -->
            <rect x="446" y="0" width="145" height="34" rx="8" fill="#1E1E2C" stroke="#2D2D42" stroke-width="1"/>
            <circle cx="462" cy="17" r="5" fill="#10B981"/>
            <text x="476" y="22" font-size="12" font-weight="600" fill="#E2E8F0">Outbox Pattern</text>

            <!-- Badge 5: Production Live -->
            <rect x="603" y="0" width="125" height="34" rx="8" fill="rgba(16, 185, 129, 0.15)" stroke="#10B981" stroke-width="1.2"/>
            <circle cx="619" cy="17" r="5" fill="#10B981" filter="url(#pinkGlow)"/>
            <text x="633" y="22" font-size="12" font-weight="700" fill="#10B981">Live on Server</text>
        </g>
    </g>
    """)

    # =========================================================================
    # 2. БЛОК 1: VK LONGPOLL & MIDDLEWARE (Слева вверху)
    # =========================================================================
    parts.append("""
    <g id="Frame_01_Ingestion" transform="translate(60, 150)">
        <!-- Фоновый контейнер фрейма -->
        <rect x="0" y="0" width="620" height="740" rx="20" fill="url(#cardGrad)" stroke="#262638" stroke-width="1.5" filter="url(#cardShadow)"/>
        
        <!-- Заголовок фрейма -->
        <rect x="0" y="0" width="620" height="60" rx="20" fill="url(#cardHighlight)"/>
        <circle cx="32" cy="30" r="14" fill="#0077FF" fill-opacity="0.2"/>
        <text x="32" y="35" font-size="14" font-weight="800" fill="#0077FF" text-anchor="middle">1</text>
        <text x="60" y="36" class="frame-title">VK LongPoll &amp; Фильтрация Событий</text>
        <text x="450" y="36" class="badge-text" fill="#0077FF">EVENT PIPELINE</text>

        <!-- Node 1.1: LongPoll Входящее событие -->
        <g transform="translate(30, 80)">
            <rect x="0" y="0" width="560" height="96" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="96" rx="3" fill="#0077FF"/>
            <text x="24" y="30" class="node-title">VK LongPoll Event Ingestion</text>
            <text x="430" y="28" class="code-pill">group_id: 240695160</text>
            <text x="24" y="55" class="node-desc">Событие от пользователя VK (сообщение, нажатие payload-кнопки в карусели/меню).</text>
            <text x="24" y="75" class="node-desc" fill="#71718A">Диспетчер RobustBotPolling с автопереподключением и heartbeat монитором.</text>
        </g>

        <!-- Стрелка вниз -->
        <path d="M 310 180 L 310 206" class="flow-line" stroke="#0077FF" marker-end="url(#arrowCyan)"/>

        <!-- Node 1.2: Maintenance Middleware -->
        <g transform="translate(30, 210)">
            <rect x="0" y="0" width="560" height="110" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="110" rx="3" fill="#F59E0B"/>
            <text x="24" y="30" class="node-title">VKMaintenanceMiddleware (SEC-11)</text>
            <rect x="420" y="14" width="120" height="22" rx="6" fill="#2E2314"/>
            <text x="480" y="29" font-size="11" font-weight="700" fill="#F59E0B" text-anchor="middle">Redis / Cache</text>
            <text x="24" y="55" class="node-desc">Проверка глобального флага техработ (ключ <tspan class="code-pill">maintenance:mode</tspan>).</text>
            <text x="24" y="75" class="node-desc">• <tspan fill="#F59E0B">Активен:</tspan> возврат нейтральной карточки обновления для обычных пользователей.</text>
            <text x="24" y="95" class="node-desc">• <tspan fill="#10B981">Не активен:</tspan> прозрачный пропуск сообщения дальше в цепочку хендлеров.</text>
        </g>

        <!-- Стрелка вниз -->
        <path d="M 310 324 L 310 350" class="flow-line" stroke="#F59E0B" marker-end="url(#arrowAmber)"/>

        <!-- Node 1.3: Router / Labeler Dispatcher -->
        <g transform="translate(30, 355)">
            <rect x="0" y="0" width="560" height="106" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="106" rx="3" fill="#B855F6"/>
            <text x="24" y="30" class="node-title">Приоритетная диспетчеризация (BotLabeler)</text>
            <text x="24" y="55" class="node-desc">Последовательная проверка правил по порядку приоритета:</text>
            <text x="24" y="75" class="node-desc" fill="#C4B5FD">1. Активное FSM-состояние (Ввод темы, текста обращения, даты отчёта)</text>
            <text x="24" y="93" class="node-desc" fill="#C4B5FD">2. Payload кнопок меню • 3. Текстовые слэш-команды (/start, меню, заявки)</text>
        </g>

        <!-- Разветвление стрелок -->
        <path d="M 200 465 L 200 500" class="flow-line" stroke="#10B981" marker-end="url(#arrowGreen)"/>
        <path d="M 420 465 L 420 500" class="flow-line" stroke="#FF4D9D" marker-end="url(#arrowPink)"/>

        <!-- Node 1.4: Реакция на команды меню -->
        <g transform="translate(30, 505)">
            <rect x="0" y="0" width="265" height="205" rx="14" fill="#131D18" stroke="#1E3A2B" stroke-width="1.2"/>
            <rect x="0" y="0" width="5" height="205" rx="2.5" fill="#10B981"/>
            <text x="18" y="28" font-size="14" font-weight="700" fill="#10B981">✓ Команда / Кнопка меню</text>
            <text x="18" y="52" class="node-desc">Срабатывает мгновенный</text>
            <text x="18" y="70" class="node-desc">предметный хендлер:</text>
            <text x="18" y="96" class="node-desc" fill="#A7F3D0">• FAQ &amp; Поиск ответов</text>
            <text x="18" y="118" class="node-desc" fill="#A7F3D0">• База знаний кафедр</text>
            <text x="18" y="140" class="node-desc" fill="#A7F3D0">• Создание обращения</text>
            <text x="18" y="162" class="node-desc" fill="#A7F3D0">• Список «Мои заявки»</text>
            <text x="18" y="184" class="node-desc" fill="#A7F3D0">• Админ-панель куратора</text>
        </g>

        <!-- Node 1.5: Произвольный текст (Новая фича!) -->
        <g transform="translate(325, 505)">
            <rect x="0" y="0" width="265" height="205" rx="14" fill="#1C141B" stroke="#3D2030" stroke-width="1.2"/>
            <rect x="0" y="0" width="5" height="205" rx="2.5" fill="#FF4D9D"/>
            <text x="18" y="28" font-size="14" font-weight="700" fill="#FF5EA6">✕ Произвольный текст</text>
            <text x="18" y="50" class="badge-text" fill="#FF85BA">SILENT FALLBACK (NEW)</text>
            <text x="18" y="76" class="node-desc">Если отправлен произвольный</text>
            <text x="18" y="94" class="node-desc">текст («привет», «спасибо») без</text>
            <text x="18" y="112" class="node-desc">активного диалога и команд:</text>
            <text x="18" y="138" class="node-desc" fill="#FFD1E6">• <tspan font-weight="700">НЕ отвечает</tspan> ошибкой</text>
            <text x="18" y="158" class="node-desc" fill="#FFD1E6">  «я не распознал команду»</text>
            <text x="18" y="178" class="node-desc" fill="#10B981">• Обновляет heartbeat</text>
            <text x="18" y="196" class="node-desc" fill="#9E9EB2">• Не спамит меню в чат</text>
        </g>
    </g>
    """)

    # =========================================================================
    # 3. БЛОК 2: СЦЕНАРИИ СТУДЕНТА И FSM МАШИНА (По центру)
    # =========================================================================
    parts.append("""
    <g id="Frame_02_Student_FSM" transform="translate(710, 150)">
        <rect x="0" y="0" width="820" height="740" rx="20" fill="url(#cardGrad)" stroke="#262638" stroke-width="1.5" filter="url(#cardShadow)"/>
        
        <rect x="0" y="0" width="820" height="60" rx="20" fill="url(#cardHighlight)"/>
        <circle cx="32" cy="30" r="14" fill="#FF4D9D" fill-opacity="0.2"/>
        <text x="32" y="35" font-size="14" font-weight="800" fill="#FF4D9D" text-anchor="middle">2</text>
        <text x="60" y="36" class="frame-title">Сценарии Студента &amp; Пошаговая FSM-Машина</text>
        <text x="620" y="36" class="badge-text" fill="#FF4D9D">CORE STUDENT UX</text>

        <!-- 4 Ветви студенческого интерфейса -->
        <g transform="translate(30, 80)">
            <!-- Ветка 1: FAQ -->
            <rect x="0" y="0" width="175" height="120" rx="12" fill="#181826" stroke="#2B2B42" stroke-width="1.2"/>
            <text x="16" y="26" font-size="14" font-weight="700" fill="#00D2FF">❓ Вопросы &amp; FAQ</text>
            <text x="16" y="48" class="node-desc">Рубрикатор тем и быстрый поиск.</text>
            <rect x="14" y="82" width="145" height="24" rx="6" fill="#132433"/>
            <text x="86" y="98" font-size="11" font-weight="600" fill="#38BDF8" text-anchor="middle">Redis cache: faq:*</text>

            <!-- Ветка 2: База знаний -->
            <rect x="195" y="0" width="175" height="120" rx="12" fill="#181826" stroke="#2B2B42" stroke-width="1.2"/>
            <text x="16" y="26" font-size="14" font-weight="700" fill="#A78BFA">📚 База знаний</text>
            <text x="16" y="48" class="node-desc">Статьи, шаблоны заявлений, приказы кафедр.</text>
            <rect x="14" y="82" width="145" height="24" rx="6" fill="#241936"/>
            <text x="86" y="98" font-size="11" font-weight="600" fill="#C084FC" text-anchor="middle">FTS Postgres</text>

            <!-- Ветка 3: Мероприятия -->
            <rect x="390" y="0" width="175" height="120" rx="12" fill="#181826" stroke="#2B2B42" stroke-width="1.2"/>
            <text x="16" y="26" font-size="14" font-weight="700" fill="#F472B6">📅 Мероприятия</text>
            <text x="16" y="48" class="node-desc">Анонсы хакатонов, конференций, ссылки.</text>
            <rect x="14" y="82" width="145" height="24" rx="6" fill="#2E1727"/>
            <text x="86" y="98" font-size="11" font-weight="600" fill="#F472B6" text-anchor="middle">Ближайшие даты</text>

            <!-- Ветка 4: Мои заявки -->
            <rect x="585" y="0" width="175" height="120" rx="12" fill="#181826" stroke="#2B2B42" stroke-width="1.2"/>
            <text x="16" y="26" font-size="14" font-weight="700" fill="#34D399">📋 Мои заявки</text>
            <text x="16" y="48" class="node-desc">Статусы тикетов и диалог с куратором.</text>
            <rect x="14" y="82" width="145" height="24" rx="6" fill="#142B24"/>
            <text x="86" y="98" font-size="11" font-weight="600" fill="#34D399" text-anchor="middle">Пагинация списков</text>
        </g>

        <!-- Разделитель: Процесс создания тикета (FSM) -->
        <rect x="30" y="225" width="760" height="485" rx="16" fill="#14141E" stroke="#2B2B3E" stroke-width="1.2"/>
        <text x="50" y="255" font-size="16" font-weight="700" fill="#FFFFFF">Жизненный цикл подачи обращения (FSM: TicketStates)</text>
        <text x="640" y="255" class="badge-text" fill="#10B981">SEC-08 PROTECTED</text>

        <!-- Шаг 1: Rate Limit -->
        <g transform="translate(50, 280)">
            <rect x="0" y="0" width="720" height="70" rx="12" fill="#1A1A26" stroke="#333348" stroke-width="1"/>
            <circle cx="28" cy="35" r="16" fill="#EF4444" fill-opacity="0.2"/>
            <text x="28" y="40" font-size="13" font-weight="800" fill="#EF4444" text-anchor="middle">1</text>
            <text x="60" y="28" class="node-title">Проверка Rate Limit (Антиспам SEC-08)</text>
            <text x="60" y="50" class="node-desc">Ключ <tspan class="code-pill">ticket_rate:{vk_id}</tspan> в Redis. Лимит: максимум 3 обращения за 5 минут. Предотвращает DoS БД.</text>
            <rect x="590" y="20" width="110" height="28" rx="6" fill="rgba(239, 68, 68, 0.15)"/>
            <text x="645" y="38" font-size="11" font-weight="700" fill="#F87171" text-anchor="middle">Max 3 / 5 min</text>
        </g>

        <!-- Шаг 2: Выбор формата и отдела -->
        <g transform="translate(50, 365)">
            <rect x="0" y="0" width="345" height="95" rx="12" fill="#1A1A26" stroke="#333348" stroke-width="1"/>
            <circle cx="28" cy="30" r="16" fill="#FF4D9D" fill-opacity="0.2"/>
            <text x="28" y="35" font-size="13" font-weight="800" fill="#FF4D9D" text-anchor="middle">2</text>
            <text x="60" y="28" class="node-title">Формат обращения</text>
            <text x="60" y="50" class="node-desc">• <tspan fill="#FF85BA">Публичное:</tspan> передача профиля VK куратору</text>
            <text x="60" y="70" class="node-desc">• <tspan fill="#A78BFA">Анонимное:</tspan> скрытие VK ID, имя «Студент #N»</text>
        </g>

        <!-- Шаг 3: Выбор отдела -->
        <g transform="translate(425, 365)">
            <rect x="0" y="0" width="345" height="95" rx="12" fill="#1A1A26" stroke="#333348" stroke-width="1"/>
            <circle cx="28" cy="30" r="16" fill="#38BDF8" fill-opacity="0.2"/>
            <text x="28" y="35" font-size="13" font-weight="800" fill="#38BDF8" text-anchor="middle">3</text>
            <text x="60" y="28" class="node-title">Выбор направления</text>
            <text x="60" y="50" class="node-desc">Динамическая клавиатура отделов из PostgreSQL:</text>
            <text x="60" y="70" class="node-desc" fill="#7DD3FC">Деканат, Бухгалтерия, Студсовет, Общежитие...</text>
        </g>

        <!-- Шаг 4: Пошаговый ввод темы и вопроса -->
        <g transform="translate(50, 475)">
            <rect x="0" y="0" width="720" height="105" rx="12" fill="#1A1A26" stroke="#333348" stroke-width="1"/>
            <circle cx="28" cy="35" r="16" fill="#B855F6" fill-opacity="0.2"/>
            <text x="28" y="40" font-size="13" font-weight="800" fill="#B855F6" text-anchor="middle">4</text>
            <text x="60" y="28" class="node-title">FSM Состояния: Ввод темы и описания</text>
            <text x="60" y="52" class="node-desc"><tspan class="code-pill">WAITING_TOPIC</tspan> — Ввод краткой сути проблемы (до 150 символов).</text>
            <text x="60" y="74" class="node-desc"><tspan class="code-pill">WAITING_TEXT</tspan> — Развёрнутое описание + прикрепление фото / документов.</text>
            <text x="60" y="94" class="node-desc" fill="#9CA3AF">Кнопка «Отмена» возвращает в меню и очищает состояние в Redis StateDispenser.</text>
        </g>

        <!-- Шаг 5: Сохранение и Outbox -->
        <g transform="translate(50, 595)">
            <rect x="0" y="0" width="720" height="95" rx="12" fill="url(#primaryGrad)" fill-opacity="0.1" stroke="#FF4D9D" stroke-width="1.4"/>
            <circle cx="28" cy="35" r="16" fill="#10B981"/>
            <text x="28" y="41" font-size="15" font-weight="800" fill="#FFFFFF" text-anchor="middle">✓</text>
            <text x="60" y="28" class="node-title" fill="#FF85BA">Успешная фиксация тикета в базе данных</text>
            <text x="60" y="52" class="node-desc" fill="#E2E8F0">1. Создание записи в таблице <tspan class="code-pill">tickets</tspan> со статусом <tspan fill="#10B981" font-weight="700">«Новое»</tspan>.</text>
            <text x="60" y="72" class="node-desc" fill="#E2E8F0">2. Атомарная запись уведомления кураторам в таблицу <tspan class="code-pill">outbox</tspan> (ACID транзакция).</text>
        </g>
    </g>
    """)

    # =========================================================================
    # 4. БЛОК 3: ПАНЕЛЬ КУРАТОРОВ И АДМИНИСТРАТОРОВ (Справа вверху)
    # =========================================================================
    parts.append("""
    <g id="Frame_03_Admin_Triage" transform="translate(1560, 150)">
        <rect x="0" y="0" width="620" height="740" rx="20" fill="url(#cardGrad)" stroke="#262638" stroke-width="1.5" filter="url(#cardShadow)"/>
        
        <rect x="0" y="0" width="620" height="60" rx="20" fill="url(#cardHighlight)"/>
        <circle cx="32" cy="30" r="14" fill="#B855F6" fill-opacity="0.2"/>
        <text x="32" y="35" font-size="14" font-weight="800" fill="#B855F6" text-anchor="middle">3</text>
        <text x="60" y="36" class="frame-title">Кураторы &amp; Триажная Система</text>
        <text x="440" y="36" class="badge-text" fill="#B855F6">OPERATOR CONTROL</text>

        <!-- Node 3.1: Двойной интерфейс работы -->
        <g transform="translate(30, 80)">
            <rect x="0" y="0" width="560" height="110" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <text x="24" y="28" class="node-title">Два канала управления обращениями</text>
            <!-- VK Panel -->
            <rect x="24" y="44" width="245" height="52" rx="8" fill="#1E1E2E"/>
            <text x="36" y="64" font-size="13" font-weight="700" fill="#38BDF8">🤖 VK Админка бота</text>
            <text x="36" y="84" font-size="11" fill="#94A3B8">Быстрый ответ из чата VK</text>
            <!-- Web Panel -->
            <rect x="290" y="44" width="245" height="52" rx="8" fill="#1E1E2E"/>
            <text x="302" y="64" font-size="13" font-weight="700" fill="#FF5EA6">💻 Веб-панель (FastAPI)</text>
            <text x="302" y="84" font-size="11" fill="#94A3B8">Полный чат, фильтры, экспорт</text>
        </g>

        <!-- Node 3.2: 2FA Авторизация -->
        <g transform="translate(30, 205)">
            <rect x="0" y="0" width="560" height="95" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="95" rx="3" fill="#10B981"/>
            <text x="24" y="28" class="node-title">Двухфакторная защита 2FA (SEC-02)</text>
            <text x="24" y="52" class="node-desc">Вход в веб-панель требует 6-значный одноразовый OTP-код.</text>
            <text x="24" y="74" class="node-desc">Код высылается через бота VK в ЛС администратору (TTL 300 сек).</text>
            <rect x="420" y="16" width="120" height="24" rx="6" fill="#122E22"/>
            <text x="480" y="32" font-size="11" font-weight="700" fill="#34D399" text-anchor="middle">VK OTP Code</text>
        </g>

        <!-- Node 3.3: Триаж и статусы тикетов -->
        <g transform="translate(30, 315)">
            <rect x="0" y="0" width="560" height="235" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <text x="24" y="28" class="node-title">Жизненный цикл статусов тикета</text>
            
            <!-- Статус 1: Новое -->
            <rect x="24" y="45" width="245" height="42" rx="8" fill="#241B2E" stroke="#7C3AED" stroke-width="1"/>
            <circle cx="40" cy="66" r="6" fill="#A855F7"/>
            <text x="56" y="70" font-size="13" font-weight="700" fill="#FFFFFF">Новое</text>
            <text x="120" y="70" font-size="11" fill="#C084FC">Ожидает взятия</text>

            <!-- Статус 2: В работе -->
            <rect x="290" y="45" width="245" height="42" rx="8" fill="#2E1C28" stroke="#EC4899" stroke-width="1"/>
            <circle cx="306" cy="66" r="6" fill="#EC4899"/>
            <text x="322" y="70" font-size="13" font-weight="700" fill="#FFFFFF">В работе</text>
            <text x="400" y="70" font-size="11" fill="#F472B6">Назначен куратор</text>

            <!-- Статус 3: Ждёт ответа студента -->
            <rect x="24" y="98" width="245" height="42" rx="8" fill="#1E2836" stroke="#0284C7" stroke-width="1"/>
            <circle cx="40" cy="119" r="6" fill="#0284C7"/>
            <text x="56" y="123" font-size="13" font-weight="700" fill="#FFFFFF">Ждёт ответа</text>
            <text x="150" y="123" font-size="11" fill="#7DD3FC">Запрос данных</text>

            <!-- Статус 4: Решено -->
            <rect x="290" y="98" width="245" height="42" rx="8" fill="#142B24" stroke="#059669" stroke-width="1"/>
            <circle cx="306" cy="119" r="6" fill="#10B981"/>
            <text x="322" y="123" font-size="13" font-weight="700" fill="#FFFFFF">Решено</text>
            <text x="390" y="123" font-size="11" fill="#6EE7B7">Вопрос закрыт</text>

            <text x="24" y="165" class="node-desc">Куратор может перенаправить обращение в другой отдел,</text>
            <text x="24" y="183" class="node-desc">прикрепить файл решения или использовать шаблонный ответ.</text>
            <text x="24" y="205" class="node-desc" fill="#A855F7">Каждое действие записывается в <tspan class="code-pill">audit_logs</tspan> для истории.</text>
        </g>

        <!-- Node 3.4: Аналитика и отчёты -->
        <g transform="translate(30, 565)">
            <rect x="0" y="0" width="560" height="145" rx="14" fill="#171724" stroke="#2B2B40" stroke-width="1.2"/>
            <text x="24" y="28" class="node-title">Ежедневная аналитика (core/reporting.py)</text>
            <text x="24" y="52" class="node-desc">Автоматическая агрегация статистики за сутки / неделю:</text>
            <text x="24" y="74" class="node-desc" fill="#E2E8F0">• Количество новых и закрытых обращений по отделам</text>
            <text x="24" y="94" class="node-desc" fill="#E2E8F0">• Среднее время первого ответа и рейтинг удовлетворённости</text>
            <text x="24" y="118" class="node-desc" fill="#F472B6">• Дедупликация рассылок через Redis: <tspan class="code-pill">report_vk_sent:{day}:{admin}</tspan></text>
        </g>
    </g>
    """)

    # =========================================================================
    # 5. БЛОК 4: НИЖНИЙ ПЛАСТ — TRANSACTIONAL OUTBOX & БД (Слева снизу)
    # =========================================================================
    parts.append("""
    <g id="Frame_04_Outbox_Engine" transform="translate(60, 930)">
        <rect x="0" y="0" width="1380" height="420" rx="20" fill="url(#cardGrad)" stroke="#262638" stroke-width="1.5" filter="url(#cardShadow)"/>
        
        <rect x="0" y="0" width="1380" height="60" rx="20" fill="url(#cardHighlight)"/>
        <circle cx="32" cy="30" r="14" fill="#10B981" fill-opacity="0.2"/>
        <text x="32" y="35" font-size="14" font-weight="800" fill="#10B981" text-anchor="middle">4</text>
        <text x="60" y="36" class="frame-title">Надёжность Доставки: Transactional Outbox Pattern &amp; База Данных</text>
        <text x="1170" y="36" class="badge-text" fill="#10B981">ZERO MESSAGE LOSS</text>

        <!-- Схема Outbox шагов -->
        <g transform="translate(40, 85)">
            <!-- Шаг 1 -->
            <rect x="0" y="0" width="280" height="150" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="150" rx="3" fill="#3B82F6"/>
            <text x="20" y="30" class="node-title">1. Бизнес-действие</text>
            <text x="20" y="55" class="node-desc">Куратор ответил на тикет</text>
            <text x="20" y="73" class="node-desc">или студент создал заявку.</text>
            <rect x="18" y="95" width="240" height="34" rx="8" fill="#132338"/>
            <text x="138" y="116" font-size="12" font-weight="600" fill="#60A5FA" text-anchor="middle">ACID Transaction Begin</text>

            <!-- Стрелка вправо -->
            <path d="M 295 75 L 335 75" class="flow-line" stroke="#60A5FA" marker-end="url(#arrowCyan)"/>

            <!-- Шаг 2 -->
            <rect x="350" y="0" width="280" height="150" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="150" rx="3" fill="#10B981"/>
            <text x="20" y="30" class="node-title">2. Двойная запись</text>
            <text x="20" y="55" class="node-desc">В рамках одной транзакции:</text>
            <text x="20" y="75" class="node-desc">• Запись сообщения в чат</text>
            <text x="20" y="95" class="node-desc">• Запись задачи в <tspan class="code-pill">outbox</tspan></text>
            <rect x="18" y="105" width="240" height="30" rx="6" fill="#142B24"/>
            <text x="138" y="124" font-size="11" font-weight="700" fill="#34D399" text-anchor="middle">COMMIT WORK</text>

            <!-- Стрелка вправо -->
            <path d="M 645 75 L 685 75" class="flow-line" stroke="#10B981" marker-end="url(#arrowGreen)"/>

            <!-- Шаг 3 -->
            <rect x="700" y="0" width="280" height="150" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="150" rx="3" fill="#F59E0B"/>
            <text x="20" y="30" class="node-title">3. Outbox Dispatcher</text>
            <text x="20" y="55" class="node-desc">Асинхронный воркер читает</text>
            <text x="20" y="73" class="node-desc">необработанные записи Outbox.</text>
            <rect x="18" y="95" width="240" height="34" rx="8" fill="#2E2314"/>
            <text x="138" y="116" font-size="12" font-weight="600" fill="#FBBF24" text-anchor="middle">Exponential Backoff</text>

            <!-- Стрелка вправо -->
            <path d="M 995 75 L 1035 75" class="flow-line" stroke="#F59E0B" marker-end="url(#arrowAmber)"/>

            <!-- Шаг 4 -->
            <rect x="1050" y="0" width="250" height="150" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="150" rx="3" fill="#FF4D9D"/>
            <text x="20" y="30" class="node-title">4. Отправка в VK API</text>
            <text x="20" y="55" class="node-desc">Гарантированная доставка</text>
            <text x="20" y="73" class="node-desc">сообщения адресату в ЛС.</text>
            <rect x="18" y="95" width="214" height="34" rx="8" fill="#2D1524"/>
            <text x="125" y="116" font-size="12" font-weight="600" fill="#F472B6" text-anchor="middle">Status = SENT</text>
        </g>

        <!-- Нижняя плашка: защита данных и инфраструктура -->
        <g transform="translate(40, 260)">
            <rect x="0" y="0" width="1300" height="125" rx="14" fill="#14141E" stroke="#252538" stroke-width="1"/>
            
            <g transform="translate(25, 20)">
                <text x="0" y="20" font-size="15" font-weight="700" fill="#FFFFFF">Защита от сбоев и санитизация PII</text>
                <text x="0" y="44" class="node-desc">• <tspan fill="#34D399" font-weight="600">Сетевые сбои VK API:</tspan> задача Outbox повторяется с экспоненциальной задержкой (retry до 5 раз). Сообщения не теряются.</text>
                <text x="0" y="66" class="node-desc">• <tspan fill="#F472B6" font-weight="600">Санитизация логов и Sentry:</tspan> SensitiveDataFilter скрывает пароли, токены VK (vk1.a.***) и Bearer-заголовки во всех логах.</text>
                <text x="0" y="88" class="node-desc">• <tspan fill="#38BDF8" font-weight="600">Пул PgBouncer:</tspan> 20 серверных соединений безопасно обслуживают более 100 одновременных веб-запросов и воркеров.</text>
            </g>
        </g>
    </g>
    """)

    # =========================================================================
    # 6. БЛОК 5: TELEGRAM МОНИТОР И ИНФРАСТРУКТУРА (Справа снизу)
    # =========================================================================
    parts.append("""
    <g id="Frame_05_Telegram_Sentinel" transform="translate(1480, 930)">
        <rect x="0" y="0" width="1340" height="420" rx="20" fill="url(#cardGrad)" stroke="#262638" stroke-width="1.5" filter="url(#cardShadow)"/>
        
        <rect x="0" y="0" width="1340" height="60" rx="20" fill="url(#cardHighlight)"/>
        <circle cx="32" cy="30" r="14" fill="#38BDF8" fill-opacity="0.2"/>
        <text x="32" y="35" font-size="14" font-weight="800" fill="#38BDF8" text-anchor="middle">5</text>
        <text x="60" y="36" class="frame-title">Инфраструктура &amp; Telegram Sentinel (Superadmin Monitor)</text>
        <text x="1130" y="36" class="badge-text" fill="#38BDF8">SYSTEM HARDENING</text>

        <g transform="translate(35, 85)">
            <!-- Колонка 1: Telegram Бот -->
            <rect x="0" y="0" width="380" height="300" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="300" rx="3" fill="#0088CC"/>
            <text x="24" y="32" class="node-title">Telegram Monitor Bot (@BotFather)</text>
            <text x="24" y="58" class="node-desc">Выделенный бот только для суперадминистратора.</text>
            <text x="24" y="85" class="node-desc" fill="#7DD3FC">📊 Метрики сервера в реальном времени:</text>
            <text x="24" y="105" class="node-desc">• CPU, RAM, Disk, Uptime сервера</text>
            <text x="24" y="125" class="node-desc">• Статусы всех 9 docker-контейнеров</text>
            <text x="24" y="155" class="node-desc" fill="#34D399">🔄 Быстрые команды управления:</text>
            <text x="24" y="175" class="node-desc">• Рестарт ботов и сервисов на лету</text>
            <text x="24" y="195" class="node-desc">• Создание бэкапа PostgreSQL в 1 клик</text>
            <text x="24" y="215" class="node-desc">• Включение/выключение техработ (Maintenance)</text>
            
            <rect x="24" y="245" width="330" height="34" rx="8" fill="#122538"/>
            <text x="189" y="267" font-size="12" font-weight="700" fill="#38BDF8" text-anchor="middle">Inline кнопки + Telegram Mini App</text>

            <!-- Колонка 2: Docker Engine & Proxy Hardening -->
            <rect x="420" y="0" width="410" height="300" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="300" rx="3" fill="#2496ED"/>
            <text x="444" y="32" class="node-title">Docker Engine &amp; Socket Hardening</text>
            <text x="444" y="58" class="node-desc">Изоляция управления контейнерами на сервере:</text>
            <text x="444" y="85" class="node-desc" fill="#93C5FD">• <tspan font-weight="700">Web-Admin:</tspan> Docker Socket НЕ смонтирован (изолирован).</text>
            <text x="444" y="110" class="node-desc" fill="#93C5FD">• <tspan font-weight="700">TG Monitor:</tspan> монтирование :ro или Socket Proxy.</text>
            <text x="444" y="135" class="node-desc">• Поддержка переменной <tspan class="code-pill">DOCKER_HOST</tspan> по HTTP/TCP.</text>
            <text x="444" y="165" class="node-desc" fill="#F472B6">Резервное копирование БД:</text>
            <text x="444" y="185" class="node-desc">• Горячий pg_dump в изолированный том /var/backups</text>
            <text x="444" y="205" class="node-desc">• Ротация дампов 30 дней и отправка архива в чат TG</text>

            <rect x="444" y="245" width="360" height="34" rx="8" fill="#182E47"/>
            <text x="624" y="267" font-size="12" font-weight="700" fill="#60A5FA" text-anchor="middle">tecnativa/docker-socket-proxy ready</text>

            <!-- Колонка 3: CI/CD & Security Sentinel -->
            <rect x="870" y="0" width="390" height="300" rx="14" fill="#181826" stroke="#2B2B40" stroke-width="1.2"/>
            <rect x="0" y="0" width="6" height="300" rx="3" fill="#10B981"/>
            <text x="894" y="32" class="node-title">CI/CD, Dependabot &amp; Тестирование</text>
            <text x="894" y="58" class="node-desc">Непрерывная проверка надёжности кода:</text>
            <text x="894" y="85" class="node-desc" fill="#6EE7B7">• <tspan font-weight="700">Dependabot:</tspan> еженедельный аудит CVE зависимостей.</text>
            <text x="894" y="110" class="node-desc" fill="#6EE7B7">• <tspan font-weight="700">GitHub Actions CI:</tspan> линтинг, pip-audit, 64+ тестов.</text>
            <text x="894" y="135" class="node-desc" fill="#6EE7B7">• <tspan font-weight="700">Git Secret Scanner:</tspan> аудит истории на утечки ключей.</text>
            <text x="894" y="165" class="node-desc" fill="#FBBF24">• <tspan font-weight="700">Locust Нагрузочные тесты:</tspan></text>
            <text x="894" y="185" class="node-desc">  PeakLoadBurstUser — стресс-тесты пиковых всплесков</text>
            <text x="894" y="205" class="node-desc">  сессии и массовых подач заявок в Outbox.</text>

            <rect x="894" y="245" width="345" height="34" rx="8" fill="#122E22"/>
            <text x="1066" y="267" font-size="12" font-weight="700" fill="#34D399" text-anchor="middle">100% Passing Automated Tests</text>
        </g>
    </g>
    """)

    # =========================================================================
    # 7. МЕЖБЛОЧНЫЕ СВЯЗИ И ПОТОКИ ДАННЫХ (CONNECTORS)
    # =========================================================================
    parts.append("""
    <g id="Connectors" opacity="0.85">
        <!-- Связь: Блок 1 (Команды меню) -> Блок 2 (Студенческие сценарии) -->
        <path d="M 680 500 C 695 500, 695 350, 710 350" class="flow-line" stroke="#FF4D9D" marker-end="url(#arrowPink)"/>
        
        <!-- Связь: Блок 2 (Создание тикета) -> Блок 4 (Outbox двойная запись) -->
        <path d="M 1120 745 C 1120 840, 400 840, 400 930" class="flow-line" stroke="#10B981" marker-end="url(#arrowGreen)"/>

        <!-- Связь: Блок 2 (Тикет создан) -> Блок 3 (Триаж куратора) -->
        <path d="M 1530 400 C 1545 400, 1545 400, 1560 400" class="flow-line" stroke="#B855F6" marker-end="url(#arrowPurple)"/>

        <!-- Связь: Блок 3 (Ответ куратора) -> Блок 4 (Outbox отправка) -->
        <path d="M 1870 745 C 1870 860, 1200 860, 1200 930" class="flow-line" stroke="#00D2FF" marker-end="url(#arrowCyan)"/>
    </g>
    """)

    # =========================================================================
    # 8. НИЖНЯЯ ПАНЕЛЬ С ЛЕГЕНДОЙ (FOOTER & LEGEND)
    # =========================================================================
    parts.append("""
    <g id="Canvas_Footer" transform="translate(60, 1690)">
        <rect x="0" y="0" width="2760" height="70" rx="16" fill="#111118" stroke="#252538" stroke-width="1.2"/>
        
        <!-- Легенда линий и статусов -->
        <g transform="translate(30, 24)">
            <circle cx="10" cy="11" r="7" fill="#0077FF"/>
            <text x="26" y="16" font-size="13" font-weight="600" fill="#E2E8F0">Входящий LongPoll поток</text>

            <circle cx="250" cy="11" r="7" fill="#FF4D9D"/>
            <text x="266" y="16" font-size="13" font-weight="600" fill="#E2E8F0">FSM сценарии создания заявок</text>

            <circle cx="530" cy="11" r="7" fill="#B855F6"/>
            <text x="546" y="16" font-size="13" font-weight="600" fill="#E2E8F0">Кураторский триаж и 2FA</text>

            <circle cx="790" cy="11" r="7" fill="#10B981"/>
            <text x="806" y="16" font-size="13" font-weight="600" fill="#E2E8F0">Transactional Outbox (Гарантия доставки)</text>

            <circle cx="1160" cy="11" r="7" fill="#38BDF8"/>
            <text x="1176" y="16" font-size="13" font-weight="600" fill="#E2E8F0">Telegram мониторинг &amp; Docker</text>
        </g>

        <!-- Авторство и копирайт -->
        <text x="2720" y="40" font-size="13" font-weight="500" fill="#6B7280" text-anchor="end">
            OSS Bot Project • Architecture &amp; Business Logic Board • Designed for Figma Import
        </text>
    </g>
    """)

    return "\n".join(parts)


def main():
    print(f"Generating figma board: {OUTPUT_FILE}...")
    content = create_board()
    svg = SVG_TEMPLATE.format(width=WIDTH, height=HEIGHT, content=content)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(svg)
    file_size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"[OK] File successfully generated: {OUTPUT_FILE} ({file_size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
