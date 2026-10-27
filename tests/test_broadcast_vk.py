"""Тесты функционала рассылки в VK-боте (bots/vk/handlers/admin.py) по стандарту Broadcast 2.0."""

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from bots.vk.bot import vk_bot
from bots.vk.common import BroadcastStates
from bots.vk.handlers.admin import (
    _extract_wall_attachment,
    broadcast_comment_choice_handler,
    broadcast_confirm_handler,
    broadcast_count_handler,
    broadcast_direct_handler,
    broadcast_exclude_handler,
    broadcast_start_handler,
    broadcast_text_handler,
)
from bots.vk.keyboards import (
    build_broadcast_comment_choice_keyboard,
    build_broadcast_confirm_keyboard,
    build_broadcast_exclude_keyboard,
)
from core.models import Admin, User, UserRole, VkOutbox, WebRole, WebUser

EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u26ff]|[\u2700-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]"
)


def _assert_no_emoji(text: str) -> None:
    """Проверить строгое отсутствие эмодзи и графических пиктограмм в строке."""
    matches = EMOJI_PATTERN.findall(text)
    assert not matches, f"Обнаружены эмодзи в тексте: {matches!r} в строке: {text!r}"


@pytest.fixture
async def setup_vk_test_data(db_session_maker):
    """Подготовка тестовых пользователей и администраторов в базе."""
    async with db_session_maker() as session:
        # 1. Обычный студент
        u_student = User(vk_id=1001, full_name="Студент Обычный")
        # 2. Обычный админ отдела
        u_admin = User(vk_id=1002, full_name="Админ Отдела")
        # 3. Суперадмин
        u_super = User(vk_id=1003, full_name="Главный Админ")
        session.add_all([u_student, u_admin, u_super])
        await session.flush()

        # Назначаем роли
        adm_dept = Admin(user_id=u_admin.id, role=UserRole.ADMIN, department_id=1)
        adm_super = Admin(user_id=u_super.id, role=UserRole.SUPERADMIN)
        session.add_all([adm_dept, adm_super])

        # Веб-пользователь суперадмина
        web_super = WebUser(
            username="super_bot_tester",
            password_hash="fake",
            role=WebRole.SUPERADMIN,
            is_active=True,
            admin_id=adm_super.id,
        )
        session.add(web_super)
        await session.commit()


