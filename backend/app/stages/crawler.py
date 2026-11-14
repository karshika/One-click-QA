"""Stage 1 – Crawl.

Same-origin BFS up to max_pages / max_depth with a polite delay. Optionally logs in first.
Never follows links or clicks anything that looks destructive (logout, delete, pay, …)
unless explicitly allowed. Never submits forms other than the login form.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from playwright.async_api import BrowserContext, ConsoleMessage, Page, Request, Response
from playwright.async_api import Error as PWError

from app.stages.browser import GuardConfig, GuardStats, launch_browser, new_context

log = logging.getLogger(__name__)

DESTRUCTIVE = re.compile(
    r"(?i)\b(log ?out|sign ?out|delete|remove|destroy|unsubscribe|deactivate|cancel[- ]?(account|subscription)|"
    r"pay|purchase|checkout|place[- ]order|buy now|reset)\b"
)
SKIP_EXT = re.compile(r"(?i)\.(pdf|zip|gz|tar|rar|7z|exe|dmg|png|jpe?g|gif|webp|svg|ico|mp4|mp3|wav|avi|mov|"
                      r"woff2?|ttf|css|js|xml|json|csv|xlsx?|docx?|pptx?)$")
LOGIN_LINK = re.compile(r"(?i)\b(log ?in|sign ?in)\b")
COMMON_LOGIN_PATHS = ["/login", "/signin", "/sign-in", "/account/login", "/users/sign_in", "/auth/login"]

# JS that summarizes the DOM into a compact, LLM-friendly structure (no raw HTML).
DOM_SUMMARY_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const labelFor = el => {
    if (el.labels && el.labels.length) return txt(el.labels[0]);
    const al = el.getAttribute('aria-label'); if (al) return al;
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const n = document.getElementById(lb); if (n) return txt(n); }
    return '';
  };
  const field = el => ({ tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || (el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : 'text'),
    name: el.getAttribute('name') || '', id: el.id || '', label: labelFor(el), placeholder: el.getAttribute('placeholder') || '',
    required: el.required || false, testid: el.getAttribute('data-testid') || '' });
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')]
    .filter(vis).slice(0, 40).map(b => ({ text: txt(b) || b.value || '', aria_label: b.getAttribute('aria-label') || '',
      type: b.getAttribute('type') || '', id: b.id || '', testid: b.getAttribute('data-testid') || '' }));
  const links = [...document.querySelectorAll('a[href]')].slice(0, 200).map(a => ({ href: a.href, text: txt(a) }));
  const forms = [...document.querySelectorAll('form')].slice(0, 10).map(f => ({
    action: f.getAttribute('action') || '', method: (f.getAttribute('method') || 'get').toLowerCase(), id: f.id || '',
    fields: [...f.querySelectorAll('input, textarea, select')].filter(i => i.type !== 'hidden').slice(0, 20).map(field),
    submit: (() => { const s = f.querySelector('button[type=submit], button:not([type]), input[type=submit]');
      return s ? (txt(s) || s.value || '') : ''; })() }));
  const loose = [...document.querySelectorAll('input, textarea, select')].filter(i => !i.form && i.type !== 'hidden' && vis(i)).slice(0, 20).map(field);
  const headings = [...document.querySelectorAll('h1, h2, h3')].slice(0, 20).map(h => h.tagName.toLowerCase() + ': ' + txt(h));
  const landmarks = [...document.querySelectorAll('header, nav, main, footer, aside, [role=navigation], [role=main], [role=search]')]
    .slice(0, 12).map(l => l.tagName.toLowerCase() + (l.getAttribute('aria-label') ? '[' + l.getAttribute('aria-label') + ']' : ''));
  const testids = [...document.querySelectorAll('[data-testid]')].slice(0, 30).map(e => e.getAttribute('data-testid'));
  const alerts = [...document.querySelectorAll('[role=alert], [role=status]')].map(txt).filter(Boolean).slice(0, 5);
  const main = document.querySelector('main') || document.body;
  return { headings, landmarks, buttons, links, forms, loose_fields: loose, testids, alerts,
    text: (main ? txt({ innerText: main.innerText.slice(0, 1200) }) : ''),
    text_long: (main ? main.innerText.replace(/\s+/g, ' ').trim().slice(0, 600) : ''),
    images: document.images.length, lang: document.documentElement.lang || '' };
}
"""


