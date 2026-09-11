"""Тест проверки битых ссылок и целостности статических ресурсов (требование 12).

Краулер обходит публичные и авторизованные страницы, извлекает все внутренние
ссылки (<a href>), изображения (<img src>), стили (<link href>) и скрипты (<script src>),
проверяя, что среди них нет 404, 500 или поврежденных ресурсов.
"""

import re
from urllib.parse import urlparse


def _extract_internal_urls(html: str) -> set[str]:
    """Извлекает внутренние URL из HTML контента."""
    urls = set()
    # <a> href
    for href in re.findall(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\'#]+)["\']', html, re.IGNORECASE):
        urls.add(href)
    # <img src>
    for src in re.findall(r'<img\s+(?:[^>]*?\s+)?src=["\']([^"\'#]+)["\']', html, re.IGNORECASE):
        urls.add(src)
    # <link href>
    for href in re.findall(
        r'<link\s+(?:[^>]*?\s+)?href=["\']([^"\'#]+)["\']', html, re.IGNORECASE
    ):
        urls.add(href)
    # <script src>
    for src in re.findall(
        r'<script\s+(?:[^>]*?\s+)?src=["\']([^"\'#]+)["\']', html, re.IGNORECASE
    ):
        urls.add(src)

    # Фильтруем внешние ссылки (http://, https://, mailto:, javascript:)
    internal_urls = set()
    for u in urls:
        u_clean = u.strip()
        if not u_clean:
            continue
        parsed = urlparse(u_clean)
        if parsed.scheme in ("http", "https", "mailto", "tel", "javascript"):
            continue
        if u_clean.startswith("/"):
            internal_urls.add(u_clean)
    return internal_urls


def _login(client) -> None:
    res = client.get("/auth/login")
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', res.text)
    if not match:
        match = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', res.text)
    csrf_token = match.group(1) if match else ""
    client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": csrf_token},
        follow_redirects=True,
    )


def test_public_pages_broken_links(web_client):
    """Проверка ссылок на публичных страницах (логин, правовые документы)."""
    seed_pages = ["/auth/login", "/legal/privacy", "/legal/consent", "/legal/terms"]
    discovered = set(seed_pages)

    for page in seed_pages:
        res = web_client.get(page)
        assert res.status_code == 200, f"Page {page} returned status {res.status_code}"
        discovered.update(_extract_internal_urls(res.text))

    for url in discovered:
        # Игнорируем экшены изменения данных
        if url.startswith(("/auth/logout", "/api/")):
            continue
        res = web_client.get(url, follow_redirects=True)
        assert res.status_code not in (
            404,
            500,
            502,
            503,
        ), f"Broken link detected at {url}: status {res.status_code}"


def test_authenticated_pages_broken_links(web_client):
    """Краулинг всех страниц админ-панели после авторизации."""
    _login(web_client)

    admin_pages = [
        "/",
        "/tickets/",
        "/departments/",
        "/admin/admins/",
        "/faq/",
        "/events/",
        "/knowledge/",
        "/logs/",
    ]
    discovered = set(admin_pages)

    for page in admin_pages:
        res = web_client.get(page, follow_redirects=True)
        assert res.status_code == 200, f"Admin page {page} returned status {res.status_code}"
        discovered.update(_extract_internal_urls(res.text))

    for url in discovered:
        # Игнорируем маршруты мутации/выхода
        if url.startswith(("/auth/logout", "/api/")):
            continue
        res = web_client.get(url, follow_redirects=True)
        assert res.status_code not in (
            404,
            500,
            502,
            503,
        ), f"Broken link in admin panel at {url}: status {res.status_code}"
