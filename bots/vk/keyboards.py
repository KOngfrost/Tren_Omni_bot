import json


def _chunk_buttons(buttons: list, size: int = 2) -> list[list]:
    """Разбить список кнопок на ряды фиксированного размера."""
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def _format_keyboard(rows: list[list[tuple[str, str]]], one_time: bool = False) -> str:
    """Сформировать JSON-структуру клавиатуры VK."""
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
        }
    )


def build_main_keyboard(is_admin: bool = False, departments: list[str] | None = None) -> str:
    """Построить основную клавиатуру с динамическими отделами из БД."""
    buttons: list[tuple[str, str]] = (
        [(dept, "primary") for dept in departments] if departments else []
    )

    buttons.extend(
        [
            ("Задать вопрос", "secondary"),
            ("FAQ", "secondary"),
            ("Мероприятия", "primary"),
            ("База знаний", "secondary"),
        ]
    )
    if is_admin:
        buttons.append(("Админ", "positive"))
    buttons.append(("Мои заявки", "primary"))
    return _format_keyboard(_chunk_buttons(buttons, 2), one_time=False)


def build_admin_keyboard() -> str:
    buttons = [
        ("Заявки администратора", "primary"),
        ("Сформировать отчет", "positive"),
        ("Отчет по дате", "positive"),
        ("Отчет за период", "positive"),
        ("Обычное меню", "secondary"),
    ]
    return _format_keyboard(_chunk_buttons(buttons, 2), one_time=False)


def build_cancel_keyboard() -> str:
    """Клавиатура с кнопкой отмены."""
    return json.dumps(
        {
            "one_time": True,
            "buttons": [
                [
                    {"action": {"type": "text", "label": "Отмена"}, "color": "negative"},
                ]
            ],
        }
    )


# Алиас для обратной совместимости
build_admin_cancel_keyboard = build_cancel_keyboard


def build_anonymous_choice_keyboard() -> str:
    """Клавиатура выбора анонимности."""
    return json.dumps(
        {
            "one_time": True,
            "buttons": [
                [
                    {
                        "action": {"type": "text", "label": "Остаться анонимным"},
                        "color": "secondary",
                    },
                    {
                        "action": {"type": "text", "label": "Остаться не анонимным"},
                        "color": "primary",
                    },
                ]
            ],
        }
    )


def build_tickets_keyboard(ticket_ids: list[int], max_buttons: int = 5) -> str:
    """Клавиатура со списком заявок: кнопка «Подробнее #N» на каждую заявку (до max_buttons)."""
    buttons = [(f"Подробнее #{ticket_id}", "secondary") for ticket_id in ticket_ids[:max_buttons]]
    buttons.append(("Меню", "primary"))
    return _format_keyboard(_chunk_buttons(buttons, 2), one_time=True)
