import json


def build_main_keyboard(is_admin: bool = False, departments: list[str] | None = None) -> str:
    """Построить основную клавиатуру с динамическими отделами из БД."""
    buttons: list[tuple[str, str]] = []
    
    # Добавляем только те отделы, которые существуют в БД
    if departments:
        for dept in departments:
            buttons.append((dept, "primary"))
    
    buttons.extend([
        ("Задать вопрос", "secondary"),
        ("FAQ", "secondary"),
        ("Мероприятия", "primary"),
        ("База знаний", "secondary"),
    ])
    if is_admin:
        buttons.append(("Админ", "positive"))
    buttons.append(("Мои заявки", "primary"))
    keyboard = {
        "one_time": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": label}, "color": color}
                for label, color in row
            ]
            for row in [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        ],
    }
    return json.dumps(keyboard)


def build_admin_keyboard() -> str:
    buttons = [
        ("Заявки администратора", "primary"),
        ("Сформировать отчет", "positive"),
        ("Отчет по дате", "positive"),
        ("Отчет за период", "positive"),
        ("Обычное меню", "secondary"),
    ]
    keyboard = {
        "one_time": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": label}, "color": color}
                for label, color in row
            ]
            for row in [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        ],
    }
    return json.dumps(keyboard)


def build_admin_cancel_keyboard() -> str:
    """Клавиатура для админа с кнопкой отмены (используется при вводе дат)."""
    keyboard = {
        "one_time": True,
        "buttons": [[
            {"action": {"type": "text", "label": "Отмена"}, "color": "negative"},
        ]],
    }
    return json.dumps(keyboard)


def build_tickets_keyboard(ticket_ids: list[int], max_buttons: int = 5) -> str:
    """Клавиатура со списком заявок: кнопка «Подробнее #N» на каждую заявку (до max_buttons)."""
    buttons = [
        (f"Подробнее #{ticket_id}", "secondary")
        for ticket_id in ticket_ids[:max_buttons]
    ]
    buttons.append(("Меню", "primary"))
    keyboard = {
        "one_time": True,
        "buttons": [
            [
                {"action": {"type": "text", "label": label}, "color": color}
                for label, color in row
            ]
            for row in [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        ],
    }
    return json.dumps(keyboard)


def build_cancel_keyboard() -> str:
    """Клавиатура с кнопкой отмены для использования при создании заявки."""
    keyboard = {
        "one_time": True,
        "buttons": [[
            {"action": {"type": "text", "label": "Отмена"}, "color": "negative"},
        ]],
    }
    return json.dumps(keyboard)