@dataclass
class Credentials:
    username: str
    password: str
    login_url: str | None = None


@dataclass
class CrawlConfig:
    base_url: str
    max_pages: int = 15
    max_depth: int = 3
    allow_local: bool = False
    extra_domains: list[str] = field(default_factory=list)
    request_delay_ms: int = 250
    credentials: Credentials | None = None
    allow_destructive: bool = False
    artifacts_dir: Path = Path("data/artifacts/tmp")
    nav_timeout_ms: int = 20_000


@dataclass
class PageData:
    url: str
    depth: int
    status_code: int | None = None
    title: str = ""
    load_error: str | None = None
    dom_summary: dict[str, Any] = field(default_factory=dict)
    a11y_tree: str = ""
    screenshot_path: str | None = None
    console_errors: list[str] = field(default_factory=list)
    network_failures: list[dict[str, Any]] = field(default_factory=list)
    api_calls: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    linked_from: str | None = None


@dataclass
class LoginOutcome:
    attempted: bool = False
    success: bool = False
    login_url: str | None = None
    landing_url: str | None = None
    message: str = ""


@dataclass
class CrawlResult:
    pages: list[PageData]
    login: LoginOutcome
    storage_state_path: str | None
    blocked_requests: list[str]


def normalize_url(href: str, base: str) -> str | None:
    if not href:
        return None
    absolute = urljoin(base, href.strip())
    absolute, _ = urldefrag(absolute)
    p = urlsplit(absolute)
    if p.scheme not in ("http", "https"):
        return None
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def same_origin(url: str, base: str) -> bool:
    a, b = urlsplit(url), urlsplit(base)
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def looks_destructive(url: str, text: str = "") -> bool:
    path = urlsplit(url).path.replace("-", " ").replace("_", " ").replace("/", " ")
    return bool(DESTRUCTIVE.search(path) or DESTRUCTIVE.search(text or ""))


class _PageRecorder:
    """Collects console errors, failed requests and XHR/fetch traffic for one page visit."""

    def __init__(self, page: Page) -> None:
        self.console: list[str] = []
        self.failures: list[dict[str, Any]] = []
        self.api: list[Response] = []
        page.on("console", self._on_console)
        page.on("pageerror", lambda exc: self.console.append(f"Uncaught {exc.name}: {exc.message}"[:500]))
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)

    def _on_console(self, msg: ConsoleMessage) -> None:
        if msg.type == "error":
            text = msg.text
            if text.startswith("Failed to load resource"):
                return  # duplicated by response/requestfailed tracking
            self.console.append(text[:500])

    def _on_failed(self, req: Request) -> None:
        err = req.failure or "failed"
        if "ERR_BLOCKED_BY_CLIENT" in err or "ERR_ABORTED" in err:
            return
        self.failures.append({"url": req.url, "method": req.method, "resource_type": req.resource_type,
                              "error": err})

    def _on_response(self, resp: Response) -> None:
        rt = resp.request.resource_type
        if rt in ("xhr", "fetch"):
            self.api.append(resp)
        if resp.status >= 400 and rt != "document" and not resp.url.endswith("/favicon.ico"):
            self.failures.append({"url": resp.url, "method": resp.request.method, "resource_type": rt,
                                  "status": resp.status})


async def _api_record(resp: Response) -> dict[str, Any]:
    req = resp.request
    rec: dict[str, Any] = {"method": req.method, "url": resp.url, "status": resp.status,
                           "content_type": resp.headers.get("content-type", ""),
                           "resource_type": req.resource_type}
    try:
        timing = req.timing
        if timing and timing.get("responseEnd", -1) >= 0:
            rec["duration_ms"] = round(timing["responseEnd"] - max(0, timing.get("requestStart", 0)), 1)
    except PWError:
        pass
    if "json" in rec["content_type"]:
        try:
            body = await resp.body()
            rec["body_sample"] = body[:2000].decode("utf-8", "replace")
        except PWError:
            pass
    return rec


