"""
Тесты массового импорта и экспорта данных (Excel/CSV) для Частых вопросов и Базы знаний,
а также работы обновлённых хендлеров бота с инструкциями.
"""

import io
from unittest.mock import AsyncMock

import openpyxl
import pytest

from bots.vk.handlers.faq import (
    faq_handler,
)
from bots.vk.handlers.knowledge import (
    knowledge_base_handler,
)
from core.bulk_import import (
    export_faq_xlsx,
    export_knowledge_xlsx,
    generate_faq_template_csv,
    generate_faq_template_xlsx,
    generate_knowledge_template_csv,
    generate_knowledge_template_xlsx,
    parse_faq_rows,
    parse_file_or_text,
    parse_knowledge_rows,
)


def test_generate_faq_template_xlsx():
    data = generate_faq_template_xlsx()
    assert isinstance(data, bytes)
    assert len(data) > 100
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws.title == "Частые вопросы"
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Вопрос"
    assert rows[0][1] == "Ответ"
    assert len(rows) > 1


def test_generate_faq_template_csv():
    data = generate_faq_template_csv()
    assert "Вопрос;Ответ" in data
    assert "Сколько всего общежитий" in data


def test_generate_knowledge_template_xlsx():
    data = generate_knowledge_template_xlsx()
    assert isinstance(data, bytes)
    assert len(data) > 100
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws.title == "База знаний"
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Ключевые слова"


def test_generate_knowledge_template_csv():
    data = generate_knowledge_template_csv()
    assert "Ключевые слова;Материал / Ответ" in data
    assert "сообщество" in data


def test_parse_faq_rows():
    raw = [
        ["Вопрос", "Ответ", "Отдел"],
        ["Где получить пропуск?", "На вахте общежития", "Жилищно-бытовой"],
        ["", "", ""],
        ["Как записаться?", "В боте", "Культмасс"],
    ]
    parsed = parse_faq_rows(raw)
    assert len(parsed) == 2
    assert parsed[0]["question"] == "Где получить пропуск?"
    assert parsed[0]["answer"] == "На вахте общежития"
    assert parsed[0]["department"] == "Жилищно-бытовой"
    assert parsed[1]["question"] == "Как записаться?"


def test_parse_knowledge_rows():
    raw = [
        ["Ключевые слова", "Ответ / Материал", "Отдел"],
        ["сантехника, кран", "Звоните мастеру", "Жилбыт"],
        ["мероприятия", "Смотрите афишу ВК", "Культмасс"],
    ]
    parsed = parse_knowledge_rows(raw)
    assert len(parsed) == 2
    assert parsed[0]["keywords"] == "сантехника, кран"
    assert parsed[0]["answer"] == "Звоните мастеру"


def test_parse_file_or_text_csv():
    csv_text = "Вопрос;Ответ;Отдел\nКак заселиться?;С паспортом;Жилбыт\n"
    raw = parse_file_or_text(None, None, csv_text)
    assert len(raw) == 2
    parsed = parse_faq_rows(raw)
    assert len(parsed) == 1
    assert parsed[0]["question"] == "Как заселиться?"


def test_export_faq_and_knowledge_xlsx():
    class DummyDept:
        name = "Информационный"

    class DummyFAQ:
        id = 1
        department = DummyDept()
        question = "Тестовый вопрос?"
        final_answer = "Тестовый ответ"

    faq_bytes = export_faq_xlsx([DummyFAQ()])
    assert len(faq_bytes) > 100

    class DummyKB:
        id = 1
        department = DummyDept()
        keywords = "тест, справка"
        answer = "Информация о тесте"

    kb_bytes = export_knowledge_xlsx([DummyKB()])
    assert len(kb_bytes) > 100


@pytest.mark.asyncio
async def test_faq_handler_returns_instructions(db_session_maker):
    msg = AsyncMock()
    msg.text = "Частые вопросы"
    msg.from_id = 12345

    await faq_handler(msg)

    assert msg.answer.called
    sent_text = msg.answer.call_args[0][0]
    assert "Инструкция по разделу «Частые вопросы»" in sent_text
    assert "Как пользоваться" in sent_text


@pytest.mark.asyncio
async def test_knowledge_base_handler_returns_instructions(db_session_maker):
    msg = AsyncMock()
    msg.text = "База знаний"
    msg.from_id = 12345

    await knowledge_base_handler(msg)

    assert msg.answer.called
    sent_text = msg.answer.call_args[0][0]
    assert "Инструкция по Базе знаний" in sent_text
    assert "Как пользоваться" in sent_text


def _login(client):
    import re

    login_page = client.get("/auth/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": csrf_token},
        follow_redirects=False,
    )


def test_faq_web_endpoints(web_client):
    _login(web_client)

    resp = web_client.get("/faq/template.xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]

    resp = web_client.get("/faq/template.csv")
    assert resp.status_code == 200
    assert "Вопрос;Ответ" in resp.text

    resp = web_client.get("/faq/export.xlsx")
    assert resp.status_code == 200


def test_knowledge_web_endpoints(web_client):
    _login(web_client)

    resp = web_client.get("/knowledge/template.xlsx")
    assert resp.status_code == 200

    resp = web_client.get("/knowledge/template.csv")
    assert resp.status_code == 200

    resp = web_client.get("/knowledge/export.xlsx")
    assert resp.status_code == 200


def test_faq_and_knowledge_bulk_import_post(web_client):
    import re

    _login(web_client)

    # FAQ import
    faq_page = web_client.get("/faq/")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', faq_page.text).group(1)
    csv_text = "Вопрос;Ответ\nГде коворкинг?;На 3 этаже главного здания\n"
    resp = web_client.post(
        "/faq/import",
        data={"text_data": csv_text, "csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Успешно загружено" in resp.text

    # Knowledge import
    kb_page = web_client.get("/knowledge/")
    csrf_kb = re.search(r'name="csrf_token" value="([^"]+)"', kb_page.text).group(1)
    kb_csv = "Ключевые слова;Ответ\nспортзал, тренировки;Спортзал открыт с 8 до 22\n"
    resp_kb = web_client.post(
        "/knowledge/import",
        data={"text_data": kb_csv, "csrf_token": csrf_kb},
        follow_redirects=True,
    )
    assert resp_kb.status_code == 200
    assert "Успешно загружено" in resp_kb.text
