"""Регрессия этапа 2.1: nonce в CSP-заголовке обязан совпадать с nonce inline-скриптов.

Если nonce не прокидывается в контекст шаблона (атрибут nonce пуст или не тот),
браузер блокирует все inline-скрипты по CSP — «умирают» кнопки и модалки.
"""

import re


def test_csp_nonce_matches_inline_scripts(web_client):
    """Страница логина: nonce из заголовка Content-Security-Policy есть в <script nonce=...>."""
    resp = web_client.get("/auth/login")
    assert resp.status_code == 200, resp.status_code
    csp = resp.headers.get("content-security-policy", "")
    match = re.search(r"nonce-([A-Za-z0-9_\-]+)", csp)
    assert match, f"В CSP-заголовке нет nonce: {csp!r}"
    nonce = match.group(1)
    assert f'nonce="{nonce}"' in resp.text, (
        "Inline-скрипты страницы не содержат nonce из CSP-заголовка "
        "— браузер заблокирует их (csp_nonce не прокидывается в контекст)"
    )
    style_src = next(directive for directive in csp.split(";") if directive.strip().startswith("style-src "))
    style_match = re.search(r"'nonce-([A-Za-z0-9_\-]+)'", style_src)
    assert style_match, f"В style-src нет nonce: {style_src!r}"
    assert f'<style nonce="{style_match.group(1)}">' in resp.text
    assert "'unsafe-inline'" not in style_src
    # Пустой атрибут nonce="" означает, что middleware не смог передать nonce
    assert 'nonce=""' not in resp.text
