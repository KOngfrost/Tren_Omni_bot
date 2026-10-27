import json


def _chunk_buttons(buttons: list, size: int = 2) -> list[list]:
    """Разбить список кнопок на ряды фиксированного размера."""
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def _pagination_nav_row(page: int, has_more: bool) -> list[tuple[str, str]]:
    """Ряд навигации пагинации: «⬅️ Назад» / «Ещё ➡️».

    Все кнопки навигации белые (secondary).
    """
    row: list[tuple[str, str]] = []
    if page > 0:
        row.append(("Назад", "secondary"))
    if has_more:
        row.append(("Ещё", "secondary"))
    return row


def _format_keyboard(rows: list[list[tuple[str, str]]], one_time: bool = False) -> str:
    """Сформировать JSON-структуру клавиатуры VK.

    Цветовые правила проекта:
    - Все обычные кнопки: белые (secondary).
    - Кнопка админа: строгая белая (secondary).
    - Кнопка отмены: красная (negative).
    """
    return json.dumps(
        {
            "one_time": one_time,
            "buttons": [
                [
                    {"action": {"type": "text", "label": label}, "color": color}
                    for label, color in row
                ]
                for row in rows
            ],
        },
        ensure_ascii=False,
    )


def build_main_keyboard(
    is_admin: bool = False,
    departments: list[str] | None = None,
    is_tech_admin: bool = False,
) -> str:
    """Построить основную клавиатуру с ключевыми разделами бота.

    Кнопки расположены аккуратными рядами:
    - Ряд 1: Создать заявку / Мои заявки (белые)
    - Ряд 2: Частые вопросы / База знаний (белые)
    - Ряд 3: Мероприятия / Партнёрство (белые)
    - Ряд 4: Админ / Тех-админ (белые)
    """
    rows: list[list[tuple[str, str]]] = [
        [("Создать заявку", "secondary"), ("Мои заявки", "secondary")],
        [("Частые вопросы", "secondary"), ("База знаний", "secondary")],
        [("Мероприятия", "secondary"), ("Партнёрство", "secondary")],
    ]
    admin_row: list[tuple[str, str]] = []
    if is_admin:
        admin_row.append(("Админ", "secondary"))
    if is_tech_admin:
        admin_row.append(("Тех-админ", "secondary"))
    if admin_row:
        rows.append(admin_row)
    return _format_keyboard(rows, one_time=False)


def build_ticket_department_keyboard(departments: list[str]) -> str:
    """Клавиатура выбора отдела при создании заявки (включая вариант 'Без отдела').

    - Отделы разбиты по 2 в ряд (белые).
    - 'Без отдела' на отдельной строке (белая).
    - 'Отмена' на отдельной строке внизу (красная).
    """
    dept_buttons: list[tuple[str, str]] = [(dept, "secondary") for dept in departments]
    rows = _chunk_buttons(dept_buttons, 2)
    rows.append([("Без отдела", "secondary")])
    rows.append([("Отмена", "negative")])
    return _format_keyboard(rows, one_time=True)


def build_admin_keyboard(is_super: bool = False) -> str:
    """Панель администратора: все действия белые, возврат в меню белый."""
    rows: list[list[tuple[str, str]]] = [
        [("Заявки администратора", "secondary"), ("Сформировать отчет", "secondary")],
    ]
    if is_super:
        rows.append([("Рассылка", "secondary")])
    rows.append([("Обычное меню", "secondary")])
    return _format_keyboard(rows, one_time=False)


def build_broadcast_exclude_keyboard(
    candidates_count: int,
    excluded_indices: set[int] | list[int] | None = None,
) -> str:
    """Клавиатура исключения получателей перед рассылкой.

    Кнопки: [Исключить #N] (или [Вернуть #N]), [Продолжить], [Отмена].
    Все кнопки строго без эмодзи.
    """
    excluded_set = set(excluded_indices or [])
    buttons: list[tuple[str, str]] = []
    for i in range(1, candidates_count + 1):
        if i in excluded_set or (i - 1) in excluded_set:
            buttons.append((f"Вернуть #{i}", "secondary"))
        else:
            buttons.append((f"Исключить #{i}", "secondary"))
    chunk_size = 3 if candidates_count > 4 else 2
    rows = _chunk_buttons(buttons, chunk_size)
    rows.append([("Продолжить", "positive")])
    rows.append([("Отмена", "negative")])
    return _format_keyboard(rows, one_time=True)