async def _settle(page: Page, ms: int = 2500) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=ms)
    except PWError:
        pass


async def visit(context: BrowserContext, url: str, depth: int, cfg: CrawlConfig, index: int) -> PageData:
    page = await context.new_page()
    rec = _PageRecorder(page)
    data = PageData(url=url, depth=depth)
    try:
        resp = await page.goto(url, wait_until="load", timeout=cfg.nav_timeout_ms)
        await _settle(page)
        await page.wait_for_timeout(300)
        data.status_code = resp.status if resp else None
        data.url = normalize_url(page.url, url) or url
        data.title = (await page.title())[:200]
        summary = await page.evaluate(DOM_SUMMARY_JS)
        links = []
        for link in summary.get("links", []):
            n = normalize_url(link.get("href", ""), page.url)
            if n:
                links.append(n)
                link["href"] = n
        data.links = sorted(set(links))
        data.forms = summary.get("forms", [])
        data.dom_summary = summary
        try:
            data.a11y_tree = (await page.locator("body").aria_snapshot(timeout=5000))[:6000]
        except PWError:
            data.a11y_tree = ""
        shot = cfg.artifacts_dir / "pages" / f"page-{index:02d}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(shot), full_page=False)
        data.screenshot_path = str(shot)
        data.api_calls = [await _api_record(r) for r in rec.api[:30]]
    except PWError as exc:
        data.load_error = str(exc).splitlines()[0][:300]
    finally:
        data.console_errors = list(dict.fromkeys(rec.console))[:20]
        data.network_failures = rec.failures[:30]
        await page.close()
    return data


async def login(context: BrowserContext, cfg: CrawlConfig) -> LoginOutcome:
    """Best-effort generic login: find a page with a password field, fill, submit."""
    assert cfg.credentials is not None
    creds = cfg.credentials
    out = LoginOutcome(attempted=True)
    page = await context.new_page()
    try:
        candidates = [creds.login_url] if creds.login_url else []
        candidates += [cfg.base_url]
        found = None
        for cand in candidates:
            target = normalize_url(cand, cfg.base_url) if cand else None
            if not target or not same_origin(target, cfg.base_url):
                continue
            await page.goto(target, wait_until="load", timeout=cfg.nav_timeout_ms)
            if await page.locator("input[type=password]").count():
                found = page.url
                break
            # follow a "Log in" / "Sign in" link
            for a in await page.locator("a[href]").all():
                text = (await a.inner_text()).strip()
                if LOGIN_LINK.search(text):
                    href = normalize_url(await a.get_attribute("href") or "", page.url)
                    if href and same_origin(href, cfg.base_url):
                        await page.goto(href, wait_until="load", timeout=cfg.nav_timeout_ms)
                        if await page.locator("input[type=password]").count():
                            found = page.url
                        break
            if found:
                break
        if not found:
            for path in COMMON_LOGIN_PATHS:
                resp = await page.goto(urljoin(cfg.base_url, path), wait_until="load", timeout=cfg.nav_timeout_ms)
                if resp and resp.ok and await page.locator("input[type=password]").count():
                    found = page.url
                    break
        if not found:
            out.message = "No login form (password field) found"
            return out
        out.login_url = found
        pw = page.locator("input[type=password]").first
        form = page.locator("form:has(input[type=password])").first
        scope = form if await form.count() else page.locator("body")
        user_field = scope.locator(
            "input[type=email], input[autocomplete=username], input[type=text], input:not([type])").first
        if await user_field.count():
            await user_field.fill(creds.username)
        await pw.fill(creds.password)
        submit = scope.locator("button[type=submit], button:not([type]), input[type=submit]").first
        before = page.url
        if await submit.count():
            await submit.click()
        else:
            await pw.press("Enter")
        await page.wait_for_load_state("load", timeout=cfg.nav_timeout_ms)
        await _settle(page, 2000)
        still_pw = await page.locator("input[type=password]").count()
        out.success = not still_pw
        if out.success and page.url != before:
            out.landing_url = normalize_url(page.url, cfg.base_url)
        out.message = "Logged in" if out.success else "Login form still shown after submit (wrong credentials?)"
    except PWError as exc:
        out.message = f"Login failed: {str(exc).splitlines()[0][:200]}"
    finally:
        await page.close()
    return out


