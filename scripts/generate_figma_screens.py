"""
Генератор дизайн-макетов страниц веб-панели OSS Bot для Figma.
Создаёт SVG-файлы для каждого экрана в двух вариантах:
- Desktop: 1440x900 px
- Mobile: 390x844 px
А также единый мега-артборд figma_all_screens_board.svg со всеми экранами.
"""

import os
from pathlib import Path

FIGMA_DIR = Path(__file__).resolve().parent.parent / "figma"
DESKTOP_DIR = FIGMA_DIR / "desktop"
MOBILE_DIR = FIGMA_DIR / "mobile"

DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
MOBILE_DIR.mkdir(parents=True, exist_ok=True)

# Общие стили и цвета
CSS_DEFS = """
<defs>
    <linearGradient id="primaryGrad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#FF5EA6" />
        <stop offset="100%" stop-color="#EA2679" />
    </linearGradient>
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#1A1A23" />
        <stop offset="100%" stop-color="#14141C" />
    </linearGradient>
    <linearGradient id="accentGlow" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#FF4D9D" stop-opacity="0.25" />
        <stop offset="100%" stop-color="#EA2679" stop-opacity="0.05" />
    </linearGradient>
    <filter id="pinkGlow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="8" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
    <filter id="cardShadow" x="-5%" y="-5%" width="110%" height="115%">
        <feDropShadow dx="0" dy="6" stdDeviation="12" flood-color="#000000" flood-opacity="0.45" />
    </filter>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&amp;display=swap');
        text { font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    </style>
</defs>
"""

