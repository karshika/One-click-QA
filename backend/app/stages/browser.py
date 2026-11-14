"""Shared Playwright helpers: browser launch and the network guard used by every stage."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Browser, BrowserContext, Route, async_playwright

from app.security.ssrf import SSRFError, check_url, is_allowed_host

log = logging.getLogger(__name__)

USER_AGENT_SUFFIX = " OneClickQA/0.1 (+automated QA; only for sites you own)"


@dataclass
class GuardConfig:
    base_url: str
    allow_local: bool
    extra_domains: list[str] = field(default_factory=list)
    # if True, only the target host (+extra domains) may be contacted at all (generated tests).
    strict_hosts: bool = False


@dataclass
class GuardStats:
    blocked: list[str] = field(default_factory=list)


async def install_guard(context: BrowserContext, cfg: GuardConfig, stats: GuardStats | None = None) -> None:
    """Abort requests that would violate SSRF rules or leave the allowed hosts.

    * navigations (documents) must stay on the target origin / allowlisted domains
    * subresources must pass the SSRF check (and the host allowlist when strict_hosts)
    """
    checked: dict[str, bool] = {}

    async def handler(route: Route) -> None:
        req = route.request
        url = req.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        same = is_allowed_host(url, cfg.base_url, cfg.extra_domains)
        if ((req.is_navigation_request() and req.frame.parent_frame is None) or cfg.strict_hosts) and not same:
            if stats is not None:
                stats.blocked.append(url)
            await route.abort("blockedbyclient")
            return
        host_key = url.split("/")[2] if "//" in url else url
        ok = checked.get(host_key)
        if ok is None:
            try:
                await asyncio.to_thread(check_url, url, allow_local=cfg.allow_local)
                ok = True
            except SSRFError:
                ok = False
            checked[host_key] = ok
        if not ok:
            if stats is not None:
                stats.blocked.append(url)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    await context.route("**/*", handler)


@asynccontextmanager
async def launch_browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            yield browser
        finally:
            await browser.close()


async def new_context(browser: Browser, guard: GuardConfig, *, storage_state: str | None = None,
                      stats: GuardStats | None = None, **kwargs: Any) -> BrowserContext:
    ua = (await _default_ua(browser)) + USER_AGENT_SUFFIX
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=ua,
                                    storage_state=storage_state, ignore_https_errors=False, **kwargs)
    await install_guard(ctx, guard, stats)
    return ctx


_UA: str | None = None


async def _default_ua(browser: Browser) -> str:
    global _UA
    if _UA is None:
        page = await browser.new_page()
        _UA = str(await page.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome")
        await page.close()
    return _UA