def build_broadcast_comment_choice_keyboard() -> str:
    """Клавиатура выбора добавления комментария к прикрепленному посту/фото.

    Кнопки: [Добавить комментарий], [Отправить без комментария], [Отмена].
    Все кнопки строго без эмодзи.
    """
    rows: list[list[tuple[str, str]]] = [
        [("Добавить комментарий", "secondary")],
        [("Отправить без комментария", "secondary")],
        [("Отмена", "negative")],
    ]
    return _format_keyboard(rows, one_time=True)


def build_broadcast_confirm_keyboard() -> str:
    """Клавиатура подтверждения отправки рассылки."""
    rows: list[list[tuple[str, str]]] = [
        [("Подтвердить рассылку", "positive")],
        [("Отмена", "negative")],
    ]
    return _format_keyboard(rows, one_time=True)


def build_admin_report_types_keyboard() -> str:
    """Клавиатура выбора типа отчёта: за сегодня, за определённое число, за период."""
    rows: list[list[tuple[str, str]]] = [
        [("За сегодня", "secondary"), ("За определённое число", "secondary")],
        [("За период", "secondary")],
        [("Отмена", "negative")],
    ]
    return _format_keyboard(rows, one_time=True)


def build_admin_tickets_list_keyboard(
    ticket_ids: list[int],
    max_buttons: int = 6,
    *,
    page: int = 0,
    has_more: bool = False,
) -> str:
    """Клавиатура списка заявок для администратора: кнопки открытия конкретных заявок."""
    buttons: list[tuple[str, str]] = [
        (f"Заявка #{tid}", "secondary") for tid in ticket_ids[:max_buttons]
    ]
    rows = _chunk_buttons(buttons, 2)
    nav_row = _pagination_nav_row(page, has_more)
    if nav_row:
        rows.append(nav_row)
    rows.append([("Заявки администратора", "secondary"), ("Админ", "secondary")])
    return _format_keyboard(rows, one_time=False)


def build_admin_ticket_actions_keyboard(ticket_id: int, is_completed: bool = False) -> str:
    """Клавиатура действий над заявкой: быстрый ответ, смена статуса, история."""
    rows: list[list[tuple[str, str]]] = [
        [(f"Ответить #{ticket_id}", "secondary"), (f"История #{ticket_id}", "secondary")]
    ]
    if is_completed:
        rows.append([(f"В обработку #{ticket_id}", "secondary")])
    else:
        rows.append(
            [(f"В обработку #{ticket_id}", "secondary"), (f"Выполнено #{ticket_id}", "secondary")]
        )
    rows.append([("Заявки администратора", "secondary"), ("Админ", "secondary")])
    return _format_keyboard(rows, one_time=False)


def build_admin_reply_cancel_keyboard(ticket_id: int) -> str:
    """Клавиатура отмены ввода ответа на заявку."""
    return _format_keyboard(
        [
            [(f"Заявка #{ticket_id}", "secondary")],
            [("Отмена", "negative")],
        ],
        one_time=True,
    )


def build_cancel_keyboard() -> str:
    """Клавиатура с кнопкой отмены."""
    return _format_keyboard([[("Отмена", "negative")]], one_time=True)


# Алиас для обратной совместимости
build_admin_cancel_keyboard = build_cancel_keyboard


def build_anonymous_choice_keyboard() -> str:
    """Клавиатура выбора анонимности с обязательной кнопкой отмены."""
    return _format_keyboard(
        [
            [("Остаться не анонимным", "secondary")],
            [("Остаться анонимным", "secondary")],
            [("Отмена", "negative")],
        ],
        one_time=True,
    )


def build_tickets_keyboard(
    ticket_ids: list[int],
    max_buttons: int = 5,
    *,
    page: int = 0,
    has_more: bool = False,
) -> str:
    """Клавиатура со списком заявок: кнопка «Подробнее #N» на каждую заявку (до max_buttons)."""
    buttons = [(f"Подробнее #{ticket_id}", "secondary") for ticket_id in ticket_ids[:max_buttons]]
    rows = _chunk_buttons(buttons, 2)
    nav_row = _pagination_nav_row(page, has_more)
    if nav_row:
        rows.append(nav_row)
    rows.append([("Меню", "secondary")])
    return _format_keyboard(rows, one_time=True)


def build_faq_departments_keyboard(departments: list[str]) -> str:
    """Клавиатура выбора отдела для просмотра частых вопросов."""
    dept_buttons: list[tuple[str, str]] = [
        (f"Вопросы: {dept}", "secondary") for dept in departments
    ]
    rows = _chunk_buttons(dept_buttons, 2)
    rows.append([("Вопросы: Все отделы", "secondary")])
    rows.append([("Отмена", "negative")])
    return _format_keyboard(rows, one_time=True)