@pytest.mark.asyncio
async def test_broadcast_start_non_admin_forbidden(setup_vk_test_data):
    """Обычный студент не имеет доступа к команде 'Рассылка'."""
    message = AsyncMock()
    message.from_id = 1001
    message.text = "Рассылка"
    message.answer = AsyncMock()

    await broadcast_start_handler(message)

    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_start_dept_admin_forbidden(setup_vk_test_data):
    """Админ отдела не может запускать рассылку (только суперадмин)."""
    message = AsyncMock()
    message.from_id = 1002
    message.text = "Рассылка"
    message.answer = AsyncMock()

    await broadcast_start_handler(message)

    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_full_fsm_flow(setup_vk_test_data, monkeypatch):
    """Полный FSM сценарий рассылки суперадмином: старт -> ввод N -> исключение -> текст -> подтверждение."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)

    # 1. Старт
    msg_start = AsyncMock()
    msg_start.from_id = 1003
    msg_start.text = "Рассылка"
    msg_start.answer = AsyncMock()

    await broadcast_start_handler(msg_start)

    msg_start.answer.assert_called_once()
    start_reply = msg_start.answer.call_args[0][0]
    assert "Массовая рассылка сообщений" in start_reply
    _assert_no_emoji(start_reply)
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_COUNT

    # 2. Некорректный ввод числа N
    msg_bad_count = AsyncMock()
    msg_bad_count.from_id = 1003
    msg_bad_count.text = "сто"
    msg_bad_count.answer = AsyncMock()

    await broadcast_count_handler(msg_bad_count)
    bad_count_reply = msg_bad_count.answer.call_args[0][0]
    assert "положительное целое число" in bad_count_reply
    _assert_no_emoji(bad_count_reply)

    # 3. Корректный ввод N (count=2 <= 10 -> переходит в WAITING_EXCLUDE)
    msg_count = AsyncMock()
    msg_count.from_id = 1003
    msg_count.text = "2"
    msg_count.answer = AsyncMock()

    await broadcast_count_handler(msg_count)
    count_reply = msg_count.answer.call_args[0][0]
    assert "Выбрано пользователей: 2" in count_reply
    _assert_no_emoji(count_reply)
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_EXCLUDE
    assert current_state.payload.get("count") == 2
    assert len(current_state.payload.get("recipients", [])) == 2

    # 4. Нажатие «Продолжить» в WAITING_EXCLUDE -> переход в WAITING_TEXT
    msg_continue = AsyncMock()
    msg_continue.from_id = 1003
    msg_continue.text = "Продолжить"
    msg_continue.answer = AsyncMock()

    await broadcast_exclude_handler(msg_continue)
    continue_reply = msg_continue.answer.call_args[0][0]
    assert "Введите текст сообщения для рассылки или перешлите пост" in continue_reply
    _assert_no_emoji(continue_reply)
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_TEXT

    # 5. Ввод текста рассылки
    msg_text = AsyncMock()
    msg_text.from_id = 1003
    msg_text.text = "Завтра день донора в клубе общежития!"
    msg_text.answer = AsyncMock()

    await broadcast_text_handler(msg_text)
    text_reply = msg_text.answer.call_args[0][0]
    assert "Подтверждение рассылки" in text_reply
    assert "Получателей: 2 пользователей" in text_reply
    assert "Завтра день донора в клубе общежития!" in text_reply
    _assert_no_emoji(text_reply)
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_CONFIRM
    assert current_state.payload.get("text") == "Завтра день донора в клубе общежития!"

    # 6. Подтверждение рассылки
    msg_confirm = AsyncMock()
    msg_confirm.from_id = 1003
    msg_confirm.text = "Подтвердить рассылку"
    msg_confirm.answer = AsyncMock()

    await broadcast_confirm_handler(msg_confirm)
    confirm_reply = msg_confirm.answer.call_args[0][0]
    assert "Рассылка успешно запущена" in confirm_reply
    assert "Поставлено в очередь отправки: 2 пользователей" in confirm_reply
    _assert_no_emoji(confirm_reply)
    # Состояние должно быть очищено
    assert await vk_bot.state_dispenser.get(1003) is None


@pytest.mark.asyncio
async def test_broadcast_exclude_recipients_scenario(setup_vk_test_data, monkeypatch):
    """Тест сценария исключения получателей кнопками [Исключить #N] и номерами."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)

    # Задаем ввод 3 пользователей
    msg_count = AsyncMock()
    msg_count.from_id = 1003
    msg_count.text = "3"
    msg_count.answer = AsyncMock()

    await broadcast_count_handler(msg_count)
    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_EXCLUDE
    recipients = state.payload.get("recipients", [])
    assert len(recipients) == 3

    # 1. Исключаем пользователя #1 кнопкой «Исключить #1»
    msg_ex1 = AsyncMock()
    msg_ex1.from_id = 1003
    msg_ex1.text = "Исключить #1"
    msg_ex1.answer = AsyncMock()

    await broadcast_exclude_handler(msg_ex1)
    reply1 = msg_ex1.answer.call_args[0][0]
    assert "исключен из рассылки" in reply1
    assert "1. " in reply1 and "[Исключен]" in reply1
    _assert_no_emoji(reply1)

    state = await vk_bot.state_dispenser.get(1003)
    assert recipients[0]["id"] in state.payload.get("excluded_ids", [])

    # 2. Восстанавливаем пользователя #1 повторным нажатием «Вернуть #1»
    msg_ret1 = AsyncMock()
    msg_ret1.from_id = 1003
    msg_ret1.text = "Вернуть #1"
    msg_ret1.answer = AsyncMock()

    await broadcast_exclude_handler(msg_ret1)
    reply_ret = msg_ret1.answer.call_args[0][0]
    assert "возвращен в список рассылки" in reply_ret
    _assert_no_emoji(reply_ret)

    state = await vk_bot.state_dispenser.get(1003)
    assert recipients[0]["id"] not in state.payload.get("excluded_ids", [])

    # 3. Исключаем пользователя #2 вводом простого числа «2»
    msg_ex2 = AsyncMock()
    msg_ex2.from_id = 1003
    msg_ex2.text = "2"
    msg_ex2.answer = AsyncMock()

    await broadcast_exclude_handler(msg_ex2)
    reply2 = msg_ex2.answer.call_args[0][0]
    assert "исключен из рассылки" in reply2
    _assert_no_emoji(reply2)

    state = await vk_bot.state_dispenser.get(1003)
    assert recipients[1]["id"] in state.payload.get("excluded_ids", [])

    # 4. Нажимаем «Продолжить»
    msg_cont = AsyncMock()
    msg_cont.from_id = 1003
    msg_cont.text = "Продолжить"
    msg_cont.answer = AsyncMock()

    await broadcast_exclude_handler(msg_cont)
    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_TEXT

    # 5. Вводим текст
    msg_text = AsyncMock()
    msg_text.from_id = 1003
    msg_text.text = "Уведомление об уборке территории"
    msg_text.answer = AsyncMock()

    await broadcast_text_handler(msg_text)
    confirm_reply = msg_text.answer.call_args[0][0]
    # Должно быть: Получателей: 2 (исключено: 1)
    assert "Получателей: 2 (исключено: 1)" in confirm_reply
    _assert_no_emoji(confirm_reply)

    # 6. Подтверждаем отправку
    msg_conf = AsyncMock()
    msg_conf.from_id = 1003
    msg_conf.text = "Подтвердить рассылку"
    msg_conf.answer = AsyncMock()

    await broadcast_confirm_handler(msg_conf)
    success_reply = msg_conf.answer.call_args[0][0]
    assert "Поставлено в очередь отправки: 2 пользователей" in success_reply
    _assert_no_emoji(success_reply)


@pytest.mark.asyncio
async def test_broadcast_forward_wall_without_comment(
    setup_vk_test_data, db_session_maker, monkeypatch
):
    """Сценарий 1: пересылка поста из сообщества с выбором «Отправить без комментария»."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)

    # Настраиваем FSM сразу на WAITING_TEXT для 2 получателей
    await vk_bot.state_dispenser.set(
        1003,
        BroadcastStates.WAITING_TEXT,
        count=2,
        recipients=[],
        excluded_ids=[],
    )

    # Администратор пересылает пост из группы (вложение wall-207038317_1234)
    msg_post = AsyncMock()
    msg_post.from_id = 1003
    msg_post.text = ""
    # Моделируем vkbottle attachment
    wall_att = MagicMock()
    wall_att.type = "wall"
    wall_att.wall = MagicMock(owner_id=-207038317, id=1234)
    msg_post.attachments = [wall_att]
    msg_post.fwd_messages = None
    msg_post.reply_message = None
    msg_post.answer = AsyncMock()

    await broadcast_text_handler(msg_post)

    msg_post.answer.assert_called_once()
    reply = msg_post.answer.call_args[0][0]
    assert "Прикреплен пост: wall-207038317_1234" in reply
    assert "Хотите добавить свой комментарий к рассылке?" in reply
    _assert_no_emoji(reply)

    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_COMMENT_CHOICE
    assert state.payload.get("attachment") == "wall-207038317_1234"

    # Выбираем «Отправить без комментария»
    msg_choice = AsyncMock()
    msg_choice.from_id = 1003
    msg_choice.text = "Отправить без комментария"
    msg_choice.answer = AsyncMock()

    await broadcast_comment_choice_handler(msg_choice)

    choice_reply = msg_choice.answer.call_args[0][0]
    assert "Подтверждение рассылки" in choice_reply
    assert "Прикрепленный пост: wall-207038317_1234" in choice_reply
    assert "Текст: без комментария" in choice_reply
    _assert_no_emoji(choice_reply)

    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_CONFIRM
    assert state.payload.get("text") == ""
    assert state.payload.get("attachment") == "wall-207038317_1234"

    # Подтверждаем рассылку
    msg_conf = AsyncMock()
    msg_conf.from_id = 1003
    msg_conf.text = "Подтвердить рассылку"
    msg_conf.answer = AsyncMock()

    await broadcast_confirm_handler(msg_conf)
    success_reply = msg_conf.answer.call_args[0][0]
    assert "Рассылка успешно запущена" in success_reply
    _assert_no_emoji(success_reply)

    # Проверяем, что в VkOutbox попала запись с attachment='wall-207038317_1234' и пустым текстом
    async with db_session_maker() as session:
        entries = list(await session.scalars(select(VkOutbox)))
        assert len(entries) >= 2
        for entry in entries:
            assert entry.attachment == "wall-207038317_1234"
            assert entry.text == ""


@pytest.mark.asyncio
async def test_broadcast_forward_wall_with_comment(
    setup_vk_test_data, db_session_maker, monkeypatch
):
    """Сценарий пересылки поста из сообщества с добавлением текстового комментария."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)

    await vk_bot.state_dispenser.set(
        1003,
        BroadcastStates.WAITING_COMMENT_CHOICE,
        count=1,
        recipients=[],
        excluded_ids=[],
        attachment="wall-207038317_5678",
    )

    # Выбираем «Добавить комментарий»
    msg_choice = AsyncMock()
    msg_choice.from_id = 1003
    msg_choice.text = "Добавить комментарий"
    msg_choice.answer = AsyncMock()

    await broadcast_comment_choice_handler(msg_choice)
    choice_reply = msg_choice.answer.call_args[0][0]
    assert "Введите текст комментария к посту" in choice_reply
    _assert_no_emoji(choice_reply)

    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_TEXT
    assert state.payload.get("adding_comment") is True

    # Вводим текст комментария
    msg_comment = AsyncMock()
    msg_comment.from_id = 1003
    msg_comment.text = "Обязательно ознакомьтесь с новым регламентом!"
    msg_comment.answer = AsyncMock()

    await broadcast_text_handler(msg_comment)
    confirm_reply = msg_comment.answer.call_args[0][0]
    assert "Прикрепленный пост: wall-207038317_5678" in confirm_reply
    assert "Обязательно ознакомьтесь с новым регламентом!" in confirm_reply
    _assert_no_emoji(confirm_reply)

    state = await vk_bot.state_dispenser.get(1003)
    assert state.state == BroadcastStates.WAITING_CONFIRM
    assert state.payload.get("text") == "Обязательно ознакомьтесь с новым регламентом!"

    # Подтверждаем рассылку
    msg_conf = AsyncMock()
    msg_conf.from_id = 1003
    msg_conf.text = "Подтвердить рассылку"
    msg_conf.answer = AsyncMock()

    await broadcast_confirm_handler(msg_conf)
    _assert_no_emoji(msg_conf.answer.call_args[0][0])

    # Проверяем, что в VkOutbox попала запись с комментарием и attachment
    async with db_session_maker() as session:
        entries = list(await session.scalars(select(VkOutbox)))
        matching = [e for e in entries if e.attachment == "wall-207038317_5678"]
        assert len(matching) >= 1
        assert matching[0].text == "Обязательно ознакомьтесь с новым регламентом!"


@pytest.mark.asyncio
async def test_extract_wall_attachment_variations():
    """Тестирование извлечения вложений wall и photo из всех источников."""
    # 1. Прямой пост в attachments (dict)
    msg1 = MagicMock()
    msg1.attachments = [{"type": "wall", "wall": {"owner_id": -207038317, "id": 100}}]
    msg1.fwd_messages = None
    msg1.reply_message = None
    msg1.text = ""
    assert _extract_wall_attachment(msg1) == "wall-207038317_100"

    # 2. Фото в attachments с access_key
    msg2 = MagicMock()
    msg2.attachments = [
        {"type": "photo", "photo": {"owner_id": 1003, "id": 777, "access_key": "sec123"}}
    ]
    msg2.fwd_messages = None
    msg2.reply_message = None
    msg2.text = ""
    assert _extract_wall_attachment(msg2) == "photo1003_777_sec123"

    # 3. Пересланное сообщение с постом
    msg3 = MagicMock()
    msg3.attachments = []
    fwd = MagicMock()
    fwd.attachments = [{"type": "wall", "wall": {"owner_id": -100, "id": 55}}]
    fwd.text = ""
    msg3.fwd_messages = [fwd]
    msg3.reply_message = None
    msg3.text = ""
    assert _extract_wall_attachment(msg3) == "wall-100_55"

    # 4. Ответ (reply_message) с ссылкой в тексте
    msg4 = MagicMock()
    msg4.attachments = []
    msg4.fwd_messages = []
    reply = MagicMock()
    reply.attachments = []
    reply.text = "Смотрите тут: https://vk.com/wall-207038317_444"
    msg4.reply_message = reply
    msg4.text = ""
    assert _extract_wall_attachment(msg4) == "wall-207038317_444"

    # 5. Ссылка в самом тексте
    msg5 = MagicMock()
    msg5.attachments = []
    msg5.fwd_messages = []
    msg5.reply_message = None
    msg5.text = "Пост vk.com/wall-207038317_9999"
    assert _extract_wall_attachment(msg5) == "wall-207038317_9999"


def test_keyboards_strictly_no_emoji():
    """Тест проверки полного отсутствия эмодзи во всех кнопках клавиатур рассылки."""
    kb_exclude = build_broadcast_exclude_keyboard(candidates_count=5, excluded_indices={1, 3})
    parsed_ex = json.loads(kb_exclude)
    for row in parsed_ex["buttons"]:
        for btn in row:
            label = btn["action"]["label"]
            _assert_no_emoji(label)

    kb_comment = build_broadcast_comment_choice_keyboard()
    parsed_com = json.loads(kb_comment)
    for row in parsed_com["buttons"]:
        for btn in row:
            label = btn["action"]["label"]
            _assert_no_emoji(label)

    kb_confirm = build_broadcast_confirm_keyboard()
    parsed_conf = json.loads(kb_confirm)
    for row in parsed_conf["buttons"]:
        for btn in row:
            label = btn["action"]["label"]
            _assert_no_emoji(label)


@pytest.mark.asyncio
async def test_broadcast_cancel_flow(setup_vk_test_data):
    """Отмена рассылки на шаге ввода N."""
    await vk_bot.state_dispenser.set(1003, BroadcastStates.WAITING_COUNT)

    msg_cancel = AsyncMock()
    msg_cancel.from_id = 1003
    msg_cancel.text = "Отмена"
    msg_cancel.answer = AsyncMock()

    await broadcast_count_handler(msg_cancel)

    reply = msg_cancel.answer.call_args[0][0]
    assert "Рассылка отменена" in reply
    _assert_no_emoji(reply)
    assert await vk_bot.state_dispenser.get(1003) is None


@pytest.mark.asyncio
async def test_broadcast_direct_command(setup_vk_test_data):
    """Быстрая команда: Рассылка 3 Срочная новость."""
    msg = AsyncMock()
    msg.from_id = 1003
    msg.text = "Рассылка 3 Срочное собрание студсовета"
    msg.answer = AsyncMock()

    await broadcast_direct_handler(msg)

    msg.answer.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "Подтверждение рассылки" in reply
    _assert_no_emoji(reply)
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_CONFIRM
    assert current_state.payload.get("count") == 3
    assert current_state.payload.get("text") == "Срочное собрание студсовета"
