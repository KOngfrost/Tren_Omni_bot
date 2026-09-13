"""UI and QA tests for Two-Factor Authentication (2FA) OTP screen with single unified input.

QA Tests:
1. Header security & cache control (no-store, no-cache, must-revalidate).
2. DOM structure and accessibility (single unified #code input, resend button, legal links).
3. Session protection, expiration, and unauthorized access redirects.
4. Form verification via unified single code.
5. Input sanitization (ignoring spaces, dashes, and non-digit characters).
6. Attempt counter decrement and remaining attempts warning message.
7. Maximum attempt lockout leading to session reset.
8. Resend code flow producing a new VkOutbox database entry.

UI Tests (Playwright Chromium):
1. Initial autofocus on single input (#code).
2. Sequential digit entry and character filtering.
3. Copy-paste of raw 6-digit codes.
4. Copy-paste of complete VK notification messages with text and emojis.
5. Resend cooldown timer behavior in sessionStorage.
6. Clean VK ID badge presentation without parentheses or trailing period.
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


@pytest.mark.asyncio
async def test_qa_2fa_page_headers_and_security(db_session_maker, monkeypatch):
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

        page_2fa = await client.get("/auth/2fa")
        assert page_2fa.status_code == 200

        cache_control = page_2fa.headers.get("cache-control", "")
        assert "no-store" in cache_control
        assert "no-cache" in cache_control
        assert "must-revalidate" in cache_control
        assert page_2fa.headers.get("pragma") == "no-cache"

        html = page_2fa.text
        assert 'name="code"' in html
        assert 'id="code"' in html
        assert 'name="csrf_token"' in html
        assert 'id="btnSubmit"' in html
        assert 'id="btnResend"' in html
        assert 'class="link-back-login"' in html
        assert "/legal/terms" in html
        assert "/legal/consent" in html
        assert "/legal/privacy" in html
        assert "Политикой обработки персональных данных" in html

        assert "VK ID: ***8999" in html
        assert "(VK ID:" not in html
        assert "VK ID: ***8999)." not in html


@pytest.mark.asyncio
async def test_qa_2fa_unauthorized_and_expired_redirect(db_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/auth/2fa", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/auth/login"

        login_page = await client.get("/auth/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        post_res = await client.post(
            "/auth/2fa", data={"code": "123456", "csrf_token": csrf}, follow_redirects=False
        )
        assert post_res.status_code == 303
        assert post_res.headers["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_qa_2fa_verification_single_code(db_session_maker, monkeypatch):
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "secret_key_for_2fa_qa_testing_12345")

    async with db_session_maker() as session:
        vk_user = User(vk_id=111222333, full_name="Single Code Тест")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="qa_admin_single",
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
                "username": "qa_admin_single",
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

        res = await client.post(
            "/auth/2fa",
            data={"code": otp_code, "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/"


@pytest.mark.asyncio
async def test_qa_2fa_verification_sanitization(db_session_maker, monkeypatch):
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

        res1 = await client.post(
            "/auth/2fa",
            data={"code": "000000", "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert res1.status_code == 303
        assert res1.headers["location"] == "/auth/2fa"

        page_2fa_retry = await client.get("/auth/2fa")
        assert "Неверный код. Осталось попыток: 4." in page_2fa_retry.text

        for _ in range(4):
            await client.post(
                "/auth/2fa",
                data={"code": "000000", "csrf_token": csrf_2fa},
                follow_redirects=False,
            )

        lockout_res = await client.post(
            "/auth/2fa",
            data={"code": "000000", "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert lockout_res.status_code == 303
        assert lockout_res.headers["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_qa_2fa_resend_code(db_session_maker, monkeypatch):
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
            assert len(messages) >= 2


@pytest.fixture
def rendered_2fa_html_file(tmp_path):
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
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")
        await page.wait_for_timeout(100)

        active_id = await page.evaluate("() => document.activeElement.id")
        assert active_id == "code"
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_typing_and_formatting(rendered_2fa_html_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        input_el = page.locator("#code")
        await input_el.click()

        await page.keyboard.type("1a2b3c4d5e6f7g")
        val = await input_el.input_value()
        assert val == "123456"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_paste_raw_code_and_full_notification(rendered_2fa_html_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        input_el = page.locator("#code")
        await input_el.click()

        await page.evaluate("""() => {
            const el = document.getElementById('code');
            const dt = new DataTransfer();
            dt.setData('text/plain', '654321');
            const pasteEvent = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
            el.dispatchEvent(pasteEvent);
        }""")
        assert await input_el.input_value() == "654321"

        vk_message = "🔐 Одноразовый код для входа в панель управления OSS Bot: 839201\nКод действителен 5 мин."
        await page.evaluate(
            """(msg) => {
            const el = document.getElementById('code');
            const dt = new DataTransfer();
            dt.setData('text/plain', msg);
            const pasteEvent = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
            el.dispatchEvent(pasteEvent);
        }""",
            vk_message,
        )
        assert await input_el.input_value() == "839201"

        await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright is required for browser UI tests")
async def test_ui_2fa_resend_cooldown_timer(rendered_2fa_html_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file:///{rendered_2fa_html_file.resolve().as_posix()}")

        await page.evaluate("""() => {
            sessionStorage.setItem('2fa_resend_cooldown_end', (Date.now() + 45000).toString());
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
