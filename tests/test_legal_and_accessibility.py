"""Тесты соответствия законодательству РФ (152-ФЗ) и стандартам доступности/SEO.

Проверяет:
1. Роуты /legal/privacy, /legal/consent, /legal/terms (152-ФЗ).
2. Фавикон /favicon.ico и /static/favicon.svg.
3. Skip-link и landmark #main-content.
4. Meta title, meta description, rel="canonical".
5. Баннер согласия на Cookie.
6. Уведомление о согласии на обработку ПД на странице логина.
7. Оптимизацию: Cache-Control и сжатие GZip.
"""


def test_legal_routes_accessible(web_client):
    """Страницы 152-ФЗ открываются без авторизации и содержат реквизиты закона."""
    # 1. Политика конфиденциальности
    res_priv = web_client.get("/legal/privacy")
    assert res_priv.status_code == 200
    assert "Политика конфиденциальности" in res_priv.text
    assert "152-ФЗ" in res_priv.text
    assert 'rel="canonical"' in res_priv.text
    assert "/legal/privacy" in res_priv.text

    # 2. Согласие на обработку ПД
    res_cons = web_client.get("/legal/consent")
    assert res_cons.status_code == 200
    assert "Согласие на обработку персональных данных" in res_cons.text
    assert "152-ФЗ" in res_cons.text
    assert 'rel="canonical"' in res_cons.text
    assert "/legal/consent" in res_cons.text

    # 3. Пользовательское соглашение
    res_terms = web_client.get("/legal/terms")
    assert res_terms.status_code == 200
    assert "Условия пользования" in res_terms.text
    assert "Пользовательское соглашение" in res_terms.text
    assert 'rel="canonical"' in res_terms.text
    assert "/legal/terms" in res_terms.text


def test_favicon_endpoints(web_client):
    """Проверка наличия favicon.ico и SVG фавикона."""
    res_ico = web_client.get("/favicon.ico")
    assert res_ico.status_code == 200
    assert "image/x-icon" in res_ico.headers.get("content-type", "")
    assert len(res_ico.content) > 0

    res_svg = web_client.get("/static/favicon.svg")
    assert res_svg.status_code == 200
    assert "<svg" in res_svg.text


def test_accessibility_landmarks_and_skip_link(web_client):
    """Проверка доступности: skip-link, landmark #main-content и фокус."""
    res = web_client.get("/auth/login")
    assert res.status_code == 200
    # Skip link
    assert 'class="skip-link"' in res.text
    assert 'href="#main-content"' in res.text
    # Main landmark
    assert 'id="main-content"' in res.text


def test_meta_tags_and_canonical(web_client):
    """Проверка meta title, description и canonical url."""
    res = web_client.get("/auth/login")
    assert res.status_code == 200
    assert "<title>" in res.text
    assert '<meta name="description"' in res.text
    assert '<link rel="canonical"' in res.text


def test_cookie_banner_present(web_client):
    """Проверка наличия баннера cookie и ссылок на правовые документы."""
    res = web_client.get("/auth/login")
    assert res.status_code == 200
    assert 'id="cookie-consent-banner"' in res.text
    assert 'id="cookie-consent-accept-btn"' in res.text
    assert "/legal/privacy" in res.text


def test_login_legal_notice(web_client):
    """Форма входа должна содержать уведомление о согласии с 152-ФЗ."""
    res = web_client.get("/auth/login")
    assert res.status_code == 200
    assert "персональных данных" in res.text
    assert "/legal/consent" in res.text
    assert "/legal/terms" in res.text


def test_static_cache_control_and_gzip(web_client):
    """Проверка кэширования статики и поддержки GZip."""
    # Cache-Control для статики
    res_css = web_client.get("/static/style.css")
    assert res_css.status_code == 200
    cache_header = res_css.headers.get("cache-control", "")
    assert "public" in cache_header and "max-age=" in cache_header

    # GZip сжатие
    res_gzip = web_client.get("/legal/privacy", headers={"Accept-Encoding": "gzip"})
    assert res_gzip.status_code == 200
    assert res_gzip.headers.get("content-encoding") == "gzip"