def persist_pages(run_id: int, pages: list[PageData], secrets: list[str]) -> list[int]:
    """Store the site map (Page rows). Secrets are masked out of everything stored."""
    from app.db.models import Page as PageRow
    from app.db.session import session_scope
    from app.security.secrets import mask_obj, mask_text

    ids: list[int] = []
    with session_scope() as s:
        for p in pages:
            row = PageRow(run_id=run_id, url=p.url, title=mask_text(p.title, secrets), status_code=p.status_code,
                          depth=p.depth, load_error=p.load_error, dom_summary=mask_obj(p.dom_summary, secrets),
                          a11y_tree=mask_text(p.a11y_tree, secrets), screenshot_path=p.screenshot_path,
                          console_errors=mask_obj(p.console_errors, secrets),
                          network_failures=mask_obj(p.network_failures, secrets),
                          api_calls=mask_obj(p.api_calls, secrets), links=p.links,
                          forms=mask_obj(p.forms, secrets))
            row.dom_summary["linked_from"] = p.linked_from
            s.add(row)
            s.flush()
            ids.append(int(row.id or 0))
        s.commit()
    return ids


async def crawl(cfg: CrawlConfig, *, should_cancel: Callable[[], bool] = lambda: False,
                on_page: Callable[[PageData], None] | None = None) -> CrawlResult:
    base = normalize_url(cfg.base_url, cfg.base_url) or cfg.base_url
    guard = GuardConfig(base_url=base, allow_local=cfg.allow_local, extra_domains=cfg.extra_domains)
    stats = GuardStats()
    pages: list[PageData] = []
    login_out = LoginOutcome()
    state_path: str | None = None
    async with launch_browser() as browser:
        context = await new_context(browser, guard, stats=stats)
        try:
            if cfg.credentials and cfg.credentials.username:
                login_out = await login(context, cfg)
                if login_out.success:
                    state_file = cfg.artifacts_dir / "auth" / "storage_state.json"
                    state_file.parent.mkdir(parents=True, exist_ok=True)
                    await context.storage_state(path=str(state_file))
                    state_path = str(state_file)
            queue: list[tuple[str, int, str | None]] = [(base, 0, None)]
            seen: set[str] = {base}
            for extra in (login_out.login_url, login_out.landing_url):
                n = normalize_url(extra, base) if extra else None
                if n and n not in seen and same_origin(n, base) and not looks_destructive(n):
                    seen.add(n)
                    queue.append((n, 1, base))
            while queue and len(pages) < cfg.max_pages:
                if should_cancel():
                    break
                url, depth, parent = queue.pop(0)
                data = await visit(context, url, depth, cfg, len(pages))
                data.linked_from = parent
                pages.append(data)
                if on_page:
                    on_page(data)
                seen.add(data.url)
                if depth < cfg.max_depth and (data.status_code or 0) < 400:
                    texts = {lnk["href"]: lnk.get("text", "") for lnk in data.dom_summary.get("links", [])}
                    for link in data.links:
                        if link in seen or not same_origin(link, base):
                            continue
                        if SKIP_EXT.search(urlsplit(link).path):
                            continue
                        if not cfg.allow_destructive and looks_destructive(link, texts.get(link, "")):
                            continue
                        seen.add(link)
                        queue.append((link, depth + 1, data.url))
                await asyncio.sleep(cfg.request_delay_ms / 1000)
        finally:
            await context.close()
    return CrawlResult(pages=pages, login=login_out, storage_state_path=state_path,
                       blocked_requests=stats.blocked[:50])
