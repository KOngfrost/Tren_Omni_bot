"""E2E-тесты кнопок веб-панели в реальном браузере Chromium (этап 5.4).

Зачем нужен именно браузер
-------------------------
Существующие ``tests/test_all_web_buttons.py`` проверяют HTTP-ответы
серверных обработчиков. Они принципиально не ловят главный класс
регрессий панели: «кнопка есть в HTML, но в браузере ничего не
происходит». Причины — реальные и повторяющиеся:

* CSP блокирует inline-скрипт, если nonce не совпал (регрессия 2.1);
* ``store.js`` не загрузился — и ``preventDefault`` не вызывается;
* обработчик не навешен из-за ошибки в JS (тихий ``Uncaught``);
* модалка открывается, но её не видно из-за CSS;
* ``fetch`` уходит без CSRF-токена и получает 403.

Всё это видно только в настоящем движке браузера. Тесты поднимают
uvicorn на свободном порту, подключают Chromium через Playwright и
кликают по кнопкам по-настоящему.

Архитектура
-----------
Приложение поднимается в фоне на том же процессе pytest, что позволяет
использовать общие monkeypatch-фикстуры ``conftest.py``. Отдельный
процесс не нужен: состояние БД и настройки и так подменены глобально.

CSP не отключается — это ключевое условие задачи. Наоборот, тесты
дополнительно проверяют, что в консоли браузера нет CSP-нарушений,
иначе «упавший» скрипт можно было бы не заметить.

Пропуск тестов
--------------
Если Playwright или браузер Chromium не установлены, тесты пропускаются
(skipif), а не падают: в CI образ без браузера — норма. Установка::

    pip install playwright
    python -m playwright install --with-deps chromium
"""

import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import closing
from typing import Any

import pytest

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    PLAYWRIGHT_AVAILABLE = False

requires_playwright = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright не установлен: pip install playwright && playwright install chromium",
)

# Таймауты подобраны так, чтобы тесты были стабильны на CI без потери
# смысла: 10 с на загрузку страницы, 5 с на клик/навигацию.
NAV_TIMEOUT_MS = 10_000
ACTION_TIMEOUT_MS = 5_000
SERVER_BOOT_TIMEOUT_S = 30