# Вспомогательные функции для Desktop
def desktop_shell(title: str, active_item: str, inner_content: str) -> str:
    menu_items = [
        ("dashboard", "Обзор", "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
        ("tickets", "Заявки", "M2 4h20v16H2z"),
        ("knowledge", "База знаний", "M4 19.5A2.5 2.5 0 0 1 6.5 17H20"),
        ("faq", "Частые вопросы", "M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z"),
        ("events", "Мероприятия", "M12 2a10 10 0 1 0 10 10"),
        ("departments", "Отделы", "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5"),
        ("admins", "Администраторы", "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"),
        ("logs", "Журнал аудита", "M8 6h13M8 12h13M8 18h13"),
        ("settings", "Настройки", "M12 2a10 10 0 1 0 10 10"),
    ]

    sidebar_links = []
    y = 150
    for key, label, icon_path in menu_items:
        is_active = (key == active_item)
        bg = ' fill="url(#accentGlow)" stroke="#FF4D9D" stroke-width="1"' if is_active else ' fill="transparent"'
        text_color = "#FFFFFF" if is_active else "#9E9EA8"
        font_weight = "700" if is_active else "500"
        indicator = f'<rect x="0" y="{y}" width="4" height="40" rx="2" fill="#FF4D9D"/>' if is_active else ''

        sidebar_links.append(f"""
        <g id="Nav-{key}" cursor="pointer">
            <rect x="12" y="{y}" width="226" height="40" rx="8"{bg}/>
            {indicator}
            <path d="{icon_path}" stroke="{text_color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none" transform="translate(28, {y+10}) scale(0.8)"/>
            <text x="56" y="{y+25}" fill="{text_color}" font-size="14" font-weight="{font_weight}">{label}</text>
        </g>
        """)
        y += 48

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 900" width="1440" height="900" fill="none">
{CSS_DEFS}
<!-- Фон рабочей области -->
<rect width="1440" height="900" fill="#08080C"/>
<circle cx="200" cy="150" r="300" fill="#FF4D9D" fill-opacity="0.06"/>
<circle cx="1200" cy="700" r="350" fill="#D65DB1" fill-opacity="0.04"/>

<!-- Боковое меню (Sidebar) -->
<g id="Sidebar">
    <rect x="0" y="0" width="250" height="900" fill="#121217" stroke="#1F1F2A" stroke-width="1"/>
    <!-- Логотип и бренд -->
    <rect x="20" y="24" width="40" height="40" rx="10" fill="url(#primaryGrad)"/>
    <text x="32" y="50" fill="#FFFFFF" font-size="18" font-weight="800">OB</text>
    <text x="70" y="42" fill="#FFFFFF" font-size="16" font-weight="700">OSS Bot</text>
    <text x="70" y="58" fill="#7D7D8F" font-size="11">Панель управления v0.8.4.1</text>
    
    <!-- Профиль пользователя -->
    <rect x="16" y="80" width="218" height="54" rx="10" fill="#181822" stroke="#252533" stroke-width="1"/>
    <circle cx="42" cy="107" r="16" fill="#FF4D9D" fill-opacity="0.2"/>
    <text x="36" y="113" fill="#FF4D9D" font-size="14" font-weight="700">A</text>
    <text x="68" y="102" fill="#FFFFFF" font-size="13" font-weight="600">admin</text>
    <rect x="68" y="108" width="80" height="18" rx="4" fill="#FF4D9D" fill-opacity="0.15"/>
    <text x="74" y="121" fill="#FF4D9D" font-size="10" font-weight="700">Суперадмин</text>

    <!-- Ссылки меню -->
    {''.join(sidebar_links)}

    <!-- Нижняя кнопка выхода -->
    <g id="Logout-Button" transform="translate(16, 836)" cursor="pointer">
        <rect width="218" height="42" rx="8" fill="#1A1A24" stroke="#262636" stroke-width="1"/>
        <text x="56" y="26" fill="#B8B8C8" font-size="13" font-weight="600">Выйти из системы</text>
    </g>
</g>

<!-- Верхний хедер (Header) -->
<g id="Header">
    <rect x="250" y="0" width="1190" height="70" fill="#121217" stroke="#1F1F2A" stroke-width="1"/>
    <text x="282" y="42" fill="#FFFFFF" font-size="20" font-weight="700">{title}</text>
    
    <!-- Поиск в шапке -->
    <rect x="680" y="16" width="320" height="38" rx="8" fill="#181822" stroke="#252533" stroke-width="1"/>
    <text x="715" y="40" fill="#7D7D8F" font-size="13">Быстрый поиск по системе...</text>
    
    <!-- Иконка темы и профиль -->
    <circle cx="1030" cy="35" r="18" fill="#1A1A24" stroke="#252533" stroke-width="1"/>
    <text x="1023" y="41" fill="#FF4D9D" font-size="16">☼</text>
    
    <rect x="1065" y="18" width="150" height="34" rx="8" fill="#1A1A24" stroke="#252533" stroke-width="1"/>
    <circle cx="1082" cy="35" r="5" fill="#10B981"/>
    <text x="1095" y="40" fill="#E2E8F0" font-size="12" font-weight="600">NetBird: Online</text>
</g>

<!-- Основной контент страницы -->
<g id="MainContent" transform="translate(250, 70)">
{inner_content}
</g>
</svg>
"""

# Вспомогательные функции для Mobile (390x844)
def mobile_shell(title: str, active_tab: str, inner_content: str) -> str:
    tabs = [
        ("dashboard", "Обзор", "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
        ("tickets", "Заявки", "M2 4h20v16H2z"),
        ("faq", "FAQ", "M12 2a10 10 0 1 0 10 10"),
        ("settings", "Ещё", "M12 2a10 10 0 1 0 10 10"),
    ]
    
    tab_items = []
    x = 0
    step = 390 / len(tabs)
    for key, label, icon in tabs:
        is_act = (key == active_tab)
        color = "#FF4D9D" if is_act else "#7D7D8F"
        fw = "700" if is_act else "500"
        tab_items.append(f"""
        <g id="Tab-{key}" transform="translate({x}, 0)">
            <text x="{step/2}" y="42" fill="{color}" font-size="10" font-weight="{fw}" text-anchor="middle">{label}</text>
            <circle cx="{step/2}" cy="22" r="12" fill="{color}" fill-opacity="0.12"/>
            <circle cx="{step/2}" cy="22" r="4" fill="{color}"/>
        </g>
        """)
        x += step

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 390 844" width="390" height="844" fill="none">
{CSS_DEFS}
<!-- Корпус смартфона и фон -->
<rect width="390" height="844" rx="40" fill="#08080C"/>
<rect x="0" y="0" width="390" height="844" rx="40" stroke="#252533" stroke-width="2"/>

<!-- Динамический остров / Dynamic Island -->
<rect x="135" y="10" width="120" height="30" rx="15" fill="#000000"/>
<circle cx="230" cy="25" r="5" fill="#14141C"/>

<!-- Мобильная шапка -->
<g id="MobileHeader">
    <rect x="0" y="48" width="390" height="60" fill="#121217" stroke="#1F1F2A" stroke-width="1"/>
    <!-- Кнопка бургер меню -->
    <rect x="16" y="62" width="34" height="34" rx="8" fill="#1A1A24"/>
    <text x="25" y="84" fill="#FFFFFF" font-size="16">☰</text>
    
    <!-- Бренд -->
    <rect x="60" y="64" width="28" height="28" rx="6" fill="url(#primaryGrad)"/>
    <text x="68" y="83" fill="#FFFFFF" font-size="12" font-weight="800">OB</text>
    <text x="96" y="83" fill="#FFFFFF" font-size="15" font-weight="700">{title}</text>
    
    <!-- Аватарка -->
    <circle cx="355" cy="78" r="14" fill="#FF4D9D" fill-opacity="0.2"/>
    <text x="350" y="83" fill="#FF4D9D" font-size="12" font-weight="700">A</text>
</g>

<!-- Мобильный контент с прокруткой -->
<g id="MobileContent" transform="translate(0, 114)">
{inner_content}
</g>

<!-- Нижняя панель навигации (Tabbar) -->
<g id="MobileTabBar" transform="translate(0, 774)">
    <rect width="390" height="70" fill="#121217" stroke="#1F1F2A" stroke-width="1"/>
    {''.join(tab_items)}
</g>

<!-- Индикатор жеста Home -->
<rect x="125" y="832" width="140" height="4" rx="2" fill="#4B4B5A"/>
</svg>
"""

# =========================================================================
# Генерация конкретных страниц
# =========================================================================

def generate_login():
    # 1. Login Desktop
    desk = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 900" width="1440" height="900" fill="none">
{CSS_DEFS}
<rect width="1440" height="900" fill="#08080C"/>
<circle cx="720" cy="450" r="400" fill="#FF4D9D" fill-opacity="0.08"/>

<!-- Карточка авторизации -->
<g id="LoginCard" transform="translate(480, 180)" filter="url(#cardShadow)">
    <rect width="480" height="540" rx="20" fill="#121217" stroke="#252533" stroke-width="1"/>
    
    <!-- Логотип -->
    <rect x="200" y="44" width="80" height="80" rx="20" fill="url(#primaryGrad)" filter="url(#pinkGlow)"/>
    <text x="222" y="94" fill="#FFFFFF" font-size="34" font-weight="800">OB</text>
    
    <text x="240" y="160" fill="#FFFFFF" font-size="24" font-weight="700" text-anchor="middle">Панель управления</text>
    <text x="240" y="185" fill="#7D7D8F" font-size="14" text-anchor="middle">Единая система приёма обращений студентов</text>
    
    <!-- Поле логин -->
    <text x="50" y="235" fill="#B8B8C8" font-size="13" font-weight="600">Имя пользователя</text>
    <rect x="50" y="245" width="380" height="48" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
    <text x="68" y="275" fill="#FFFFFF" font-size="14">admin</text>
    
    <!-- Поле пароль -->
    <text x="50" y="325" fill="#B8B8C8" font-size="13" font-weight="600">Пароль доступа</text>
    <rect x="50" y="335" width="380" height="48" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
    <text x="68" y="367" fill="#7D7D8F" font-size="18">••••••••••••••••</text>
    
    <!-- Кнопка Войти -->
    <g id="SubmitButton" cursor="pointer">
        <rect x="50" y="415" width="380" height="50" rx="10" fill="url(#primaryGrad)" filter="url(#pinkGlow)"/>
        <text x="240" y="446" fill="#FFFFFF" font-size="15" font-weight="700" text-anchor="middle">Войти в панель</text>
    </g>
    
    <!-- Безопасность и версия -->
    <text x="240" y="495" fill="#7D7D8F" font-size="12" text-anchor="middle">Защищено 2FA • Argon2id • Версия 0.8.4.1</text>
</g>
</svg>"""

    # 1. Login Mobile
    mob = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 390 844" width="390" height="844" fill="none">
{CSS_DEFS}
<rect width="390" height="844" rx="40" fill="#08080C"/>
<circle cx="195" cy="300" r="180" fill="#FF4D9D" fill-opacity="0.08"/>

<!-- Dynamic Island -->
<rect x="135" y="10" width="120" height="30" rx="15" fill="#000000"/>

<g id="MobileLogin" transform="translate(24, 120)">
    <rect x="131" y="20" width="80" height="80" rx="20" fill="url(#primaryGrad)"/>
    <text x="153" y="70" fill="#FFFFFF" font-size="34" font-weight="800">OB</text>
    
    <text x="171" y="135" fill="#FFFFFF" font-size="22" font-weight="700" text-anchor="middle">OSS Bot</text>
    <text x="171" y="160" fill="#7D7D8F" font-size="13" text-anchor="middle">Вход в панель управления</text>
    
    <!-- Поля -->
    <text x="0" y="210" fill="#B8B8C8" font-size="13" font-weight="600">Логин</text>
    <rect x="0" y="220" width="342" height="50" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
    <text x="16" y="252" fill="#FFFFFF" font-size="14">admin</text>
    
    <text x="0" y="300" fill="#B8B8C8" font-size="13" font-weight="600">Пароль</text>
    <rect x="0" y="310" width="342" height="50" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
    <text x="16" y="342" fill="#7D7D8F" font-size="18">••••••••••••••••</text>
    
    <!-- Кнопка -->
    <rect x="0" y="390" width="342" height="52" rx="12" fill="url(#primaryGrad)"/>
    <text x="171" y="422" fill="#FFFFFF" font-size="15" font-weight="700" text-anchor="middle">Продолжить</text>
    
    <text x="171" y="475" fill="#7D7D8F" font-size="12" text-anchor="middle">Защищённый контур v0.8.4.1</text>
</g>
</svg>"""

    (DESKTOP_DIR / "01_login_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "01_login_mobile.svg").write_text(mob, encoding="utf-8")

def generate_2fa():
    # 2. 2FA Desktop
    desk = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 900" width="1440" height="900" fill="none">
{CSS_DEFS}
<rect width="1440" height="900" fill="#08080C"/>
<circle cx="720" cy="450" r="400" fill="#FF4D9D" fill-opacity="0.08"/>

<g id="2FACard" transform="translate(480, 200)" filter="url(#cardShadow)">
    <rect width="480" height="500" rx="20" fill="#121217" stroke="#252533" stroke-width="1"/>
    
    <circle cx="240" cy="70" r="32" fill="#FF4D9D" fill-opacity="0.15"/>
    <text x="228" y="78" fill="#FF4D9D" font-size="24">🔒</text>
    
    <text x="240" y="135" fill="#FFFFFF" font-size="22" font-weight="700" text-anchor="middle">Двухфакторная проверка</text>
    <text x="240" y="160" fill="#7D7D8F" font-size="13" text-anchor="middle">Мы отправили 6-значный код в личные сообщения VK</text>
    
    <!-- 6 ячеек кода -->
    <g transform="translate(60, 200)">
        <rect x="0" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="18" y="40" fill="#FFFFFF" font-size="24" font-weight="700">4</text>
        
        <rect x="62" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="80" y="40" fill="#FFFFFF" font-size="24" font-weight="700">8</text>
        
        <rect x="124" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="142" y="40" fill="#FFFFFF" font-size="24" font-weight="700">1</text>
        
        <rect x="186" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="204" y="40" fill="#FFFFFF" font-size="24" font-weight="700">9</text>
        
        <rect x="248" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
        <text x="266" y="40" fill="#FFFFFF" font-size="24" font-weight="700">2</text>
        
        <rect x="310" y="0" width="50" height="60" rx="10" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
        <text x="328" y="40" fill="#FFFFFF" font-size="24" font-weight="700">0</text>
    </g>
    
    <text x="240" y="300" fill="#D65DB1" font-size="13" font-weight="600" text-anchor="middle">⏱ Срок действия кода: 04:45</text>
    
    <!-- Кнопка подтвердить -->
    <rect x="50" y="340" width="380" height="50" rx="10" fill="url(#primaryGrad)"/>
    <text x="240" y="371" fill="#FFFFFF" font-size="15" font-weight="700" text-anchor="middle">Подтвердить вход</text>
    
    <text x="240" y="425" fill="#7D7D8F" font-size="13" text-anchor="middle">Не пришёл код? Отправить повторно через 30 сек.</text>
</g>
</svg>"""

    # 2. 2FA Mobile
    mob = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 390 844" width="390" height="844" fill="none">
{CSS_DEFS}
<rect width="390" height="844" rx="40" fill="#08080C"/>
<rect x="135" y="10" width="120" height="30" rx="15" fill="#000000"/>

<g id="Mobile2FA" transform="translate(24, 150)">
    <circle cx="171" cy="40" r="30" fill="#FF4D9D" fill-opacity="0.15"/>
    <text x="160" y="48" fill="#FF4D9D" font-size="22">🔒</text>
    
    <text x="171" y="105" fill="#FFFFFF" font-size="20" font-weight="700" text-anchor="middle">Код безопасности</text>
    <text x="171" y="130" fill="#7D7D8F" font-size="12" text-anchor="middle">Введите 6 цифр из сообщения ВКонтакте</text>
    
    <!-- 6 ячеек -->
    <g transform="translate(15, 170)">
        <rect x="0" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="16" y="36" fill="#FFFFFF" font-size="20" font-weight="700">4</text>
        <rect x="54" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="70" y="36" fill="#FFFFFF" font-size="20" font-weight="700">8</text>
        <rect x="108" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="124" y="36" fill="#FFFFFF" font-size="20" font-weight="700">1</text>
        <rect x="162" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#FF4D9D" stroke-width="2"/>
        <text x="178" y="36" fill="#FFFFFF" font-size="20" font-weight="700">9</text>
        <rect x="216" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
        <text x="232" y="36" fill="#FFFFFF" font-size="20" font-weight="700">2</text>
        <rect x="270" y="0" width="46" height="54" rx="8" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
        <text x="286" y="36" fill="#FFFFFF" font-size="20" font-weight="700">0</text>
    </g>
    
    <text x="171" y="260" fill="#D65DB1" font-size="12" font-weight="600" text-anchor="middle">⏱ Истекает через 04:45</text>
    
    <rect x="0" y="300" width="342" height="52" rx="12" fill="url(#primaryGrad)"/>
    <text x="171" y="332" fill="#FFFFFF" font-size="15" font-weight="700" text-anchor="middle">Подтвердить</text>
</g>
</svg>"""

    (DESKTOP_DIR / "02_2fa_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "02_2fa_mobile.svg").write_text(mob, encoding="utf-8")

def generate_dashboard():
    # 3. Dashboard Desktop
    content = """
    <!-- 4 карточки статистики -->
    <g id="StatsRow" transform="translate(32, 28)">
        <!-- Карточка 1 -->
        <rect x="0" y="0" width="260" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="20" y="35" fill="#7D7D8F" font-size="13" font-weight="600">Всего обращений</text>
        <text x="20" y="75" fill="#FFFFFF" font-size="30" font-weight="800">1,248</text>
        <rect x="180" y="20" width="60" height="22" rx="6" fill="#10B981" fill-opacity="0.15"/>
        <text x="190" y="35" fill="#10B981" font-size="11" font-weight="700">+12.4%</text>

        <!-- Карточка 2 -->
        <rect x="286" y="0" width="260" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="306" y="35" fill="#7D7D8F" font-size="13" font-weight="600">В работе сейчас</text>
        <text x="306" y="75" fill="#FF4D9D" font-size="30" font-weight="800">14</text>
        <circle cx="510" cy="30" r="6" fill="#FF4D9D"/>

        <!-- Карточка 3 -->
        <rect x="572" y="0" width="260" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="592" y="35" fill="#7D7D8F" font-size="13" font-weight="600">Решено сегодня</text>
        <text x="592" y="75" fill="#10B981" font-size="30" font-weight="800">38</text>
        <text x="592" y="95" fill="#7D7D8F" font-size="11">96% без повторных запросов</text>

        <!-- Карточка 4 -->
        <rect x="858" y="0" width="260" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="878" y="35" fill="#7D7D8F" font-size="13" font-weight="600">Ср. время ответа</text>
        <text x="878" y="75" fill="#FFFFFF" font-size="30" font-weight="800">18 мин</text>
        <text x="878" y="95" fill="#10B981" font-size="11">↓ на 4 мин быстрее нормы</text>
    </g>

    <!-- График и аналитика -->
    <g id="ChartAndActivity" transform="translate(32, 160)">
        <!-- График активности -->
        <rect x="0" y="0" width="700" height="280" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="24" y="35" fill="#FFFFFF" font-size="16" font-weight="700">Динамика обращений за неделю</text>
        
        <!-- Столбцы графика -->
        <g transform="translate(40, 230)">
            <rect x="20" y="-120" width="40" height="120" rx="6" fill="#FF4D9D" fill-opacity="0.8"/>
            <text x="32" y="20" fill="#7D7D8F" font-size="12">Пн</text>
            <rect x="110" y="-160" width="40" height="160" rx="6" fill="#FF4D9D" fill-opacity="0.8"/>
            <text x="122" y="20" fill="#7D7D8F" font-size="12">Вт</text>
            <rect x="200" y="-190" width="40" height="190" rx="6" fill="url(#primaryGrad)"/>
            <text x="212" y="20" fill="#FFFFFF" font-size="12" font-weight="700">Ср</text>
            <rect x="290" y="-140" width="40" height="140" rx="6" fill="#FF4D9D" fill-opacity="0.8"/>
            <text x="302" y="20" fill="#7D7D8F" font-size="12">Чт</text>
            <rect x="380" y="-110" width="40" height="110" rx="6" fill="#FF4D9D" fill-opacity="0.8"/>
            <text x="392" y="20" fill="#7D7D8F" font-size="12">Пт</text>
            <rect x="470" y="-50" width="40" height="50" rx="6" fill="#2E2E3E"/>
            <text x="482" y="20" fill="#7D7D8F" font-size="12">Сб</text>
            <rect x="560" y="-30" width="40" height="30" rx="6" fill="#2E2E3E"/>
            <text x="572" y="20" fill="#7D7D8F" font-size="12">Вс</text>
        </g>

        <!-- Нагрузка по отделам -->
        <rect x="726" y="0" width="392" height="280" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="746" y="35" fill="#FFFFFF" font-size="16" font-weight="700">Топ отделов по нагрузке</text>
        
        <text x="746" y="75" fill="#B8B8C8" font-size="13">Учебный отдел (42%)</text>
        <rect x="746" y="85" width="350" height="10" rx="5" fill="#20202C"/>
        <rect x="746" y="85" width="147" height="10" rx="5" fill="#FF4D9D"/>

        <text x="746" y="125" fill="#B8B8C8" font-size="13">Стипендиальная комиссия (28%)</text>
        <rect x="746" y="135" width="350" height="10" rx="5" fill="#20202C"/>
        <rect x="746" y="135" width="98" height="10" rx="5" fill="#D65DB1"/>

        <text x="746" y="175" fill="#B8B8C8" font-size="13">Общежития и заселение (18%)</text>
        <rect x="746" y="185" width="350" height="10" rx="5" fill="#20202C"/>
        <rect x="746" y="185" width="63" height="10" rx="5" fill="#8B5CF6"/>
    </g>

    <!-- Таблица последних обращений -->
    <g id="RecentTickets" transform="translate(32, 460)">
        <rect x="0" y="0" width="1118" height="340" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="24" y="35" fill="#FFFFFF" font-size="16" font-weight="700">Последние обращения студентов</text>
        
        <!-- Шапка таблицы -->
        <rect x="20" y="55" width="1078" height="36" rx="6" fill="#181822"/>
        <text x="36" y="78" fill="#7D7D8F" font-size="12" font-weight="700">ID</text>
        <text x="100" y="78" fill="#7D7D8F" font-size="12" font-weight="700">ТЕМА ОБРАЩЕНИЯ</text>
        <text x="440" y="78" fill="#7D7D8F" font-size="12" font-weight="700">СТУДЕНТ</text>
        <text x="650" y="78" fill="#7D7D8F" font-size="12" font-weight="700">ОТДЕЛ</text>
        <text x="840" y="78" fill="#7D7D8F" font-size="12" font-weight="700">СТАТУС</text>
        <text x="990" y="78" fill="#7D7D8F" font-size="12" font-weight="700">ДЕЙСТВИЕ</text>

        <!-- Строка 1 -->
        <text x="36" y="125" fill="#FF4D9D" font-size="13" font-weight="700">#1042</text>
        <text x="100" y="125" fill="#FFFFFF" font-size="13" font-weight="600">Заявление на материальную помощь к сессии</text>
        <text x="440" y="125" fill="#B8B8C8" font-size="13">Анна Смирнова (ИВТ-21)</text>
        <text x="650" y="125" fill="#B8B8C8" font-size="13">Профком</text>
        <rect x="840" y="108" width="80" height="24" rx="6" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="852" y="124" fill="#FF4D9D" font-size="11" font-weight="700">В работе</text>
        <rect x="990" y="108" width="70" height="24" rx="6" fill="#252533"/>
        <text x="1002" y="124" fill="#FFFFFF" font-size="11" font-weight="600">Открыть</text>

        <!-- Строка 2 -->
        <text x="36" y="175" fill="#FF4D9D" font-size="13" font-weight="700">#1041</text>
        <text x="100" y="175" fill="#FFFFFF" font-size="13" font-weight="600">Пересдача задолженности по дискретной математике</text>
        <text x="440" y="175" fill="#B8B8C8" font-size="13">Максим Романов (ПМИ-3)</text>
        <text x="650" y="175" fill="#B8B8C8" font-size="13">Учебный отдел</text>
        <rect x="840" y="158" width="70" height="24" rx="6" fill="#D65DB1" fill-opacity="0.15"/>
        <text x="852" y="174" fill="#D65DB1" font-size="11" font-weight="700">Новый</text>
        <rect x="990" y="158" width="70" height="24" rx="6" fill="#252533"/>
        <text x="1002" y="174" fill="#FFFFFF" font-size="11" font-weight="600">Открыть</text>

        <!-- Строка 3 -->
        <text x="36" y="225" fill="#FF4D9D" font-size="13" font-weight="700">#1040</text>
        <text x="100" y="225" fill="#FFFFFF" font-size="13" font-weight="600">Выдача справки об обучении для военкомата</text>
        <text x="440" y="225" fill="#B8B8C8" font-size="13">Денис Ковалёв (КТ-11)</text>
        <text x="650" y="225" fill="#B8B8C8" font-size="13">Деканат</text>
        <rect x="840" y="208" width="75" height="24" rx="6" fill="#10B981" fill-opacity="0.15"/>
        <text x="852" y="224" fill="#10B981" font-size="11" font-weight="700">Решён</text>
        <rect x="990" y="208" width="70" height="24" rx="6" fill="#252533"/>
        <text x="1002" y="224" fill="#FFFFFF" font-size="11" font-weight="600">Открыть</text>
    </g>
    """
    desk = desktop_shell("Обзор системы", "dashboard", content)

    # 3. Dashboard Mobile
    mob_content = """
    <!-- 2x2 метрики -->
    <g transform="translate(16, 12)">
        <rect x="0" y="0" width="170" height="84" rx="10" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="14" y="26" fill="#7D7D8F" font-size="11">Всего заявок</text>
        <text x="14" y="58" fill="#FFFFFF" font-size="22" font-weight="800">1,248</text>
        <text x="14" y="74" fill="#10B981" font-size="10">+12% за неделю</text>

        <rect x="188" y="0" width="170" height="84" rx="10" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="202" y="26" fill="#7D7D8F" font-size="11">В работе</text>
        <text x="202" y="58" fill="#FF4D9D" font-size="22" font-weight="800">14</text>
        <text x="202" y="74" fill="#D65DB1" font-size="10">требуют ответа</text>
    </g>

    <!-- Заголовок списка -->
    <text x="16" y="125" fill="#FFFFFF" font-size="15" font-weight="700">Новые обращения</text>
    
    <!-- Карточка 1 -->
    <g transform="translate(16, 140)">
        <rect width="358" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#FF4D9D" font-size="12" font-weight="700">#1042 • Профком</text>
        <text x="16" y="50" fill="#FFFFFF" font-size="13" font-weight="600">Заявление на материальную помощь</text>
        <text x="16" y="70" fill="#7D7D8F" font-size="12">Анна Смирнова (ИВТ-21)</text>
        <rect x="16" y="82" width="70" height="20" rx="4" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="24" y="96" fill="#FF4D9D" font-size="10" font-weight="700">В работе</text>
        <text x="310" y="96" fill="#7D7D8F" font-size="11">12 мин</text>
    </g>

    <!-- Карточка 2 -->
    <g transform="translate(16, 262)">
        <rect width="358" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#FF4D9D" font-size="12" font-weight="700">#1041 • Учебный отдел</text>
        <text x="16" y="50" fill="#FFFFFF" font-size="13" font-weight="600">Пересдача задолженности</text>
        <text x="16" y="70" fill="#7D7D8F" font-size="12">Максим Романов (ПМИ-3)</text>
        <rect x="16" y="82" width="60" height="20" rx="4" fill="#D65DB1" fill-opacity="0.15"/>
        <text x="24" y="96" fill="#D65DB1" font-size="10" font-weight="700">Новый</text>
        <text x="310" y="96" fill="#7D7D8F" font-size="11">25 мин</text>
    </g>

    <!-- Карточка 3 -->
    <g transform="translate(16, 384)">
        <rect width="358" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#FF4D9D" font-size="12" font-weight="700">#1040 • Деканат</text>
        <text x="16" y="50" fill="#FFFFFF" font-size="13" font-weight="600">Справка об обучении в военкомат</text>
        <text x="16" y="70" fill="#7D7D8F" font-size="12">Денис Ковалёв (КТ-11)</text>
        <rect x="16" y="82" width="65" height="20" rx="4" fill="#10B981" fill-opacity="0.15"/>
        <text x="24" y="96" fill="#10B981" font-size="10" font-weight="700">Решён</text>
        <text x="310" y="96" fill="#7D7D8F" font-size="11">1 час</text>
    </g>
    """
    mob = mobile_shell("Обзор", "dashboard", mob_content)

    (DESKTOP_DIR / "03_dashboard_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "03_dashboard_mobile.svg").write_text(mob, encoding="utf-8")

def generate_tickets():
    # 4. Tickets Desktop
    content = """
    <!-- Панель фильтров -->
    <g id="TicketFilters" transform="translate(32, 24)">
        <rect width="1118" height="64" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        
        <!-- Вкладки статусов -->
        <rect x="16" y="14" width="70" height="36" rx="8" fill="url(#primaryGrad)"/>
        <text x="32" y="37" fill="#FFFFFF" font-size="13" font-weight="700">Все 48</text>

        <rect x="94" y="14" width="80" height="36" rx="8" fill="#1A1A24"/>
        <text x="110" y="37" fill="#B8B8C8" font-size="13">Новые 6</text>

        <rect x="182" y="14" width="94" height="36" rx="8" fill="#1A1A24"/>
        <text x="198" y="37" fill="#B8B8C8" font-size="13">В работе 14</text>

        <rect x="284" y="14" width="94" height="36" rx="8" fill="#1A1A24"/>
        <text x="300" y="37" fill="#B8B8C8" font-size="13">Решённые 28</text>

        <!-- Поиск -->
        <rect x="740" y="14" width="230" height="36" rx="8" fill="#181822" stroke="#2A2A38" stroke-width="1"/>
        <text x="760" y="37" fill="#7D7D8F" font-size="12">Поиск по ФИО, номеру...</text>

        <!-- Кнопка экспорта -->
        <rect x="982" y="14" width="120" height="36" rx="8" fill="#252533"/>
        <text x="1005" y="37" fill="#FFFFFF" font-size="12" font-weight="600">📥 Экспорт CSV</text>
    </g>

    <!-- Таблица заявок -->
    <g id="TicketsTable" transform="translate(32, 108)">
        <rect width="1118" height="680" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        
        <!-- Заголовок -->
        <rect x="16" y="16" width="1086" height="40" rx="8" fill="#181822"/>
        <text x="36" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ID</text>
        <text x="96" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ТЕМА</text>
        <text x="440" y="41" fill="#7D7D8F" font-size="12" font-weight="700">СТУДЕНТ / ГРУППА</text>
        <text x="660" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ОТДЕЛ</text>
        <text x="820" y="41" fill="#7D7D8F" font-size="12" font-weight="700">СТАТУС</text>
        <text x="960" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ДЕЙСТВИЯ</text>

        <!-- 5 строк тикетов -->
        <g transform="translate(0, 70)">
            <text x="36" y="24" fill="#FF4D9D" font-size="13" font-weight="700">#1042</text>
            <text x="96" y="24" fill="#FFFFFF" font-size="13" font-weight="600">Материальная помощь</text>
            <text x="440" y="24" fill="#B8B8C8" font-size="13">Анна Смирнова (ИВТ-21)</text>
            <text x="660" y="24" fill="#B8B8C8" font-size="13">Профком</text>
            <rect x="820" y="8" width="80" height="24" rx="6" fill="#FF4D9D" fill-opacity="0.15"/>
            <text x="832" y="24" fill="#FF4D9D" font-size="11" font-weight="700">В работе</text>
            <rect x="960" y="8" width="80" height="26" rx="6" fill="url(#primaryGrad)"/>
            <text x="980" y="25" fill="#FFFFFF" font-size="11" font-weight="700">Ответить</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>

        <g transform="translate(0, 126)">
            <text x="36" y="24" fill="#FF4D9D" font-size="13" font-weight="700">#1041</text>
            <text x="96" y="24" fill="#FFFFFF" font-size="13" font-weight="600">Пересдача задолженности</text>
            <text x="440" y="24" fill="#B8B8C8" font-size="13">Максим Романов (ПМИ-3)</text>
            <text x="660" y="24" fill="#B8B8C8" font-size="13">Учебный отдел</text>
            <rect x="820" y="8" width="70" height="24" rx="6" fill="#D65DB1" fill-opacity="0.15"/>
            <text x="832" y="24" fill="#D65DB1" font-size="11" font-weight="700">Новый</text>
            <rect x="960" y="8" width="80" height="26" rx="6" fill="#252533"/>
            <text x="980" y="25" fill="#FFFFFF" font-size="11" font-weight="700">Открыть</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>

        <g transform="translate(0, 182)">
            <text x="36" y="24" fill="#FF4D9D" font-size="13" font-weight="700">#1040</text>
            <text x="96" y="24" fill="#FFFFFF" font-size="13" font-weight="600">Справка в военкомат</text>
            <text x="440" y="24" fill="#B8B8C8" font-size="13">Денис Ковалёв (КТ-11)</text>
            <text x="660" y="24" fill="#B8B8C8" font-size="13">Деканат</text>
            <rect x="820" y="8" width="75" height="24" rx="6" fill="#10B981" fill-opacity="0.15"/>
            <text x="832" y="24" fill="#10B981" font-size="11" font-weight="700">Решён</text>
            <rect x="960" y="8" width="80" height="26" rx="6" fill="#252533"/>
            <text x="980" y="25" fill="#FFFFFF" font-size="11" font-weight="700">Открыть</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>
    </g>
    """
    desk = desktop_shell("Обращения студентов", "tickets", content)

    # 4. Tickets Mobile
    mob_content = """
    <!-- Чипы фильтрации -->
    <g transform="translate(16, 10)">
        <rect x="0" y="0" width="60" height="32" rx="16" fill="url(#primaryGrad)"/>
        <text x="18" y="20" fill="#FFFFFF" font-size="12" font-weight="700">Все 48</text>

        <rect x="68" y="0" width="70" height="32" rx="16" fill="#181822" stroke="#252533" stroke-width="1"/>
        <text x="82" y="20" fill="#B8B8C8" font-size="12">Новые 6</text>

        <rect x="146" y="0" width="85" height="32" rx="16" fill="#181822" stroke="#252533" stroke-width="1"/>
        <text x="158" y="20" fill="#B8B8C8" font-size="12">В работе 14</text>

        <rect x="238" y="0" width="85" height="32" rx="16" fill="#181822" stroke="#252533" stroke-width="1"/>
        <text x="250" y="20" fill="#B8B8C8" font-size="12">Решены 28</text>
    </g>

    <!-- Поиск -->
    <rect x="16" y="52" width="358" height="40" rx="10" fill="#121217" stroke="#252533" stroke-width="1"/>
    <text x="36" y="77" fill="#7D7D8F" font-size="13">Поиск по номеру или ФИО...</text>

    <!-- Список тикетов -->
    <g transform="translate(16, 104)">
        <rect width="358" height="115" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="26" fill="#FF4D9D" font-size="13" font-weight="700">#1042</text>
        <text x="65" y="26" fill="#7D7D8F" font-size="12">• Профком студентов</text>
        <text x="16" y="48" fill="#FFFFFF" font-size="14" font-weight="600">Заявление на материальную помощь</text>
        <text x="16" y="70" fill="#B8B8C8" font-size="12">Анна Смирнова (ИВТ-21)</text>
        <rect x="16" y="82" width="75" height="22" rx="6" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="24" y="97" fill="#FF4D9D" font-size="11" font-weight="700">В работе</text>
        <rect x="260" y="80" width="85" height="26" rx="6" fill="url(#primaryGrad)"/>
        <text x="276" y="97" fill="#FFFFFF" font-size="11" font-weight="700">Ответить</text>
    </g>

    <g transform="translate(16, 230)">
        <rect width="358" height="115" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="26" fill="#FF4D9D" font-size="13" font-weight="700">#1041</text>
        <text x="65" y="26" fill="#7D7D8F" font-size="12">• Учебный отдел</text>
        <text x="16" y="48" fill="#FFFFFF" font-size="14" font-weight="600">Пересдача задолженности по матану</text>
        <text x="16" y="70" fill="#B8B8C8" font-size="12">Максим Романов (ПМИ-3)</text>
        <rect x="16" y="82" width="65" height="22" rx="6" fill="#D65DB1" fill-opacity="0.15"/>
        <text x="24" y="97" fill="#D65DB1" font-size="11" font-weight="700">Новый</text>
        <rect x="260" y="80" width="85" height="26" rx="6" fill="#252533"/>
        <text x="278" y="97" fill="#FFFFFF" font-size="11" font-weight="700">Открыть</text>
    </g>
    """
    mob = mobile_shell("Заявки", "tickets", mob_content)

    (DESKTOP_DIR / "04_tickets_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "04_tickets_mobile.svg").write_text(mob, encoding="utf-8")

def generate_ticket_detail():
    # 5. Ticket Detail Desktop
    content = """
    <!-- Верхний заголовок тикета -->
    <g id="TicketHeader" transform="translate(32, 20)">
        <rect width="1118" height="70" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="24" y="36" fill="#FF4D9D" font-size="16" font-weight="800">#1042</text>
        <text x="80" y="36" fill="#FFFFFF" font-size="16" font-weight="700">Заявление на материальную помощь к сессии</text>
        <text x="24" y="55" fill="#7D7D8F" font-size="12">Создан 25 сен, 10:14 • Студент: Анна Смирнова (VK ID 4829104) • Отдел: Профком</text>
        
        <!-- Кнопки смены статуса -->
        <rect x="880" y="18" width="100" height="34" rx="8" fill="#10B981" fill-opacity="0.15" stroke="#10B981" stroke-width="1"/>
        <text x="898" y="40" fill="#10B981" font-size="12" font-weight="700">✓ Решить</text>

        <rect x="994" y="18" width="100" height="34" rx="8" fill="#EF4444" fill-opacity="0.15" stroke="#EF4444" stroke-width="1"/>
        <text x="1010" y="40" fill="#EF4444" font-size="12" font-weight="700">✕ Закрыть</text>
    </g>

    <!-- Диалог со студентом и боковая карточка -->
    <g id="ChatArea" transform="translate(32, 104)">
        <!-- Левая колонка: Чат -->
        <rect width="780" height="680" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        
        <!-- Сообщение студента -->
        <g transform="translate(24, 24)">
            <rect width="520" height="90" rx="12" fill="#181822" stroke="#252533" stroke-width="1"/>
            <text x="16" y="24" fill="#FF4D9D" font-size="12" font-weight="700">Анна Смирнова • 10:14</text>
            <text x="16" y="48" fill="#FFFFFF" font-size="13">Здравствуйте! Подскажите, пожалуйста, какие справки</text>
            <text x="16" y="68" fill="#FFFFFF" font-size="13">нужно прикрепить к заявлению на матпомощь для сирот?</text>
        </g>

        <!-- Ответ куратора -->
        <g transform="translate(236, 134)">
            <rect width="520" height="110" rx="12" fill="url(#primaryGrad)"/>
            <text x="16" y="24" fill="#FFFFFF" font-size="12" font-weight="700">Куратор admin • 10:22</text>
            <text x="16" y="48" fill="#FFFFFF" font-size="13">Добрый день, Анна! Вам потребуются:</text>
            <text x="16" y="68" fill="#FFFFFF" font-size="13">1. Справка из деканата об обучении</text>
            <text x="16" y="88" fill="#FFFFFF" font-size="13">2. Копия свидетельства и справка о доходах за 3 месяца.</text>
        </g>

        <!-- Поле ввода ответа -->
        <g transform="translate(24, 570)">
            <rect width="732" height="86" rx="12" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
            <text x="20" y="32" fill="#7D7D8F" font-size="13">Напишите ответ студенту (сообщение уйдёт в VK)...</text>
            <rect x="610" y="36" width="106" height="38" rx="8" fill="url(#primaryGrad)"/>
            <text x="635" y="60" fill="#FFFFFF" font-size="13" font-weight="700">Отправить</text>
        </g>

        <!-- Правая колонка: Инфо о студенте и шаблоны -->
        <g transform="translate(800, 0)">
            <rect width="318" height="680" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
            <text x="20" y="35" fill="#FFFFFF" font-size="15" font-weight="700">Данные студента</text>
            
            <text x="20" y="70" fill="#7D7D8F" font-size="12">ФИО:</text>
            <text x="20" y="90" fill="#FFFFFF" font-size="14" font-weight="600">Смирнова Анна Игоревна</text>

            <text x="20" y="125" fill="#7D7D8F" font-size="12">Институт / Группа:</text>
            <text x="20" y="145" fill="#FFFFFF" font-size="14">ИТиКН • ИВТ-21-2</text>

            <text x="20" y="180" fill="#7D7D8F" font-size="12">Связанный профиль VK:</text>
            <text x="20" y="200" fill="#FF4D9D" font-size="14" font-weight="600">vk.com/id4829104</text>

            <line x1="20" y1="230" x2="298" y2="230" stroke="#20202C"/>

            <text x="20" y="260" fill="#FFFFFF" font-size="15" font-weight="700">Быстрые шаблоны</text>
            
            <rect x="20" y="280" width="278" height="38" rx="8" fill="#1A1A24"/>
            <text x="32" y="304" fill="#B8B8C8" font-size="12">Заявление принято в работу</text>

            <rect x="20" y="326" width="278" height="38" rx="8" fill="#1A1A24"/>
            <text x="32" y="350" fill="#B8B8C8" font-size="12">Запрос дополнительных документов</text>

            <rect x="20" y="372" width="278" height="38" rx="8" fill="#1A1A24"/>
            <text x="32" y="396" fill="#B8B8C8" font-size="12">Выплата назначена, ожидайте</text>
        </g>
    </g>
    """
    desk = desktop_shell("Тикет #1042", "tickets", content)

    # 5. Ticket Detail Mobile
    mob_content = """
    <!-- Заголовок тикета -->
    <g transform="translate(16, 10)">
        <rect width="358" height="74" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="14" y="24" fill="#FF4D9D" font-size="13" font-weight="800">#1042 • Анна Смирнова</text>
        <text x="14" y="44" fill="#FFFFFF" font-size="13" font-weight="600">Заявление на матпомощь</text>
        <rect x="14" y="50" width="70" height="18" rx="4" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="20" y="63" fill="#FF4D9D" font-size="10" font-weight="700">В работе</text>
        <text x="260" y="63" fill="#10B981" font-size="11" font-weight="700">✓ Завершить</text>
    </g>

    <!-- Сообщения -->
    <g transform="translate(16, 96)">
        <rect width="280" height="80" rx="12" fill="#181822"/>
        <text x="12" y="20" fill="#FF4D9D" font-size="11" font-weight="700">Анна Смирнова • 10:14</text>
        <text x="12" y="40" fill="#FFFFFF" font-size="12">Какие документы нужны для</text>
        <text x="12" y="58" fill="#FFFFFF" font-size="12">заявления на матпомощь?</text>
    </g>

    <g transform="translate(94, 186)">
        <rect width="280" height="90" rx="12" fill="url(#primaryGrad)"/>
        <text x="12" y="20" fill="#FFFFFF" font-size="11" font-weight="700">Вы • 10:22</text>
        <text x="12" y="40" fill="#FFFFFF" font-size="12">Справка из деканата и копия</text>
        <text x="12" y="58" fill="#FFFFFF" font-size="12">справки о доходах за 3 мес.</text>
    </g>

    <!-- Ввод ответа внизу -->
    <g transform="translate(16, 590)">
        <rect width="358" height="50" rx="12" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
        <text x="16" y="30" fill="#7D7D8F" font-size="12">Ответить студенту в VK...</text>
        <circle cx="330" cy="25" r="16" fill="url(#primaryGrad)"/>
        <text x="323" y="31" fill="#FFFFFF" font-size="14">→</text>
    </g>
    """
    mob = mobile_shell("Тикет #1042", "tickets", mob_content)

    (DESKTOP_DIR / "05_ticket_detail_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "05_ticket_detail_mobile.svg").write_text(mob, encoding="utf-8")

def generate_departments():
    # 6. Departments Desktop
    content = """
    <g id="DeptActions" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">Управление отделами университета</text>
        <rect x="940" y="0" width="178" height="40" rx="10" fill="url(#primaryGrad)"/>
        <text x="965" y="25" fill="#FFFFFF" font-size="13" font-weight="700">+ Добавить отдел</text>
    </g>

    <g id="DeptGrid" transform="translate(32, 80)">
        <!-- Карточка 1 -->
        <rect x="0" y="0" width="350" height="220" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="20" y="20" width="46" height="46" rx="12" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="32" y="50" fill="#FF4D9D" font-size="22">📚</text>
        <text x="80" y="38" fill="#FFFFFF" font-size="16" font-weight="700">Учебный отдел</text>
        <text x="80" y="58" fill="#7D7D8F" font-size="12">Сессия, справки, приказы</text>
        <text x="20" y="105" fill="#B8B8C8" font-size="13">Кураторов привязано: 4</text>
        <text x="20" y="130" fill="#B8B8C8" font-size="13">Активных тикетов: 8</text>
        <rect x="20" y="160" width="145" height="34" rx="8" fill="#1A1A24"/>
        <text x="45" y="182" fill="#B8B8C8" font-size="12">Редактировать</text>
        <rect x="175" y="160" width="155" height="34" rx="8" fill="url(#primaryGrad)"/>
        <text x="205" y="182" fill="#FFFFFF" font-size="12" font-weight="700">Открыть тикеты</text>

        <!-- Карточка 2 -->
        <rect x="384" y="0" width="350" height="220" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="404" y="20" width="46" height="46" rx="12" fill="#D65DB1" fill-opacity="0.15"/>
        <text x="416" y="50" fill="#D65DB1" font-size="22">💰</text>
        <text x="464" y="38" fill="#FFFFFF" font-size="16" font-weight="700">Стипендиальная комиссия</text>
        <text x="464" y="58" fill="#7D7D8F" font-size="12">Академическая и ПГАС</text>
        <text x="404" y="105" fill="#B8B8C8" font-size="13">Кураторов привязано: 2</text>
        <text x="404" y="130" fill="#B8B8C8" font-size="13">Активных тикетов: 3</text>
        <rect x="404" y="160" width="145" height="34" rx="8" fill="#1A1A24"/>
        <text x="429" y="182" fill="#B8B8C8" font-size="12">Редактировать</text>
        <rect x="559" y="160" width="155" height="34" rx="8" fill="url(#primaryGrad)"/>
        <text x="589" y="182" fill="#FFFFFF" font-size="12" font-weight="700">Открыть тикеты</text>

        <!-- Карточка 3 -->
        <rect x="768" y="0" width="350" height="220" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="788" y="20" width="46" height="46" rx="12" fill="#10B981" fill-opacity="0.15"/>
        <text x="800" y="50" fill="#10B981" font-size="22">🏢</text>
        <text x="848" y="38" fill="#FFFFFF" font-size="16" font-weight="700">Общежития и кампус</text>
        <text x="848" y="58" fill="#7D7D8F" font-size="12">Заселение, переселение, быт</text>
        <text x="788" y="105" fill="#B8B8C8" font-size="13">Кураторов привязано: 3</text>
        <text x="788" y="130" fill="#B8B8C8" font-size="13">Активных тикетов: 2</text>
        <rect x="788" y="160" width="145" height="34" rx="8" fill="#1A1A24"/>
        <text x="813" y="182" fill="#B8B8C8" font-size="12">Редактировать</text>
        <rect x="943" y="160" width="155" height="34" rx="8" fill="url(#primaryGrad)"/>
        <text x="973" y="182" fill="#FFFFFF" font-size="12" font-weight="700">Открыть тикеты</text>
    </g>
    """
    desk = desktop_shell("Отделы и направления", "departments", content)

    # 6. Departments Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="44" rx="10" fill="url(#primaryGrad)"/>
        <text x="179" y="28" fill="#FFFFFF" font-size="13" font-weight="700" text-anchor="middle">+ Добавить отдел</text>
    </g>

    <g transform="translate(16, 68)">
        <!-- Отдел 1 -->
        <rect width="358" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="32" fill="#FFFFFF" font-size="15" font-weight="700">Учебный отдел</text>
        <text x="16" y="54" fill="#7D7D8F" font-size="12">Кураторов: 4 • В работе: 8</text>
        <rect x="16" y="68" width="100" height="28" rx="6" fill="#1A1A24"/>
        <text x="32" y="86" fill="#B8B8C8" font-size="11">Настройки</text>
        <rect x="126" y="68" width="110" height="28" rx="6" fill="url(#primaryGrad)"/>
        <text x="146" y="86" fill="#FFFFFF" font-size="11" font-weight="700">К заявкам</text>
    </g>

    <g transform="translate(16, 190)">
        <!-- Отдел 2 -->
        <rect width="358" height="110" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="32" fill="#FFFFFF" font-size="15" font-weight="700">Стипендиальная комиссия</text>
        <text x="16" y="54" fill="#7D7D8F" font-size="12">Кураторов: 2 • В работе: 3</text>
        <rect x="16" y="68" width="100" height="28" rx="6" fill="#1A1A24"/>
        <text x="32" y="86" fill="#B8B8C8" font-size="11">Настройки</text>
        <rect x="126" y="68" width="110" height="28" rx="6" fill="url(#primaryGrad)"/>
        <text x="146" y="86" fill="#FFFFFF" font-size="11" font-weight="700">К заявкам</text>
    </g>
    """
    mob = mobile_shell("Отделы", "dashboard", mob_content)

    (DESKTOP_DIR / "06_departments_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "06_departments_mobile.svg").write_text(mob, encoding="utf-8")

def generate_faq():
    # 7. FAQ Desktop
    content = """
    <g id="FAQActions" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">База частых вопросов (FAQ)</text>
        <rect x="940" y="0" width="178" height="40" rx="10" fill="url(#primaryGrad)"/>
        <text x="965" y="25" fill="#FFFFFF" font-size="13" font-weight="700">+ Добавить вопрос</text>
    </g>

    <!-- Аккордеон вопросов -->
    <g id="FAQList" transform="translate(32, 84)">
        <!-- Вопрос 1 (Раскрыт) -->
        <rect x="0" y="0" width="1118" height="140" rx="12" fill="#121217" stroke="#FF4D9D" stroke-width="1"/>
        <text x="24" y="36" fill="#FF4D9D" font-size="15" font-weight="700">Как получить справку об обучении с гербовой печатью?</text>
        <text x="24" y="65" fill="#B8B8C8" font-size="13">Справка оформляется через раздел «Заявки» в боте или лично в кабинете 214 учебного корпуса.</text>
        <text x="24" y="85" fill="#B8B8C8" font-size="13">Срок изготовления составляет от 1 до 3 рабочих дней. Уведомление о готовности придёт в VK.</text>
        <text x="24" y="115" fill="#7D7D8F" font-size="11">Категория: Справки и документы • Просмотров: 342</text>
        <text x="1060" y="36" fill="#FF4D9D" font-size="18">▲</text>

        <!-- Вопрос 2 -->
        <rect x="0" y="156" width="1118" height="64" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="24" y="38" fill="#FFFFFF" font-size="14" font-weight="600">Где подать заявление на повышенную государственную стипендию (ПГАС)?</text>
        <text x="1060" y="38" fill="#7D7D8F" font-size="18">▼</text>

        <!-- Вопрос 3 -->
        <rect x="0" y="232" width="1118" height="64" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="24" y="38" fill="#FFFFFF" font-size="14" font-weight="600">Порядок временного выселения из общежития на период каникул</text>
        <text x="1060" y="38" fill="#7D7D8F" font-size="18">▼</text>
    </g>
    """
    desk = desktop_shell("Частые вопросы", "faq", content)

    # 7. FAQ Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="40" rx="10" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="20" y="25" fill="#7D7D8F" font-size="12">Поиск по вопросам...</text>
    </g>

    <g transform="translate(16, 62)">
        <rect width="358" height="120" rx="12" fill="#121217" stroke="#FF4D9D" stroke-width="1"/>
        <text x="14" y="26" fill="#FF4D9D" font-size="13" font-weight="700">Как заказать справку об обучении?</text>
        <text x="14" y="50" fill="#B8B8C8" font-size="11">Через команду «Справка» в боте или в каб. 214.</text>
        <text x="14" y="70" fill="#B8B8C8" font-size="11">Срок изготовления: 1-3 рабочих дня.</text>
        <text x="14" y="100" fill="#7D7D8F" font-size="10">Категория: Документы</text>
    </g>

    <g transform="translate(16, 194)">
        <rect width="358" height="56" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="14" y="34" fill="#FFFFFF" font-size="12" font-weight="600">Подача на стипендию ПГАС</text>
        <text x="325" y="34" fill="#7D7D8F" font-size="14">▼</text>
    </g>
    """
    mob = mobile_shell("FAQ", "faq", mob_content)

    (DESKTOP_DIR / "07_faq_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "07_faq_mobile.svg").write_text(mob, encoding="utf-8")

def generate_events():
    # 8. Events Desktop
    content = """
    <g id="EventsActions" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">Студенческие мероприятия и события</text>
        <rect x="910" y="0" width="208" height="40" rx="10" fill="url(#primaryGrad)"/>
        <text x="932" y="25" fill="#FFFFFF" font-size="13" font-weight="700">+ Создать мероприятие</text>
    </g>

    <g id="EventsGrid" transform="translate(32, 80)">
        <!-- Мероприятие 1 -->
        <rect x="0" y="0" width="544" height="180" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="20" y="20" width="70" height="70" rx="12" fill="url(#primaryGrad)"/>
        <text x="38" y="52" fill="#FFFFFF" font-size="20" font-weight="800">28</text>
        <text x="36" y="72" fill="#FFFFFF" font-size="12">СЕН</text>
        
        <text x="105" y="40" fill="#FFFFFF" font-size="16" font-weight="700">День карьеры и стажировок IT 2026</text>
        <text x="105" y="65" fill="#B8B8C8" font-size="13">Главный корпус, Актовый зал • 14:00 - 18:00</text>
        <text x="105" y="85" fill="#7D7D8F" font-size="12">Зарегистрировалось студентов: 412</text>
        
        <rect x="20" y="120" width="140" height="34" rx="8" fill="#1A1A24"/>
        <text x="45" y="142" fill="#B8B8C8" font-size="12">Редактировать</text>
        <rect x="175" y="120" width="180" height="34" rx="8" fill="url(#primaryGrad)"/>
        <text x="195" y="142" fill="#FFFFFF" font-size="12" font-weight="700">📢 Разослать в VK</text>

        <!-- Мероприятие 2 -->
        <rect x="574" y="0" width="544" height="180" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="594" y="20" width="70" height="70" rx="12" fill="#20202C"/>
        <text x="612" y="52" fill="#FF4D9D" font-size="20" font-weight="800">05</text>
        <text x="612" y="72" fill="#FF4D9D" font-size="12">ОКТ</text>
        
        <text x="679" y="40" fill="#FFFFFF" font-size="16" font-weight="700">Хакатон по разработке чат-ботов</text>
        <text x="679" y="65" fill="#B8B8C8" font-size="13">Технопарк, Коворкинг • 10:00</text>
        <text x="679" y="85" fill="#7D7D8F" font-size="12">Зарегистрировалось студентов: 128</text>
        
        <rect x="594" y="120" width="140" height="34" rx="8" fill="#1A1A24"/>
        <text x="619" y="142" fill="#B8B8C8" font-size="12">Редактировать</text>
    </g>
    """
    desk = desktop_shell("Мероприятия", "events", content)

    # 8. Events Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="140" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="14" y="16" width="50" height="50" rx="10" fill="url(#primaryGrad)"/>
        <text x="26" y="40" fill="#FFFFFF" font-size="16" font-weight="800">28</text>
        <text x="24" y="56" fill="#FFFFFF" font-size="10">СЕН</text>
        
        <text x="74" y="32" fill="#FFFFFF" font-size="13" font-weight="700">День карьеры IT 2026</text>
        <text x="74" y="50" fill="#7D7D8F" font-size="11">Актовый зал • 14:00</text>
        
        <rect x="14" y="86" width="330" height="38" rx="8" fill="url(#primaryGrad)"/>
        <text x="179" y="110" fill="#FFFFFF" font-size="12" font-weight="700" text-anchor="middle">📢 Рассылка анонса студентам</text>
    </g>
    """
    mob = mobile_shell("События", "dashboard", mob_content)

    (DESKTOP_DIR / "08_events_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "08_events_mobile.svg").write_text(mob, encoding="utf-8")

def generate_knowledge_base():
    # 9. Knowledge Base Desktop
    content = """
    <g id="KBActions" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">База знаний и регламенты</text>
        <rect x="940" y="0" width="178" height="40" rx="10" fill="url(#primaryGrad)"/>
        <text x="970" y="25" fill="#FFFFFF" font-size="13" font-weight="700">+ Новая статья</text>
    </g>

    <g id="KBCards" transform="translate(32, 80)">
        <rect x="0" y="0" width="350" height="160" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="20" y="36" fill="#FF4D9D" font-size="12" font-weight="700">УЧЕБНЫЙ ПРОЦЕСС</text>
        <text x="20" y="65" fill="#FFFFFF" font-size="15" font-weight="700">Регламент сдачи зачётно-экзаменационной сессии</text>
        <text x="20" y="130" fill="#7D7D8F" font-size="11">Обновлено: 3 дня назад • Читать статью →</text>

        <rect x="384" y="0" width="350" height="160" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="404" y="36" fill="#D65DB1" font-size="12" font-weight="700">СОЦИАЛЬНЫЙ БЛОК</text>
        <text x="404" y="65" fill="#FFFFFF" font-size="15" font-weight="700">Полный список льгот и выплат для студентов</text>
        <text x="404" y="130" fill="#7D7D8F" font-size="11">Обновлено: 1 неделю назад • Читать статью →</text>

        <rect x="768" y="0" width="350" height="160" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="788" y="36" fill="#10B981" font-size="12" font-weight="700">ПРОЖИВАНИЕ</text>
        <text x="788" y="65" fill="#FFFFFF" font-size="15" font-weight="700">Правила внутреннего распорядка общежитий</text>
        <text x="788" y="130" fill="#7D7D8F" font-size="11">Обновлено: 2 недели назад • Читать статью →</text>
    </g>
    """
    desk = desktop_shell("База знаний", "knowledge", content)

    # 9. Knowledge Base Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="100" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#FF4D9D" font-size="11" font-weight="700">УЧЕБНЫЙ ПРОЦЕСС</text>
        <text x="16" y="52" fill="#FFFFFF" font-size="13" font-weight="700">Регламент сдачи сессии</text>
        <text x="16" y="80" fill="#7D7D8F" font-size="11">Обновлено: 3 дня назад →</text>
    </g>
    <g transform="translate(16, 122)">
        <rect width="358" height="100" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#D65DB1" font-size="11" font-weight="700">СТИПЕНДИИ</text>
        <text x="16" y="52" fill="#FFFFFF" font-size="13" font-weight="700">Список льгот для студентов</text>
        <text x="16" y="80" fill="#7D7D8F" font-size="11">Обновлено: неделю назад →</text>
    </g>
    """
    mob = mobile_shell("База знаний", "knowledge", mob_content)

    (DESKTOP_DIR / "09_knowledge_base_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "09_knowledge_base_mobile.svg").write_text(mob, encoding="utf-8")

def generate_admins():
    # 10. Admins Desktop
    content = """
    <g id="AdminsActions" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">Администраторы и права доступа</text>
        <rect x="910" y="0" width="208" height="40" rx="10" fill="url(#primaryGrad)"/>
        <text x="930" y="25" fill="#FFFFFF" font-size="13" font-weight="700">+ Новый администратор</text>
    </g>

    <g id="AdminsTable" transform="translate(32, 80)">
        <rect width="1118" height="400" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="16" y="16" width="1086" height="40" rx="8" fill="#181822"/>
        <text x="36" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ПОЛЬЗОВАТЕЛЬ</text>
        <text x="240" y="41" fill="#7D7D8F" font-size="12" font-weight="700">РОЛЬ</text>
        <text x="440" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ОТДЕЛ</text>
        <text x="650" y="41" fill="#7D7D8F" font-size="12" font-weight="700">VK ID (ДЛЯ 2FA)</text>
        <text x="850" y="41" fill="#7D7D8F" font-size="12" font-weight="700">СТАТУС</text>
        <text x="990" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ДЕЙСТВИЯ</text>

        <!-- Строка 1 -->
        <g transform="translate(0, 70)">
            <text x="36" y="24" fill="#FFFFFF" font-size="13" font-weight="700">admin</text>
            <rect x="240" y="8" width="90" height="24" rx="6" fill="#FF4D9D" fill-opacity="0.15"/>
            <text x="250" y="24" fill="#FF4D9D" font-size="11" font-weight="700">Суперадмин</text>
            <text x="440" y="24" fill="#B8B8C8" font-size="13">Все отделы</text>
            <text x="650" y="24" fill="#B8B8C8" font-size="13">id1029384</text>
            <text x="850" y="24" fill="#10B981" font-size="13">● Активен</text>
            <text x="990" y="24" fill="#FF4D9D" font-size="13">Редактировать</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>

        <!-- Строка 2 -->
        <g transform="translate(0, 126)">
            <text x="36" y="24" fill="#FFFFFF" font-size="13" font-weight="700">curator_study</text>
            <rect x="240" y="8" width="100" height="24" rx="6" fill="#D65DB1" fill-opacity="0.15"/>
            <text x="250" y="24" fill="#D65DB1" font-size="11" font-weight="700">Админ отдела</text>
            <text x="440" y="24" fill="#B8B8C8" font-size="13">Учебный отдел</text>
            <text x="650" y="24" fill="#B8B8C8" font-size="13">id4910293</text>
            <text x="850" y="24" fill="#10B981" font-size="13">● Активен</text>
            <text x="990" y="24" fill="#FF4D9D" font-size="13">Редактировать</text>
        </g>
    </g>
    """
    desk = desktop_shell("Администраторы", "admins", content)

    # 10. Admins Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="90" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="28" fill="#FFFFFF" font-size="14" font-weight="700">admin</text>
        <rect x="16" y="38" width="80" height="20" rx="4" fill="#FF4D9D" fill-opacity="0.15"/>
        <text x="24" y="52" fill="#FF4D9D" font-size="10" font-weight="700">Суперадмин</text>
        <text x="16" y="74" fill="#7D7D8F" font-size="11">VK ID: id1029384 • Все отделы</text>
    </g>
    """
    mob = mobile_shell("Админы", "settings", mob_content)

    (DESKTOP_DIR / "10_admins_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "10_admins_mobile.svg").write_text(mob, encoding="utf-8")

def generate_logs():
    # 11. Logs Desktop
    content = """
    <g id="LogsHeader" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">Журнал безопасности и действий (Audit Log)</text>
        <rect x="940" y="0" width="178" height="40" rx="10" fill="#252533"/>
        <text x="965" y="25" fill="#FFFFFF" font-size="13" font-weight="600">📥 Выгрузить архив</text>
    </g>

    <g id="LogsTable" transform="translate(32, 80)">
        <rect width="1118" height="400" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <rect x="16" y="16" width="1086" height="40" rx="8" fill="#181822"/>
        <text x="36" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ВРЕМЯ (МСК)</text>
        <text x="180" y="41" fill="#7D7D8F" font-size="12" font-weight="700">СОБЫТИЕ</text>
        <text x="520" y="41" fill="#7D7D8F" font-size="12" font-weight="700">ПОЛЬЗОВАТЕЛЬ</text>
        <text x="740" y="41" fill="#7D7D8F" font-size="12" font-weight="700">IP-АДРЕС</text>
        <text x="920" y="41" fill="#7D7D8F" font-size="12" font-weight="700">СТАТУС</text>

        <!-- Строка 1 -->
        <g transform="translate(0, 70)">
            <text x="36" y="24" fill="#7D7D8F" font-size="13">25.09 13:54:30</text>
            <text x="180" y="24" fill="#FFFFFF" font-size="13">Успешная авторизация 2FA в веб-панель</text>
            <text x="520" y="24" fill="#FF4D9D" font-size="13">admin</text>
            <text x="740" y="24" fill="#B8B8C8" font-size="13">10.0.0.1 (VPN)</text>
            <text x="920" y="24" fill="#10B981" font-size="13">SUCCESS</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>

        <!-- Строка 2 -->
        <g transform="translate(0, 126)">
            <text x="36" y="24" fill="#7D7D8F" font-size="13">25.09 13:52:12</text>
            <text x="180" y="24" fill="#FFFFFF" font-size="13">Ответ куратора в тикете #1042</text>
            <text x="520" y="24" fill="#FF4D9D" font-size="13">admin</text>
            <text x="740" y="24" fill="#B8B8C8" font-size="13">10.0.0.1 (VPN)</text>
            <text x="920" y="24" fill="#10B981" font-size="13">SUCCESS</text>
            <line x1="20" y1="46" x2="1098" y2="46" stroke="#1D1D28"/>
        </g>
    </g>
    """
    desk = desktop_shell("Журнал аудита", "logs", content)

    # 11. Logs Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="74" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="24" fill="#7D7D8F" font-size="11">25.09 13:54</text>
        <text x="16" y="44" fill="#FFFFFF" font-size="12" font-weight="600">Вход 2FA: admin</text>
        <text x="16" y="62" fill="#10B981" font-size="11">Защищённая сеть • 10.0.0.1</text>
    </g>
    """
    mob = mobile_shell("Журнал", "settings", mob_content)

    (DESKTOP_DIR / "11_logs_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "11_logs_mobile.svg").write_text(mob, encoding="utf-8")

def generate_settings():
    # 12. Settings Desktop
    content = """
    <g id="SettingsForm" transform="translate(32, 24)">
        <text x="0" y="25" fill="#FFFFFF" font-size="18" font-weight="700">Системные параметры OSS Bot</text>
        
        <g transform="translate(0, 50)">
            <rect width="800" height="580" rx="14" fill="#121217" stroke="#20202C" stroke-width="1"/>
            
            <text x="32" y="45" fill="#FFFFFF" font-size="15" font-weight="700">1. Режим работы бота</text>
            <rect x="32" y="60" width="340" height="44" rx="8" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
            <text x="48" y="87" fill="#FFFFFF" font-size="13">Longpoll (по умолчанию)</text>

            <text x="32" y="140" fill="#FFFFFF" font-size="15" font-weight="700">2. Время утренней рассылки отчёта</text>
            <rect x="32" y="155" width="340" height="44" rx="8" fill="#181822" stroke="#2E2E3E" stroke-width="1"/>
            <text x="48" y="182" fill="#FFFFFF" font-size="13">09:00 (МСК)</text>

            <text x="32" y="235" fill="#FFFFFF" font-size="15" font-weight="700">3. Режим технического обслуживания</text>
            <rect x="32" y="255" width="60" height="30" rx="15" fill="#2E2E3E"/>
            <circle cx="47" cy="270" r="11" fill="#7D7D8F"/>
            <text x="105" y="275" fill="#7D7D8F" font-size="13">Отключён (система работает в штатном режиме)</text>

            <text x="32" y="330" fill="#FFFFFF" font-size="15" font-weight="700">4. Защищённый контур</text>
            <text x="32" y="355" fill="#10B981" font-size="13">● Защищено: Caddy WAF + Let's Encrypt TLS</text>

            <rect x="32" y="490" width="220" height="46" rx="10" fill="url(#primaryGrad)"/>
            <text x="65" y="519" fill="#FFFFFF" font-size="14" font-weight="700">Сохранить настройки</text>
        </g>
    </g>
    """
    desk = desktop_shell("Настройки", "settings", content)

    # 12. Settings Mobile
    mob_content = """
    <g transform="translate(16, 10)">
        <rect width="358" height="240" rx="12" fill="#121217" stroke="#20202C" stroke-width="1"/>
        <text x="16" y="32" fill="#FFFFFF" font-size="14" font-weight="700">Параметры бота</text>
        
        <text x="16" y="65" fill="#7D7D8F" font-size="11">Режим работы:</text>
        <text x="16" y="85" fill="#FFFFFF" font-size="13">Longpoll</text>

        <text x="16" y="120" fill="#7D7D8F" font-size="11">Время отчёта:</text>
        <text x="16" y="140" fill="#FFFFFF" font-size="13">09:00 (МСК)</text>

        <rect x="16" y="175" width="326" height="42" rx="8" fill="url(#primaryGrad)"/>
        <text x="179" y="201" fill="#FFFFFF" font-size="13" font-weight="700" text-anchor="middle">Сохранить</text>
    </g>
    """
    mob = mobile_shell("Настройки", "settings", mob_content)

    (DESKTOP_DIR / "12_settings_desktop.svg").write_text(desk, encoding="utf-8")
    (MOBILE_DIR / "12_settings_mobile.svg").write_text(mob, encoding="utf-8")

def generate_all_screens_artboard():
    """Создаёт единый огромный файл-холст для Figma, в котором все экраны размещены по сетке."""
    # Ширина холста: 6 экранов в строке * (1440 + 80) = ~9200
    # Высота: 2 строки десктопа + 2 строки мобилок = ~3500
    screens = [
        ("01_login_desktop.svg", "01_login_mobile.svg", "01. Авторизация (Login)"),
        ("02_2fa_desktop.svg", "02_2fa_mobile.svg", "02. Двухфакторная проверка (2FA)"),
        ("03_dashboard_desktop.svg", "03_dashboard_mobile.svg", "03. Главный Дашборд (Dashboard)"),
        ("04_tickets_desktop.svg", "04_tickets_mobile.svg", "04. Список обращений (Tickets)"),
        ("05_ticket_detail_desktop.svg", "05_ticket_detail_mobile.svg", "05. Диалог по тикету (Chat)"),
        ("06_departments_desktop.svg", "06_departments_mobile.svg", "06. Отделы (Departments)"),
        ("07_faq_desktop.svg", "07_faq_mobile.svg", "07. База вопросов (FAQ)"),
        ("08_events_desktop.svg", "08_events_mobile.svg", "08. Мероприятия (Events)"),
        ("09_knowledge_base_desktop.svg", "09_knowledge_base_mobile.svg", "09. База знаний (Knowledge)"),
        ("10_admins_desktop.svg", "10_admins_mobile.svg", "10. Администраторы (Admins)"),
        ("11_logs_desktop.svg", "11_logs_mobile.svg", "11. Журнал аудита (Logs)"),
        ("12_settings_desktop.svg", "12_settings_mobile.svg", "12. Настройки системы (Settings)"),
    ]

    elements = []
    
    # 1. Desktop Row (4 колонки по 3 экрана)
    col = 0
    row = 0
    for desk_file, _, title in screens:
        x = 100 + col * (1440 + 100)
        y = 140 + row * (900 + 120)
        desk_path = DESKTOP_DIR / desk_file
        if desk_path.exists():
            content = desk_path.read_text(encoding="utf-8")
            # Извлекаем внутреннее содержимое между <svg> и </svg>
            start = content.find(">") + 1
            end = content.rfind("</svg>")
            inner = content[start:end]
            elements.append(f"""
            <g id="Frame-Desktop-{col+1}-{row+1}" transform="translate({x}, {y})">
                <text x="0" y="-30" fill="#FFFFFF" font-size="28" font-weight="800">{title} — Desktop (1440x900)</text>
                {inner}
            </g>
            """)
        col += 1
        if col >= 3:
            col = 0
            row += 1

    # 2. Mobile Row
    m_col = 0
    m_row = 0
    base_my = 140 + 4 * (900 + 120) + 100
    for _, mob_file, title in screens:
        x = 100 + m_col * (390 + 80)
        y = base_my + m_row * (844 + 100)
        mob_path = MOBILE_DIR / mob_file
        if mob_path.exists():
            content = mob_path.read_text(encoding="utf-8")
            start = content.find(">") + 1
            end = content.rfind("</svg>")
            inner = content[start:end]
            elements.append(f"""
            <g id="Frame-Mobile-{m_col+1}-{m_row+1}" transform="translate({x}, {y})">
                <text x="0" y="-20" fill="#FF4D9D" font-size="18" font-weight="700">{title} — Mobile (390x844)</text>
                {inner}
            </g>
            """)
        m_col += 1
        if m_col >= 6:
            m_col = 0
            m_row += 1

    total_w = 4800
    total_h = base_my + 2 * (844 + 100) + 200

    board_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" width="{total_w}" height="{total_h}" fill="none">
{CSS_DEFS}
<rect width="{total_w}" height="{total_h}" fill="#050508"/>

<!-- Заголовок дизайн-системы -->
<g transform="translate(100, 70)">
    <text x="0" y="0" fill="#FFFFFF" font-size="42" font-weight="800">OSS Bot Web Panel — Дизайн-система и все экраны для Figma</text>
    <text x="0" y="32" fill="#7D7D8F" font-size="18">Все 12 страниц веб-панели в разрешениях Desktop (1440×900) и Mobile (390×844) • Версия v0.8.4.1</text>
</g>

{''.join(elements)}
</svg>"""

    (FIGMA_DIR / "figma_all_screens_board.svg").write_text(board_svg, encoding="utf-8")

def main():
    print("Генерация страниц...")
    generate_login()
    generate_2fa()
    generate_dashboard()
    generate_tickets()
    generate_ticket_detail()
    generate_departments()
    generate_faq()
    generate_events()
    generate_knowledge_base()
    generate_admins()
    generate_logs()
    generate_settings()
    print("Генерация сводного холста Figma...")
    generate_all_screens_artboard()
    print("Генерация успешно завершена!")

if __name__ == "__main__":
    main()
