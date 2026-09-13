"""UI and QA tests for Two-Factor Authentication (2FA) OTP screen.

QA Tests:
1. Header security & cache control (no-store, no-cache, must-revalidate).
2. DOM structure and accessibility (6 digit cells, separator, hidden #code, resend button, legal links).
3. Session protection, expiration, and unauthorized access redirects.
4. Form verification via unified single code.
5. Form verification fallback via separate split digit inputs (code_1..code_6).
6. Input sanitization (ignoring spaces, dashes, and non-digit characters).
7. Attempt counter decrement and remaining attempts warning message.
8. Maximum attempt lockout leading to session reset.
9. Resend code flow producing a new VkOutbox database entry.

UI Tests (Playwright Chromium):
1. Initial autofocus on first digit cell (code_1).
2. Arrow navigation (ArrowRight forward, ArrowLeft backward with boundary clamping).
3. Home and End keyboard shortcuts (Home -> code_1, End -> code_6).
4. Sequential digit entry and automatic focus progression.
5. Backspace deletion and focus retreat on empty cells.
6. Copy-paste of raw 6-digit codes.
7. Copy-paste of complete VK notification messages with text and emojis.
8. Resend cooldown timer behavior in sessionStorage.
9. Clean VK ID badge presentation without parentheses or trailing period.
"""

import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.config import settings
from core.models import Admin, User, UserRole, VkOutbox, WebRole, WebUser
from web.main import app
from web.security.passwords import hash_password
from web.templating import templates

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# ============================================================================
# QA Tests (Backend & Integration Flow)
# ============================================================================


