"""Headless Chromium via the official Playwright package (no stealth patches)."""
from __future__ import annotations

import ipaddress
from html import escape as html_escape
import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

from .policy import USER_AGENT


def _bypassed(host: str) -> bool:
    """True if ``host`` should be reached directly (loopback/private or listed in NO_PROXY).

    Chromium ignores loopback bypass rules once an explicit proxy is configured,
    so the decision is made here, per target, before launching the browser."""
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return True
    except ValueError:
        if host == "localhost":
            return True
    for entry in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(","):
        entry = entry.strip().lstrip("*")
        if not entry:
            continue
        if "/" in entry:
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        elif host == entry.lstrip(".") or (entry.startswith(".") and host.endswith(entry)):
            return True
    return False


def _proxy(target_url: str | None) -> dict | None:
    server = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not server or (target_url and _bypassed(urlsplit(target_url).hostname or "")):
        return None
    return {"server": server}


@contextmanager
def browser_context(user_agent: str = USER_AGENT, headless: bool = True, target_url: str | None = None) -> Iterator:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("playwright is not installed: pip install playwright") from exc
    with sync_playwright() as p:
        kwargs: dict = {"headless": headless}
        exe = os.environ.get("AVR_CHROMIUM_PATH")
        if exe:
            kwargs["executable_path"] = exe
        proxy = _proxy(target_url)
        if proxy:
            kwargs["proxy"] = proxy
        browser = p.chromium.launch(**kwargs)
        try:
            ctx = browser.new_context(user_agent=user_agent, ignore_https_errors=False)
            yield ctx
            ctx.close()
        finally:
            browser.close()


def rendered_html(page) -> str:
    """One well-formed HTML document: the page body plus each iframe's body in a
    ``<section data-frame=...>`` (games usually render inside an iframe).
    Concatenating whole documents would make HTML parsers stop at the first one."""
    title = html_escape(page.title() or "")
    parts = [f"<html><head><title>{title}</title></head><body>"]
    for i, frame in enumerate(page.frames):
        try:
            body = frame.evaluate("() => document.body ? document.body.outerHTML : ''")
        except Exception:  # detached frame / navigation race
            continue
        if i == 0:
            parts.append(body)
        else:
            parts.append(f'<section data-frame="{html_escape(frame.url)}">{body}</section>')
    parts.append("</body></html>")
    return "\n".join(parts)