def build_faq_items_keyboard(
    item_ids: list[int],
    max_buttons: int = 6,
    *,
    page: int = 0,
    has_more: bool = False,
) -> str:
    """Клавиатура номеров вопросов для быстрого открытия в один клик."""
    start_num = page * max_buttons + 1
    buttons: list[tuple[str, str]] = [
        (f"{start_num + i}", "secondary") for i, item_id in enumerate(item_ids[:max_buttons])
    ]
    rows = _chunk_buttons(buttons, 3)
    nav_row = _pagination_nav_row(page, has_more)
    if nav_row:
        rows.append(nav_row)
    rows.append([("К разделам вопросов", "secondary")])
    rows.append([("Отмена", "negative")])
    return _format_keyboard(rows, one_time=True)


def build_knowledge_departments_keyboard(
    departments: list[str], topics: list[str] | None = None
) -> str:
    """Клавиатура выбора отдела для просмотра материалов базы знаний."""
    dept_buttons: list[tuple[str, str]] = [(f"База: {dept}", "secondary") for dept in departments]
    rows = _chunk_buttons(dept_buttons, 2)
    if topics:
        topic_buttons: list[tuple[str, str]] = [(f"Тема: {t}", "secondary") for t in topics[:4]]
        rows.extend(_chunk_buttons(topic_buttons, 2))
    rows.append([("База: Без отдела", "secondary"), ("База: Все материалы", "secondary")])
    rows.append([("Отмена", "negative")])
    return _format_keyboard(rows, one_time=True)


def build_back_nav_keyboard(back_label: str = "К разделам вопросов") -> str:
    """Клавиатура возврата назад."""
    rows = [
        [(back_label, "secondary")],
        [("Отмена", "negative")],
    ]
    return _format_keyboard(rows, one_time=True)


def build_events_keyboard(event_ids: list[int] | None = None) -> str:
    """Клавиатура мероприятий с кнопками быстрой записи в один клик и возвратом в меню."""
    buttons: list[tuple[str, str]] = [
        (f"Записаться #{eid}", "secondary") for eid in (event_ids or [])[:6]
    ]
    rows = _chunk_buttons(buttons, 2)
    rows.append([("Меню", "secondary")])
    return _format_keyboard(rows, one_time=False)


def build_knowledge_suggest_keyboard() -> str:
    """Клавиатура с кнопкой создания заявки после предложения статьи из базы знаний."""
    rows = [
        [("Создать заявку", "secondary")],
        [("Отмена", "negative")],
    ]
    return _format_keyboard(rows, one_time=True)


def build_tech_admin_keyboard() -> str:
    """Главная панель Технического администратора."""
    rows: list[list[tuple[str, str]]] = [
        [("Сервер", "secondary"), ("Метрики", "secondary")],
        [("Бэкап БД", "secondary"), ("Техработы", "secondary")],
        [("Контейнеры", "secondary"), ("Сброс сессий", "secondary")],
        [("Логи сервисов", "secondary"), ("2FA статус", "secondary")],
        [("Главное меню", "secondary")],
    ]
    return _format_keyboard(rows, one_time=False)


def build_tech_containers_keyboard(containers: list[str] | None = None) -> str:
    """Клавиатура списка контейнеров для перезапуска."""
    c_list = containers or [
        "oss_bot_app",
        "oss_bot_web",
        "oss_bot_redis",
        "oss_bot_pgbouncer",
        "oss_bot_go_api",
        "oss_bot_caddy",
    ]
    rows: list[list[tuple[str, str]]] = [
        [(f"Перезапуск {name}", "secondary")] for name in c_list[:6]
    ]
    rows.append([("Тех-меню", "secondary"), ("Главное меню", "secondary")])
    return _format_keyboard(rows, one_time=False)


def build_tech_maintenance_keyboard(is_active: bool) -> str:
    """Клавиатура управления режимом технического обслуживания."""
    action_text = "Отключить техработы" if is_active else "Включить техработы"
    rows: list[list[tuple[str, str]]] = [
        [(action_text, "negative" if not is_active else "positive")],
        [("Тех-меню", "secondary"), ("Главное меню", "secondary")],
    ]
    return _format_keyboard(rows, one_time=False)


def build_tech_logs_keyboard(containers: list[str] | None = None) -> str:
    """Клавиатура выбора сервиса для просмотра последних логов."""
    c_list = containers or ["oss_bot_app", "oss_bot_web", "oss_bot_redis", "oss_bot_db"]
    rows: list[list[tuple[str, str]]] = [[(f"Логи {name}", "secondary")] for name in c_list[:4]]
    rows.append([("Тех-меню", "secondary"), ("Главное меню", "secondary")])
    return _format_keyboard(rows, one_time=False)