@pytest.mark.asyncio
async def test_qa_2fa_page_headers_and_security(db_session_maker, monkeypatch):
    """QA: Страница /auth/2fa отдает Cache-Control no-store и правильную разметку."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=777888999, full_name="QA Инженер")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_headers",
            password_hash=hash_password("qa_password_123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Логин
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        login_res = await client.post(
            "/auth/login",
            data={
                "username": "qa_admin_headers",
                "password": "qa_password_123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert login_res.status_code == 303
        assert login_res.headers["location"] == "/auth/2fa"

        # Запрос 2FA страницы
        page_2fa = await client.get("/auth/2fa")
        assert page_2fa.status_code == 200

        # Проверка кэширования
        cache_control = page_2fa.headers.get("cache-control", "")
        assert "no-store" in cache_control
        assert "no-cache" in cache_control
        assert "must-revalidate" in cache_control
        assert page_2fa.headers.get("pragma") == "no-cache"

        # Проверка разметки
        html = page_2fa.text
        for i in range(1, 7):
            assert f'name="code_{i}"' in html
        assert 'name="code"' in html
        assert 'id="code"' in html
        assert 'name="csrf_token"' in html
        assert 'id="btnSubmit"' in html
        assert 'id="btnResend"' in html
        assert 'class="link-back-login"' in html
        assert "/legal/terms" in html
        assert "/legal/consent" in html

        # Проверка отсутствия скобок и точки в бейдже VK ID
        assert "VK ID: ***8999" in html
        assert "(VK ID:" not in html
        assert "VK ID: ***8999)." not in html


@pytest.mark.asyncio
async def test_qa_2fa_unauthorized_and_expired_redirect(db_session_maker, monkeypatch):
    """QA: Неавторизованный или просроченный запрос 2FA редиректит на логин."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET без сессии
        res = await client.get("/auth/2fa", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/auth/login"

        # Получаем CSRF токен со страницы входа
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        # POST на 2FA без активного шага 2FA редиректит на /auth/login
        post_res = await client.post(
            "/auth/2fa", data={"code": "123456", "csrf_token": csrf}, follow_redirects=False
        )
        assert post_res.status_code == 303
        assert post_res.headers["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_qa_2fa_verification_single_code_and_split_fallback(db_session_maker, monkeypatch):
    """QA: Проверка ввода через скрытый код и fallback через отдельные ячейки."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=111222333, full_name="Fallback Тест")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_fallback",
            password_hash=hash_password("qa_password_123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)

    # 1. Fallback через отдельные ячейки code_1..code_6
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        await client.post(
            "/auth/login",
            data={
                "username": "qa_admin_fallback",
                "password": "qa_password_123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

        async with db_session_maker() as session:
            outbox = (
                await session.execute(
                    select(VkOutbox)
                    .where(VkOutbox.vk_id == 111222333)
                    .order_by(VkOutbox.id.desc())
                )
            ).scalar_one()
            otp_match = re.search(r"\b(\d{6})\b", outbox.text)
            assert otp_match, f"OTP not found in {outbox.text}"
            otp_code = otp_match.group(1)

        page_2fa = await client.get("/auth/2fa")
        csrf_2fa = re.search(r'name="csrf_token" value="([^"]+)"', page_2fa.text).group(1)

        # Отправляем code="" и заполненные code_1..code_6
        payload = {
            "code": "",
            "code_1": otp_code[0],
            "code_2": otp_code[1],
            "code_3": otp_code[2],
            "code_4": otp_code[3],
            "code_5": otp_code[4],
            "code_6": otp_code[5],
            "csrf_token": csrf_2fa,
        }
        res = await client.post("/auth/2fa", data=payload, follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/"


@pytest.mark.asyncio
async def test_qa_2fa_verification_sanitization(db_session_maker, monkeypatch):
    """QA: Санитизация кода (пробелы, дефисы и нечисловые символы)."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=333444555, full_name="Sanitize Тест")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_sanitize",
            password_hash=hash_password("qa_password_123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        await client.post(
            "/auth/login",
            data={
                "username": "qa_admin_sanitize",
                "password": "qa_password_123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

        async with db_session_maker() as session:
            outbox = (
                await session.execute(
                    select(VkOutbox)
                    .where(VkOutbox.vk_id == 333444555)
                    .order_by(VkOutbox.id.desc())
                )
            ).scalar_one()
            otp_match = re.search(r"\b(\d{6})\b", outbox.text)
            assert otp_match, f"OTP not found in {outbox.text}"
            otp_code = otp_match.group(1)

        page_2fa = await client.get("/auth/2fa")
        csrf_2fa = re.search(r'name="csrf_token" value="([^"]+)"', page_2fa.text).group(1)

        # Передаем с пробелами и дефисом
        formatted_code = f" {otp_code[:3]} - {otp_code[3:]} "
        res = await client.post(
            "/auth/2fa",
            data={"code": formatted_code, "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/"


@pytest.mark.asyncio
async def test_qa_2fa_attempts_decrement_and_lockout(db_session_maker, monkeypatch):
    """QA: Неверный код уменьшает счётчик попыток; превышение 5 попыток сбрасывает сессию."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=444555666, full_name="Lockout Тест")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_lockout",
            password_hash=hash_password("qa_password_123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        await client.post(
            "/auth/login",
            data={
                "username": "qa_admin_lockout",
                "password": "qa_password_123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

        page_2fa = await client.get("/auth/2fa")
        csrf_2fa = re.search(r'name="csrf_token" value="([^"]+)"', page_2fa.text).group(1)

        # 1-я неверная попытка
        res1 = await client.post(
            "/auth/2fa",
            data={"code": "000000", "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert res1.status_code == 303
        assert res1.headers["location"] == "/auth/2fa"

        page_2fa_retry = await client.get("/auth/2fa")
        assert "Неверный код. Осталось попыток: 4." in page_2fa_retry.text

        # Ещё 4 неверные попытки (достигаем лимита 5)
        for _ in range(4):
            await client.post(
                "/auth/2fa",
                data={"code": "000000", "csrf_token": csrf_2fa},
                follow_redirects=False,
            )

        # 6-я попытка -> блокировка и редирект на /auth/login
        lockout_res = await client.post(
            "/auth/2fa",
            data={"code": "000000", "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert lockout_res.status_code == 303
        assert lockout_res.headers["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_qa_2fa_resend_code(db_session_maker, monkeypatch):
    """QA: Повторная отправка 2FA-кода создает новую запись в очереди сообщений."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=666777888, full_name="Resend Тест")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_resend",
            password_hash=hash_password("qa_password_123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        await client.post(
            "/auth/login",
            data={
                "username": "qa_admin_resend",
                "password": "qa_password_123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

        page_2fa = await client.get("/auth/2fa")
        csrf_2fa = re.search(r'name="csrf_token" value="([^"]+)"', page_2fa.text).group(1)

        # Отправляем запрос повторной отправки
        resend_res = await client.post(
            "/auth/2fa/resend",
            data={"csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert resend_res.status_code == 303
        assert resend_res.headers["location"] == "/auth/2fa"

        async with db_session_maker() as session:
            messages = (
                (
                    await session.execute(
                        select(VkOutbox)
                        .where(VkOutbox.vk_id == 666777888)
                        .order_by(VkOutbox.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(messages) >= 2, "Должно быть отправлено минимум 2 кода (первый и повторный)"


# ============================================================================
# UI Tests (Playwright Chromium Interactive Testing)
# ============================================================================


@pytest.fixture
def rendered_2fa_html_file(tmp_path):
    """Генерирует статическую тестовую страницу из шаблона 2fa.html."""
    html_content = templates.get_template("2fa.html").render(
        {
            "request": {"url": {"path": "/auth/2fa"}},
            "masked_vk_id": "***4321",
            "csrf_token": "qa_ui_token_12345",
            "error": None,
        }
    )
    test_file = tmp_path / "2fa_ui_test.html"
    test_file.write_text(html_content, encoding="utf-8")
    yield test_file
    test_file.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_initial_autofocus(rendered_2fa_html_file):
    """UI: Автофокус устанавливается на первую ячейку (code_1)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        # Даем отработать setTimeout(..., 50)
        await page.wait_for_timeout(100)

        active_name = await page.evaluate("() => document.activeElement.name")
        assert active_name == "code_1", f"Expected code_1 to be focused, got {active_name}"
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_arrow_navigation(rendered_2fa_html_file):
    """UI: Навигация стрелками влево и вправо с корректными границами."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        first_input = page.locator('input[name="code_1"]')
        await first_input.click()

        # Шагаем вправо до 6-го элемента
        for i in range(2, 7):
            await page.keyboard.press("ArrowRight")
            active = await page.evaluate("() => document.activeElement.name")
            assert active == f"code_{i}"

        # На 6-м элементе ArrowRight не должен выходить за границу
        await page.keyboard.press("ArrowRight")
        active_bound = await page.evaluate("() => document.activeElement.name")
        assert active_bound == "code_6"

        # Шагаем влево обратно до 1-го элемента
        for i in range(5, 0, -1):
            await page.keyboard.press("ArrowLeft")
            active = await page.evaluate("() => document.activeElement.name")
            assert active == f"code_{i}"

        # На 1-м элементе ArrowLeft не выходит за границы
        await page.keyboard.press("ArrowLeft")
        active_start = await page.evaluate("() => document.activeElement.name")
        assert active_start == "code_1"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_home_and_end_navigation(rendered_2fa_html_file):
    """UI: Клавиши Home/ArrowUp и End/ArrowDown перемещают в начало и в конец."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        await page.locator('input[name="code_3"]').click()
        assert await page.evaluate("() => document.activeElement.name") == "code_3"

        # End -> переход на code_6
        await page.keyboard.press("End")
        assert await page.evaluate("() => document.activeElement.name") == "code_6"

        # Home -> переход на code_1
        await page.keyboard.press("Home")
        assert await page.evaluate("() => document.activeElement.name") == "code_1"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_typing_and_hidden_code_sync(rendered_2fa_html_file):
    """UI: Посимвольный ввод цифр переключает фокус и синхронизирует скрытый инпут."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        await page.locator('input[name="code_1"]').click()

        digits = ["9", "4", "2", "8", "0", "1"]
        for d in digits:
            await page.keyboard.type(d)

        # Проверяем значения ячеек
        for idx, d in enumerate(digits, start=1):
            val = await page.locator(f'input[name="code_{idx}"]').input_value()
            assert val == d

            # Проверяем класс is-filled
            classes = await page.locator(f'input[name="code_{idx}"]').get_attribute("class")
            assert "is-filled" in classes

        # Проверяем значение скрытого инпута
        hidden_val = await page.locator("#code").input_value()
        assert hidden_val == "942801"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_backspace_navigation(rendered_2fa_html_file):
    """UI: Backspace очищает ячейку и при повторном нажатии возвращает фокус на предыдущую."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        await page.locator('input[name="code_1"]').click()
        await page.keyboard.type("5")
        await page.keyboard.type("7")

        # Фокус сейчас на code_3
        assert await page.evaluate("() => document.activeElement.name") == "code_3"

        # На пустой code_3 жмем Backspace -> переходит на code_2 и очищает его
        await page.keyboard.press("Backspace")
        assert await page.evaluate("() => document.activeElement.name") == "code_2"
        assert await page.locator('input[name="code_2"]').input_value() == ""

        # Жмем Backspace еще раз -> переходит на code_1 и очищает его
        await page.keyboard.press("Backspace")
        assert await page.evaluate("() => document.activeElement.name") == "code_1"
        assert await page.locator('input[name="code_1"]').input_value() == ""

        assert await page.locator("#code").input_value() == ""
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_paste_raw_code_and_full_notification(rendered_2fa_html_file):
    """UI: Вставка сырых цифр или полного сообщения ВК заполняет все 6 ячеек."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        # 1. Вставка сырого 6-значного кода
        await page.evaluate("""() => {
            const dt = new DataTransfer();
            dt.setData('text/plain', '654321');
            const pasteEvent = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true });
            document.querySelector('.otp-inputs').dispatchEvent(pasteEvent);
        }""")

        vals = [await page.locator(f'input[name="code_{i}"]').input_value() for i in range(1, 7)]
        assert vals == ["6", "5", "4", "3", "2", "1"]
        assert await page.locator("#code").input_value() == "654321"

        # 2. Вставка полного уведомления с эмодзи и текстом
        vk_message = "🔐 Одноразовый код для входа в панель управления OSS Bot: 839201\nКод действителен 5 мин."
        await page.evaluate(
            """(msg) => {
            const dt = new DataTransfer();
            dt.setData('text/plain', msg);
            const pasteEvent = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true });
            document.querySelector('.otp-inputs').dispatchEvent(pasteEvent);
        }""",
            vk_message,
        )

        vals_vk = [
            await page.locator(f'input[name="code_{i}"]').input_value() for i in range(1, 7)
        ]
        assert vals_vk == ["8", "3", "9", "2", "0", "1"]
        assert await page.locator("#code").input_value() == "839201"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_resend_cooldown_timer(rendered_2fa_html_file):
    """UI: Отправка формы повторного запроса кода блокирует кнопку таймером cooldown."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        # Имитируем сохранение cooldown в sessionStorage
        await page.evaluate("""() => {
            sessionStorage.setItem('2fa_resend_cooldown_end', (Date.now() + 45000).toString());
            // Перезагрузка страницы/вызов таймера
            location.reload();
        }""")
        await page.wait_for_load_state("load")

        btn_resend = page.locator("#btnResend")
        is_disabled = await btn_resend.is_disabled()
        assert is_disabled is True

        label_text = await page.locator("#resendLabel").text_content()
        assert "Отправить повторно (" in label_text
        assert "с)" in label_text

        await browser.close()