def _free_port() -> int:
    """Найти свободный TCP-порт на localhost.

    Порт 0 просит ядро выбрать свободный, после закрытия сокета порт
    освобождается. Гонка маловероятна и безопасна: при ней Uvicorn
    упадёт с EADDRINUSE, и тест будет перезапущен.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _extract_csrf(html: str) -> str:
    """Достать CSRF-токен из HTML формы входа."""
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html)
    if not match:
        raise AssertionError("На странице входа нет CSRF-токена")
    return match.group(1)


@pytest.fixture
def live_server(db_session_maker) -> Iterator[str]:
    """Поднять FastAPI-приложение на свободном порту в фоновом потоке.

    Сервер работает в том же процессе, что и pytest, — это позволяет
    переиспользовать monkeypatch-подмены ``conftest.py`` (async_session_maker
    во всех модулях), без которых приложение обращалось бы к боевой БД.

    ``lifespan`` отключён намеренно: фоновые задачи (outbox-воркер,
    очистка rate-limit) в тестах не нужны, а их запуск добавил бы
    недетерминированные обращения к БД.
    """
    import uvicorn

    from web.main import app

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + SERVER_BOOT_TIMEOUT_S
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            thread.join(timeout=5)
            raise AssertionError(
                f"Uvicorn не поднялся за {SERVER_BOOT_TIMEOUT_S} с на порту {port}"
            )
        if not thread.is_alive():
            raise AssertionError("Поток Uvicorn завершился до старта сервера")
        time.sleep(0.05)

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def _seed_admin(session_maker, username: str, password: str) -> int:
    """Создать суперадмина панели и вернуть его id."""
    from core.models import WebRole, WebUser
    from web.security.passwords import hash_password

    async with session_maker() as session:
        web_user = WebUser(
            username=username,
            password_hash=hash_password(password),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()
        await session.refresh(web_user)
        return int(web_user.id)


async def _seed_ticket(session_maker, department_id: int, topic: str) -> int:
    """Создать заявку для проверки кнопок «Открыть» и «Отправить ответ»."""
    from core.models import Ticket, TicketStatus

    async with session_maker() as session:
        ticket = Ticket(
            topic=topic,
            description="Описание тестовой заявки",
            status=TicketStatus.NEW,
            department_id=department_id,
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        return int(ticket.id)


async def _seed_department(session_maker, name: str) -> int:
    """Создать отдел и вернуть его id."""
    from core.models import Department

    async with session_maker() as session:
        dept = Department(name=name)
        session.add(dept)
        await session.commit()
        await session.refresh(dept)
        return int(dept.id)


class _ConsoleWatcher:
    """Собирает ошибки консоли и pageerror со страницы браузера.

    Нужен, чтобы тест ловил «тихо сломанный» JavaScript: если CSP
    заблокировал скрипт, браузер пишет в консоль, но HTTP-ответы и
    разметка остаются в порядке — обычные тесты этого не заметят.
    """

    def __init__(self, page) -> None:
        self.messages: list[str] = []
        self.page_errors: list[str] = []
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)

    def _on_console(self, message) -> None:
        # type=error ловит в том числе "Refused to execute inline script
        # because it violates the following Content Security Policy directive".
        if message.type == "error":
            if "net::ERR_" in message.text:
                return
            self.messages.append(f"[{message.type}] {message.text}")

    def _on_page_error(self, error) -> None:
        self.page_errors.append(str(error))

    def assert_no_script_errors(self) -> None:
        """Проверить, что на странице нет ошибок и нарушений CSP."""
        problems = self.page_errors + self.messages
        assert not problems, (
            "В консоли браузера есть ошибки (возможно, нарушен CSP):\n" + "\n".join(problems)
        )


async def _login_via_ui(page, base_url: str, username: str, password: str) -> None:
    """Войти в панель через реальную HTML-форму.

    Вход выполняется браузером, а не HTTP-клиентом: это проверяет,
    что CSRF-токен из формы действительно принимается сервером и что
    редирект после входа приводит на авторизованную страницу.
    """
    await page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click('button[type="submit"]')

    # После успешного входа — редирект на дашборд; остаёмся на /auth/login
    # только при ошибке (тогда показывается flash_error).
    await page.wait_for_load_state("networkidle")
    assert "/auth/login" not in page.url, (
        f"После входа остались на странице входа: {page.url}. "
        "Проверьте учётные данные или состояние сессии."
    )


@pytest.fixture
async def browser() -> AsyncIterator[Any]:
    """Запустить Chromium и закрыть его после теста."""
    async with async_playwright() as playwright:
        chromium = await playwright.chromium.launch()
        try:
            yield chromium
        finally:
            await chromium.close()


@pytest.fixture
async def logged_in_page(browser, live_server, db_session_maker):
    """Открыть авторизованную страницу в браузере.

    Создаёт суперадмина, выполняет вход через форму и отдаёт готовую
    вкладку вместе с наблюдателем за консолью.
    """
    from sqlalchemy import delete, select

    from core.config import settings
    from core.models import Department, WebUser

    monkey = pytest.MonkeyPatch()
    # 2FA выключена: иначе после входа потребуется OTP-код из VK,
    # который в тестах недоступен.
    monkey.setattr(settings, "TWO_FACTOR_ENABLED", False)
    # Bootstrap-креды из .env не должны влиять на сценарий.
    monkey.setattr(settings, "WEB_ADMIN_USERNAME", "")
    monkey.setattr(settings, "WEB_ADMIN_PASSWORD", "")

    username = "browser_admin"
    password = "BrowserPass123"

    async with db_session_maker() as session:
        await session.execute(delete(WebUser).where(WebUser.username == username))
        await session.commit()

    await _seed_admin(db_session_maker, username, password)

    async with db_session_maker() as session:
        existing = await session.scalar(
            select(Department.id).where(Department.name == "Отдел для UI-тестов")
        )
    department_id = (
        int(existing)
        if existing
        else await _seed_department(db_session_maker, "Отдел для UI-тестов")
    )
    await _seed_ticket(db_session_maker, department_id, "Проблема с заявкой для UI")

    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    watcher = _ConsoleWatcher(page)
    try:
        await _login_via_ui(page, live_server, username, password)
        yield page, watcher
    finally:
        await context.close()
        monkey.undo()


# =========================================================================
# Тесты кнопок (этап 5.4)
# =========================================================================


@requires_playwright
@pytest.mark.asyncio
async def test_button_open_ticket_opens_dialog(logged_in_page, live_server):
    """Кнопка «Открыть» в списке заявок открывает модалку с диалогом.

    Проверяется полный цикл: клик по кнопке → AJAX-загрузка /tickets/{id}
    → отрисовка чата в #modal-body → модалка получает класс active.
    Без работающего JS тест падает на ожидании #ticket-chat-box.
    """
    page, watcher = logged_in_page

    await page.goto(f"{live_server}/tickets/", wait_until="networkidle")

    open_button = page.locator("#tickets-table button[data-ticket-id]").first
    await open_button.wait_for(state="visible")

    # Модалка до клика не активна.
    modal = page.locator("#ticket-modal")
    assert not await modal.evaluate("el => el.classList.contains('active')")

    await open_button.click()

    # Диалог отрисован скриптом openTicket() из tickets.html.
    await page.wait_for_selector("#ticket-chat-box", state="visible", timeout=ACTION_TIMEOUT_MS)
    await page.wait_for_selector("#chat-reply-input", state="visible")

    assert await modal.evaluate(
        "el => el.classList.contains('active')"
    ), "После клика «Открыть» модалка должна получить класс active"

    title = await page.locator("#modal-title").text_content()
    assert title and "Заявка #" in title, f"Неожиданный заголовок модалки: {title!r}"

    # Кнопка ответа обязана появиться вместе с формой.
    assert await page.locator("#chat-reply-submit").is_visible()

    watcher.assert_no_script_errors()


@requires_playwright
@pytest.mark.asyncio
async def test_button_send_reply_adds_message_to_chat(logged_in_page, live_server):
    """Кнопка «Отправить ответ» доходит до сервера и добавляет сообщение.

    Это самый ценный тест файла: он проверяет сразу CSRF-заголовок,
    JSON-тело, обработчик /tickets/{id}/reply и обновление DOM.
    """
    page, watcher = logged_in_page

    await page.goto(f"{live_server}/tickets/", wait_until="networkidle")
    await page.locator("#tickets-table button[data-ticket-id]").first.click()
    await page.wait_for_selector("#chat-reply-input", state="visible")

    reply_text = "Ответ оператора из браузерного теста"

    # Счётчик сообщений ДО отправки: после клика их должно стать +1.
    before = await page.locator("#ticket-chat-box .chat-bubble").count()

    await page.fill("#chat-reply-input", reply_text)
    await page.click("#chat-reply-submit")

    # Пузырь нового сообщения появляется без перезагрузки страницы.
    await page.wait_for_function(
        "expected => document.querySelectorAll('#ticket-chat-box .chat-bubble')"
        ".length > expected",
        arg=before,
        timeout=ACTION_TIMEOUT_MS,
    )

    after = await page.locator("#ticket-chat-box .chat-bubble").count()
    assert after == before + 1, f"Ожидалось {before + 1} сообщений, получено {after}"

    # Текст ответа должен быть виден в чате.
    chat_text = await page.locator("#ticket-chat-box").text_content()
    assert reply_text in (chat_text or ""), "Текст ответа не появился в чате"

    # Поле ввода очищается после успешной отправки.
    assert (
        await page.input_value("#chat-reply-input") == ""
    ), "Поле ввода должно очищаться после отправки ответа"

    watcher.assert_no_script_errors()


@requires_playwright
@pytest.mark.asyncio
async def test_button_send_reply_validates_empty_input(logged_in_page, live_server):
    """Пустой ответ не отправляется: кнопка показывает ошибку валидации.

    Защищает от создания пустых сообщений в БД и подтверждает, что
    обработчик sendTicketReply() вызывается и работает.
    """
    page, watcher = logged_in_page

    await page.goto(f"{live_server}/tickets/", wait_until="networkidle")
    await page.locator("#tickets-table button[data-ticket-id]").first.click()
    await page.wait_for_selector("#chat-reply-input", state="visible")

    before = await page.locator("#ticket-chat-box .chat-bubble").count()

    # Не заполняем textarea и сразу кликаем «Отправить ответ».
    await page.click("#chat-reply-submit")

    await page.wait_for_selector(".alert-flash-error", state="visible", timeout=ACTION_TIMEOUT_MS)
    error_text = await page.locator(".alert-flash-error").text_content()
    assert "Введите текст ответа" in (
        error_text or ""
    ), f"Неожиданный текст ошибки валидации: {error_text!r}"

    after = await page.locator("#ticket-chat-box .chat-bubble").count()
    assert after == before, "Пустой ответ не должен добавлять сообщения"

    watcher.assert_no_script_errors()


@requires_playwright
@pytest.mark.asyncio
async def test_button_create_department_creates_via_ajax(logged_in_page, live_server):
    """Кнопка «Создать отдел» открывает модалку и создаёт отдел через AJAX.

    Проверяется цепочка: клик по кнопке в шапке → модалка active →
    ввод названия → submit → success-уведомление, закрытие модалки и
    очистка поля. Именно здесь ломается панель, когда store.js не загрузился.
    """
    page, watcher = logged_in_page

    dept_name = "Отдел из Playwright"
    await page.goto(f"{live_server}/departments/", wait_until="networkidle")

    # Модалка закрыта до клика.
    modal = page.locator("#create-modal")
    assert not await modal.evaluate("el => el.classList.contains('active')")

    await page.click('button[data-open-modal="create-modal"]')
    await page.wait_for_selector(
        "#create-modal.active", state="visible", timeout=ACTION_TIMEOUT_MS
    )

    await page.fill("#dept-name", dept_name)
    await page.click('#create-modal button[type="submit"]')

    # Успешное создание показывается всплывающим уведомлением.
    await page.wait_for_selector(
        ".alert-flash-success", state="visible", timeout=ACTION_TIMEOUT_MS
    )
    success_text = await page.locator(".alert-flash-success").text_content()
    assert dept_name in (
        success_text or ""
    ), f"Уведомление должно содержать название отдела: {success_text!r}"

    # После успеха модалка закрывается, а поле очищается.
    await page.wait_for_selector(
        "#create-modal:not(.active)", state="hidden", timeout=ACTION_TIMEOUT_MS
    )
    assert (
        await page.input_value("#dept-name") == ""
    ), "Поле названия должно очищаться после создания отдела"

    watcher.assert_no_script_errors()


@requires_playwright
@pytest.mark.asyncio
async def test_button_create_department_rejects_empty_name(logged_in_page, live_server):
    """«Создать отдел» с пустым названием показывает ошибку, не обращаясь к БД.

    Клиентская валидация в app.js должна сработать раньше формы: если она
    сломана, отправка уйдёт на сервер и вернёт 400 с flash_error.
    """
    page, watcher = logged_in_page

    await page.goto(f"{live_server}/departments/", wait_until="networkidle")
    await page.click('button[data-open-modal="create-modal"]')
    await page.wait_for_selector("#create-modal.active", state="visible")

    # Пустое название блокируется HTML5 required, поэтому проверяем
    # клиентскую валидацию приложения на пробельном вводе.
    await page.fill("#dept-name", "   ")
    await page.click('#create-modal button[type="submit"]')

    await page.wait_for_selector(".alert-flash-error", state="visible", timeout=ACTION_TIMEOUT_MS)
    error_text = await page.locator(".alert-flash-error").text_content()
    assert "Укажите название отдела" in (
        error_text or ""
    ), f"Неожиданный текст ошибки: {error_text!r}"

    # Модалка остаётся открытой — пользователь должен исправить ввод.
    assert await page.locator("#create-modal").evaluate(
        "el => el.classList.contains('active')"
    ), "При ошибке валидации модалка не должна закрываться"

    watcher.assert_no_script_errors()


@requires_playwright
@pytest.mark.asyncio
async def test_ticket_page_loads_without_csp_violations(logged_in_page, live_server):
    """Страница заявок не имеет нарушений CSP и JS-ошибок.

    Отдельный тест-«canary»: если CSP снова заблокирует inline-скрипты
    (регрессия 2.1), все кнопочные тесты выше упадут по таймауту с
    невнятным сообщением. Здесь причина видна сразу.
    """
    page, watcher = logged_in_page

    response = await page.goto(f"{live_server}/tickets/", wait_until="networkidle")
    assert response is not None and response.status == 200

    csp = response.headers.get("content-security-policy", "")
    assert "script-src" in csp, f"В ответе нет заголовка CSP: {csp!r}"
    # CSP должен быть включён (nonce), а не ослаблен для тестов.
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src, (
        "CSP не должен разрешать unsafe-inline в script-src — иначе тест "
        "не проверяет реальную защиту"
    )

    # Скрипты страницы действительно загрузились и выполнились.
    assert await page.evaluate(
        "() => typeof window.getCsrfToken === 'function'"
    ), "Функции tickets.html не определены: inline-скрипт не выполнился (CSP?)"

    watcher.assert_no_script_errors()

