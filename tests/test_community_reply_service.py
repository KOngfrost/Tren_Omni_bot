"""Модульные тесты для сервиса ответов администраторов из сообщества VK.

Покрывает core/services/community_reply_service.py:
- Регулярные выражения: TICKET_PREFIX_RE, TICKET_IN_TEXT_RE, BOT_SYSTEM_PATTERNS
- Вспомогательные методы: _collect_quoted_messages, _find_ticket_by_id_or_local_num,
  _resolve_by_prefix, _resolve_by_quoted_text, _resolve_fallback, _resolve_admin_name,
  resolve_target_ticket
- Основную точку входа: handle_community_message_reply (валидация, игнорирование,
  переход статусов, история сообщений, аудит в Log, вызов notify_ticket_change).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from core.models import Log, MessageAuthorType, Ticket, TicketMessage, TicketStatus, User
from core.services.community_reply_service import (
    BOT_SYSTEM_PATTERNS,
    TICKET_IN_TEXT_RE,
    TICKET_PREFIX_RE,
    _collect_quoted_messages,
    _find_ticket_by_id_or_local_num,
    _resolve_admin_name,
    _resolve_by_prefix,
    _resolve_by_quoted_text,
    _resolve_fallback,
    handle_community_message_reply,
    resolve_target_ticket,
)

# ==============================================================================
# 1. Тесты регулярных выражений
# ==============================================================================


@pytest.mark.parametrize(
    ("input_text", "expected_num", "expected_clean_text"),
    [
        ("[#123] Спасибо", 123, "Спасибо"),
        ("#123 Решено", 123, "Решено"),
        ("[заявка #123] Ответ", 123, "Ответ"),
        ("№ 456: Готово к выдаче", 456, "Готово к выдаче"),
        ("[обращение 789] - Передано", 789, "Передано"),
        ("тикет #42 Принято", 42, "Принято"),
        ("тикет №99 -- выполнено", 99, "выполнено"),
        ("[#100]", 100, ""),
        ("#77   Многострочный\nтекст ответа", 77, "Многострочный\nтекст ответа"),
    ],
)
def test_ticket_prefix_re_matches(input_text, expected_num, expected_clean_text):
    """TICKET_PREFIX_RE корректно извлекает номер заявки и очищенный текст ответа."""
    match = TICKET_PREFIX_RE.match(input_text)
    assert match is not None
    raw_num = match.group(1) or match.group(2) or match.group(3)
    assert int(raw_num) == expected_num
    clean_body = (match.group(4) or "").strip()
    assert clean_body == expected_clean_text


@pytest.mark.parametrize(
    "non_matching_text",
    [
        "Обычный текст ответа без номеров",
        "Привет, как дела?",
        "Заявка без номера",
        "123 просто число в начале",
    ],
)
def test_ticket_prefix_re_non_matching(non_matching_text):
    """TICKET_PREFIX_RE не должен срабатывать на сообщения без префикса тикета."""
    assert TICKET_PREFIX_RE.match(non_matching_text) is None


@pytest.mark.parametrize(
    ("text", "expected_num"),
    [
        ("По поводу заявки #123 всё сделано", 123),
        ("В отношении обращения №456 уточните данные", 456),
        ("тикет #789 закрыт по вашей просьбе", 789),
        ("Ответ по #42: сантехник будет завтра", 42),
        ("Статус №99 изменен", 99),
    ],
)
def test_ticket_in_text_re_matches(text, expected_num):
    """TICKET_IN_TEXT_RE находит упоминание номера заявки внутри свободного текста."""
    match = TICKET_IN_TEXT_RE.search(text)
    assert match is not None
    assert int(match.group(1)) == expected_num


@pytest.mark.parametrize(
    "system_text",
    [
        "Ваше обращение принято к рассмотрению",
        "Номер обращения: #105",
        "Номер обращения : 204",
        "Раздел: Вопросы проживания",
        "Главное меню сообщества",
        "Выберите отдел для обращения",
        "выберите действие из списка",
        "выберите нужное действие",
        "выберите режим обращения",
        "Остаться анонимным при создании",
        "Остаться не анонимным",
        "Не удалось отправить заявку",
        "Введите текст обращения ниже",
        "Опишите вашу проблему подробнее",
        "Нажмите «отмена» для возврата",
        "Анонимное обращение зарегистрировано",
        "Спасибо за обращение! Ожидайте ответа",
        "Создание заявки завершено успешно",
        "Возможно, поможет эта информация: статья 42",
        "Одноразовый код для входа: 481920",
        "Ежедневный отчёт за 2026-10-23",
    ],
)
def test_bot_system_patterns_match(system_text):
    """BOT_SYSTEM_PATTERNS распознает автоматические системные фразы бота."""
    assert BOT_SYSTEM_PATTERNS.search(system_text) is not None


@pytest.mark.parametrize(
    "admin_text",
    [
        "Здравствуйте! Мастер уже вышел к вам в комнату.",
        "Уточните, пожалуйста, удобное время для осмотра.",
        "Мы передали ваш запрос коменданту общежития.",
        "Все неполадки с интернетом устранены.",
    ],
)
def test_bot_system_patterns_do_not_match_regular_replies(admin_text):
    """BOT_SYSTEM_PATTERNS не должен ложно срабатывать на осмысленные ответы оператора."""
    assert BOT_SYSTEM_PATTERNS.search(admin_text) is None


# ==============================================================================
# 2. Тесты вспомогательных функций резолвинга
# ==============================================================================


def test_collect_quoted_messages():
    """_collect_quoted_messages собирает цепочку цитат до глубины 4 и пересланные сообщения."""
    # Пустые параметры
    assert _collect_quoted_messages(None, None) == []
    assert _collect_quoted_messages({}, []) == []  # пустой dict считается falsy и пропускается
    assert _collect_quoted_messages({"text": "привет"}, []) == [{"text": "привет"}]

    # Цепочка вложенности глубиной 5
    reply_chain = {
        "text": "уровень 0",
        "reply_message": {
            "text": "уровень 1",
            "reply_message": {
                "text": "уровень 2",
                "reply_message": {
                    "text": "уровень 3",
                    "reply_message": {
                        "text": "уровень 4 (должен быть отсечен по лимиту depth < 4)"
                    },
                },
            },
        },
    }
    fwd = [{"text": "пересланное 1"}, "не_словарь_игнорируется", {"text": "пересланное 2"}]

    result = _collect_quoted_messages(reply_chain, fwd)
    texts = [item.get("text") for item in result]
    assert texts == [
        "уровень 0",
        "уровень 1",
        "уровень 2",
        "уровень 3",
        "пересланное 1",
        "пересланное 2",
    ]
    assert "уровень 4 (должен быть отсечен по лимиту depth < 4)" not in texts


@pytest.mark.asyncio
async def test_find_ticket_by_id_or_local_num(db_session_maker):
    """_find_ticket_by_id_or_local_num ищет по прямому ID, локальному номеру или глобальному ID."""
    async with db_session_maker() as session:
        u1 = User(vk_id=1001, full_name="Студент 1")
        u2 = User(vk_id=1002, full_name="Студент 2")
        session.add_all([u1, u2])
        await session.flush()

        # Создаем заявки с ID > 10, чтобы локальные номера 1 и 2 не совпадали с primary key
        t1 = Ticket(id=101, user_id=u1.id, topic="Заявка 1", status=TicketStatus.NEW)
        t2 = Ticket(id=102, user_id=u1.id, topic="Заявка 2", status=TicketStatus.NEW)
        t_other = Ticket(id=201, user_id=u2.id, topic="Заявка чужая", status=TicketStatus.NEW)
        session.add_all([t1, t2, t_other])
        await session.commit()

        # 1. Поиск по прямому ID заявки студента (101)
        found = await _find_ticket_by_id_or_local_num(session, u1.id, 101)
        assert found is not None
        assert found.id == 101

        # 2. Поиск по локальному номеру студента (1-я и 2-я заявки)
        found_local1 = await _find_ticket_by_id_or_local_num(session, u1.id, 1)
        assert found_local1 is not None
        assert found_local1.id == 101

        found_local2 = await _find_ticket_by_id_or_local_num(session, u1.id, 2)
        assert found_local2 is not None
        assert found_local2.id == 102

        # 3. Поиск по глобальному ID (когда локальный номер вне диапазона)
        found_global = await _find_ticket_by_id_or_local_num(session, u1.id, 201)
        assert found_global is not None
        assert found_global.id == 201

        # 4. Несуществующий номер
        assert await _find_ticket_by_id_or_local_num(session, u1.id, 999999) is None


@pytest.mark.asyncio
async def test_resolve_by_prefix(db_session_maker):
    """_resolve_by_prefix корректно находит заявку и очищает префикс ответа."""
    async with db_session_maker() as session:
        user = User(vk_id=2001, full_name="Тестовый Студент")
        session.add(user)
        await session.flush()

        ticket = Ticket(user_id=user.id, topic="Вопрос по стиралке", status=TicketStatus.NEW)
        session.add(ticket)
        await session.commit()

        # Совпадение с префиксом и текстом
        cand, clean_text = await _resolve_by_prefix(session, user.id, f"[#{ticket.id}] Готово!")
        assert cand is not None
        assert cand.id == ticket.id
        assert clean_text == "Готово!"

        # Префикс без текста ответа (возвращает исходный текст)
        cand_empty, original_text = await _resolve_by_prefix(session, user.id, f"[#{ticket.id}]")
        assert cand_empty is not None
        assert cand_empty.id == ticket.id
        assert original_text == f"[#{ticket.id}]"

        # Префикс с несуществующим номером
        cand_none, preserved = await _resolve_by_prefix(session, user.id, "[#9999] Текст")
        assert cand_none is None
        assert preserved == "[#9999] Текст"

        # Нет префикса вовсе
        cand_none2, preserved2 = await _resolve_by_prefix(session, user.id, "Обычный текст")
        assert cand_none2 is None
        assert preserved2 == "Обычный текст"


@pytest.mark.asyncio
async def test_resolve_by_quoted_text(db_session_maker):
    """_resolve_by_quoted_text находит заявку по номеру в цитате, описанию, сообщению или response_text."""
    async with db_session_maker() as session:
        user = User(vk_id=3001, full_name="Студент с историей")
        session.add(user)
        await session.flush()

        ticket = Ticket(
            user_id=user.id,
            topic="Отопление",
            description="Батарея холодная в 404 комнате",
            response_text="Сантехник уведомлен",
            status=TicketStatus.NEW,
        )
        session.add(ticket)
        await session.flush()

        t_msg = TicketMessage(
            ticket_id=ticket.id,
            author_type=MessageAuthorType.USER,
            message="Уточняю, батарея течет прямо на пол",
        )
        session.add(t_msg)
        await session.commit()

        # 2.1 Номер внутри цитируемого текста
        res_num = await _resolve_by_quoted_text(
            session, user.id, f"Уведомление по заявке #{ticket.id}", "Ответ"
        )
        assert res_num is not None
        assert res_num[0].id == ticket.id

        # 2.2 Совпадение с описанием заявки (подстрока)
        res_desc = await _resolve_by_quoted_text(
            session, user.id, "Батарея холодная в 404", "Ответ по описанию"
        )
        assert res_desc is not None
        assert res_desc[0].id == ticket.id

        # 2.3 Совпадение с сообщением в истории (TicketMessage)
        res_msg = await _resolve_by_quoted_text(
            session, user.id, "течет прямо на пол", "Срочно выезжаем"
        )
        assert res_msg is not None
        assert res_msg[0].id == ticket.id

        # 2.4 Совпадение с response_text
        res_resp = await _resolve_by_quoted_text(
            session, user.id, "Сантехник уведомлен", "Дополнительный комментарий"
        )
        assert res_resp is not None
        assert res_resp[0].id == ticket.id

        # 2.5 Номер в цитате есть, но такой заявки не существует -> не падает, ищет дальше
        res_nonexistent_num = await _resolve_by_quoted_text(
            session, user.id, "Заявка #999999 не существует", "Ответ"
        )
        assert res_nonexistent_num is None

        # Нет совпадений
        res_miss = await _resolve_by_quoted_text(
            session, user.id, "Совершенно посторонний текст", "Ответ"
        )
        assert res_miss is None


@pytest.mark.asyncio
async def test_resolve_fallback(db_session_maker):
    """_resolve_fallback отдает приоритет активным заявкам перед завершенными."""
    now = datetime.now(UTC)
    async with db_session_maker() as session:
        user = User(vk_id=4001, full_name="Студент с несколькими заявками")
        session.add(user)
        await session.flush()

        # Завершенная заявка создана позже
        t_completed = Ticket(
            user_id=user.id,
            topic="Старая закрытая",
            status=TicketStatus.COMPLETED,
            created_at=now - timedelta(hours=1),
        )
        # Активная заявка создана раньше
        t_active = Ticket(
            user_id=user.id,
            topic="Активная заявка",
            status=TicketStatus.NEW,
            created_at=now - timedelta(hours=2),
        )
        session.add_all([t_completed, t_active])
        await session.commit()

        # Фоллбэк должен выбрать t_active, несмотря на то, что t_completed свежее
        cand, text = await _resolve_fallback(session, user.id, "Сообщение без контекста")
        assert cand is not None
        assert cand.id == t_active.id
        assert text == "Сообщение без контекста"

        # Если активных нет, фоллбэк выбирает последнюю завершенную
        t_active.status = TicketStatus.COMPLETED
        await session.commit()

        cand_latest, _ = await _resolve_fallback(session, user.id, "Ещё сообщение")
        assert cand_latest is not None
        assert cand_latest.id == t_completed.id

        # Если у пользователя нет заявок
        u_empty = User(vk_id=4002, full_name="Студент без заявок")
        session.add(u_empty)
        await session.commit()
        cand_none, _ = await _resolve_fallback(session, u_empty.id, "Тест")
        assert cand_none is None


@pytest.mark.asyncio
async def test_resolve_target_ticket_chain(db_session_maker):
    """resolve_target_ticket проходит по цепочке: префикс -> цитаты -> фоллбэк."""
    now = datetime.now(UTC)
    async with db_session_maker() as session:
        user = User(vk_id=5001, full_name="Студент цепочки")
        session.add(user)
        await session.flush()

        t1 = Ticket(
            user_id=user.id,
            topic="Первая",
            description="Описание 1",
            status=TicketStatus.NEW,
            created_at=now - timedelta(minutes=10),
        )
        t2 = Ticket(
            user_id=user.id,
            topic="Вторая",
            description="Описание 2",
            status=TicketStatus.NEW,
            created_at=now,
        )
        session.add_all([t1, t2])
        await session.commit()

        # 1. Срабатывает префикс
        t_pref, txt_pref = await resolve_target_ticket(
            session, user.id, f"[#{t1.id}] Префиксный ответ"
        )
        assert t_pref.id == t1.id
        assert txt_pref == "Префиксный ответ"

        # 2. Префикса нет, срабатывает цитата второй заявки
        # Проверяем также цепочку с пустым сообщением и несовпадающей цитатой
        t_quote, txt_quote = await resolve_target_ticket(
            session,
            user.id,
            "Ответ на цитату",
            reply_message={"text": "  ", "reply_message": {"text": "Неизвестный вопрос"}},
            fwd_messages=[{"text": "Описание 2"}],
        )
        assert t_quote.id == t2.id
        assert txt_quote == "Ответ на цитату"

        # 3. Ни префикса, ни цитаты -> фоллбэк на последнюю открытую
        t_fallback, txt_fb = await resolve_target_ticket(session, user.id, "Чистый фоллбэк")
        assert t_fallback.id == t2.id
        assert txt_fb == "Чистый фоллбэк"


@pytest.mark.asyncio
async def test_resolve_admin_name(db_session_maker):
    """_resolve_admin_name определяет имя из БД, через VK API или фоллбэк."""
    async with db_session_maker() as session:
        # 1. admin_author_id is None -> Сообщество VK
        assert await _resolve_admin_name(session, None) == "Сообщество VK"

        # 2. Пользователь найден в БД с full_name
        admin_user = User(vk_id=911, full_name="Анна Модератор")
        session.add(admin_user)
        await session.commit()
        assert await _resolve_admin_name(session, 911) == "Анна Модератор"

        # 3. Пользователь в БД без full_name, VK API возвращает имя
        admin_user_no_name = User(vk_id=912, full_name=None)
        session.add(admin_user_no_name)
        await session.commit()

        with patch("core.vk_client.fetch_vk_user_name", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = "Ольга Администратор"
            name = await _resolve_admin_name(session, 912)
            assert name == "Ольга Администратор"
            assert admin_user_no_name.full_name == "Ольга Администратор"

        # 4. Пользователя нет в БД, VK API возвращает имя
        with patch("core.vk_client.fetch_vk_user_name", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = "Дмитрий Поддержка"
            assert await _resolve_admin_name(session, 913) == "Дмитрий Поддержка"

        # 5. VK API возвращает None -> фоллбэк на "VK ID {id}"
        with patch("core.vk_client.fetch_vk_user_name", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            assert await _resolve_admin_name(session, 914) == "VK ID 914"


# ==============================================================================
# 3. Тесты handle_community_message_reply (основной интеграционный сценарий)
# ==============================================================================


@pytest.mark.asyncio
async def test_handle_community_message_reply_ignore_conditions(db_session_maker):
    """handle_community_message_reply игнорирует невалидные и системные сообщения."""
    # Пустой текст или peer_id
    assert (
        await handle_community_message_reply(peer_id=0, text="Ответ", admin_author_id=10) is None
    )
    assert await handle_community_message_reply(peer_id=123, text="", admin_author_id=10) is None
    assert (
        await handle_community_message_reply(peer_id=123, text="   ", admin_author_id=10) is None
    )

    # Системные шаблоны автоответа бота
    assert (
        await handle_community_message_reply(
            peer_id=123, text="Ваше обращение принято к рассмотрению", admin_author_id=10
        )
        is None
    )
    assert (
        await handle_community_message_reply(
            peer_id=123, text="Главное меню сообщества", admin_author_id=10
        )
        is None
    )

    # Сообщение отправлено ботом / через API без реального admin_author_id
    assert (
        await handle_community_message_reply(peer_id=123, text="Живой ответ", admin_author_id=None)
        is None
    )
    assert (
        await handle_community_message_reply(peer_id=123, text="Живой ответ", admin_author_id=0)
        is None
    )
    assert (
        await handle_community_message_reply(peer_id=123, text="Живой ответ", admin_author_id=-5)
        is None
    )

    # Студент не найден в БД
    assert (
        await handle_community_message_reply(
            peer_id=999888777, text="Ответ неизвестному", admin_author_id=100
        )
        is None
    )

    # Студент есть, но у него нет никаких заявок
    async with db_session_maker() as session:
        user_no_tickets = User(vk_id=888111, full_name="Студент без заявок")
        session.add(user_no_tickets)
        await session.commit()

    assert (
        await handle_community_message_reply(
            peer_id=888111, text="Ответ по воздуху", admin_author_id=100
        )
        is None
    )


@pytest.mark.asyncio
async def test_handle_community_message_reply_by_prefix_success(db_session_maker):
    """Успешная обработка ответа по префиксу с переводом статуса, историей и аудитом."""
    student_vk = 770001
    admin_vk = 880001

    async with db_session_maker() as session:
        student = User(vk_id=student_vk, full_name="Иван Заявитель")
        admin = User(vk_id=admin_vk, full_name="Мария Админ")
        session.add_all([student, admin])
        await session.flush()

        ticket = Ticket(
            user_id=student.id,
            topic="Сломанный замок",
            description="Замок в блоке заедает",
            status=TicketStatus.NEW,
        )
        session.add(ticket)
        await session.commit()
        ticket_id = ticket.id

    with patch("core.events.notify_ticket_change") as mock_notify:
        updated_ticket = await handle_community_message_reply(
            peer_id=student_vk,
            text=f"[#{ticket_id}] Заявка передана мастеру, ожидайте сегодня до 18:00",
            admin_author_id=admin_vk,
        )

        assert updated_ticket is not None
        assert updated_ticket.id == ticket_id
        assert updated_ticket.status == TicketStatus.IN_PROGRESS
        assert updated_ticket.response_text == "Заявка передана мастеру, ожидайте сегодня до 18:00"
        mock_notify.assert_called_once()

    # Проверяем записи в БД: сообщения истории и журнал аудита
    async with db_session_maker() as session:
        t = await session.get(Ticket, ticket_id)
        assert t is not None
        assert t.status == TicketStatus.IN_PROGRESS
        assert t.response_text == "Заявка передана мастеру, ожидайте сегодня до 18:00"

        # Проверяем сообщения истории
        messages = (
            await session.scalars(
                select(TicketMessage)
                .where(TicketMessage.ticket_id == ticket_id)
                .order_by(TicketMessage.id.asc())
            )
        ).all()
        assert len(messages) == 2

        # 1. Сообщение администратора
        admin_msg = messages[0]
        assert admin_msg.author_type == MessageAuthorType.ADMIN
        assert admin_msg.author_vk_id == admin_vk
        assert admin_msg.message == "Заявка передана мастеру, ожидайте сегодня до 18:00"

        # 2. Системное сообщение о смене статуса
        sys_msg = messages[1]
        assert sys_msg.author_type == MessageAuthorType.SYSTEM
        assert "Статус автоматически изменён: Новое -> В обработке" in sys_msg.message
        assert "Мария Админ" in sys_msg.message

        # Проверяем запись в таблице Log
        logs = (
            await session.scalars(
                select(Log).where(Log.user_id == student.id, Log.action == "community_reply")
            )
        ).all()
        assert len(logs) == 1
        log_entry = logs[0]
        assert f"на заявку #{ticket_id}" in log_entry.details
        assert "Мария Админ" in log_entry.details
        assert "В обработке" in log_entry.details


@pytest.mark.asyncio
async def test_handle_community_message_reply_by_quoted_text_mention(db_session_maker):
    """Поиск заявки через номер в цитируемом сообщении: 'По поводу заявки #123 всё сделано'."""
    student_vk = 770002
    admin_vk = 880002

    async with db_session_maker() as session:
        student = User(vk_id=student_vk, full_name="Петр Студент")
        session.add(student)
        await session.flush()

        t1 = Ticket(user_id=student.id, topic="Заявка 1", status=TicketStatus.NEW)
        t2 = Ticket(user_id=student.id, topic="Заявка 2", status=TicketStatus.NEW)
        session.add_all([t1, t2])
        await session.commit()
        t2_id = t2.id

    updated = await handle_community_message_reply(
        peer_id=student_vk,
        text="Приняли, чиним",
        admin_author_id=admin_vk,
        reply_message={"text": f"По поводу заявки #{t2_id} всё сделано"},
    )

    assert updated is not None
    assert updated.id == t2_id
    assert updated.status == TicketStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_handle_community_message_reply_already_in_progress(db_session_maker):
    """Если заявка уже IN_PROGRESS, системное сообщение о смене статуса повторно не пишется."""
    student_vk = 770003
    admin_vk = 880003

    async with db_session_maker() as session:
        student = User(vk_id=student_vk, full_name="Сергей Студент")
        admin = User(vk_id=admin_vk, full_name="Оператор")
        session.add_all([student, admin])
        await session.flush()

        ticket = Ticket(
            user_id=student.id,
            topic="Тема",
            description="Описание",
            status=TicketStatus.IN_PROGRESS,  # уже в обработке
        )
        session.add(ticket)
        await session.commit()
        t_id = ticket.id

    updated = await handle_community_message_reply(
        peer_id=student_vk,
        text="Второе сообщение оператора",
        admin_author_id=admin_vk,
    )

    assert updated is not None
    assert updated.id == t_id
    assert updated.status == TicketStatus.IN_PROGRESS

    async with db_session_maker() as session:
        messages = (
            await session.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t_id))
        ).all()
        # Должно быть только 1 сообщение (ADMIN), системное сообщение не создается
        assert len(messages) == 1
        assert messages[0].author_type == MessageAuthorType.ADMIN
        assert messages[0].message == "Второе сообщение оператора"


@pytest.mark.asyncio
async def test_handle_community_message_reply_reopens_completed_ticket(db_session_maker):
    """Если у студента есть только завершенная заявка, фоллбэк переоткрывает её в IN_PROGRESS."""
    student_vk = 770004
    admin_vk = 880004

    async with db_session_maker() as session:
        student = User(vk_id=student_vk, full_name="Выпускник")
        session.add(student)
        await session.flush()

        ticket = Ticket(
            user_id=student.id,
            topic="Старая тема",
            status=TicketStatus.COMPLETED,
        )
        session.add(ticket)
        await session.commit()
        t_id = ticket.id

    updated = await handle_community_message_reply(
        peer_id=student_vk,
        text="Возобновляем работу по вашему обращению",
        admin_author_id=admin_vk,
    )

    assert updated is not None
    assert updated.id == t_id
    assert updated.status == TicketStatus.IN_PROGRESS

    async with db_session_maker() as session:
        messages = (
            await session.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t_id))
        ).all()
        assert len(messages) == 2
        types = [m.author_type for m in messages]
        assert MessageAuthorType.ADMIN in types
        assert MessageAuthorType.SYSTEM in types
