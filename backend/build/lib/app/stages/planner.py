"""Stage 2 – Plan user journeys.

The planner model gets a COMPACT site map (paths, titles, headings, forms, buttons, links –
never raw HTML, never credentials) and returns a JourneyPlan. Scope is enforced in code:
journeys may only start on / visit crawled same-site paths.

Deterministic baseline journeys (login positive/negative, forms) are always added when the
site map supports them; with no LLM (no key / budget exhausted) they are the whole plan.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.context import JourneyData, RunContext
from app.llm import prompts
from app.llm.provider import LLMError
from app.llm.schemas import JourneyPlan
from app.stages.crawler import PageData, looks_destructive


def slug(text: str, n: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "journey"


def compact_site_map(ctx: RunContext, pages: list[PageData]) -> str:
    out = []
    for p in pages:
        d = p.dom_summary or {}
        entry: dict[str, Any] = {"path": ctx.path_of(p.url), "status": p.status_code, "title": p.title[:80]}
        if d.get("headings"):
            entry["headings"] = d["headings"][:6]
        forms = []
        for f in d.get("forms", [])[:3]:
            forms.append({
                "method": f.get("method"), "action": f.get("action"), "submit": f.get("submit"),
                "fields": [(x.get("label") or x.get("name") or x.get("placeholder") or x.get("type"))
                           + f" ({x.get('type')})" for x in f.get("fields", [])[:8]],
            })
        if forms:
            entry["forms"] = forms
        btns = [b.get("text") or b.get("aria_label") for b in d.get("buttons", []) if b.get("text") or
                b.get("aria_label")]
        if btns:
            entry["buttons"] = btns[:10]
        links = sorted({ctx.path_of(lnk["href"]) for lnk in d.get("links", [])
                        if lnk.get("href", "").startswith(ctx.base_url.rstrip("/"))})
        if links:
            entry["links"] = links[:15]
        if d.get("alerts"):
            entry["alerts"] = d["alerts"][:3]
        out.append(entry)
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def find_login_page(pages: list[PageData]) -> PageData | None:
    for p in pages:
        for f in (p.dom_summary or {}).get("forms", []):
            if any(x.get("type") == "password" for x in f.get("fields", [])):
                return p
    return None


def template_journeys(ctx: RunContext) -> list[JourneyData]:
    """Deterministic baseline journeys derived from the site map."""
    js: list[JourneyData] = []
    login_page = find_login_page(ctx.pages)
    if login_page and ctx.credentials and ctx.credentials.username:
        path = ctx.path_of(login_page.url)
        common: dict[str, Any] = dict(kind="login", start_url=path, pages=[path], source="template")
        js.append(JourneyData(key="tpl-login-valid", name="Login with valid credentials", priority=1,
                              description="The configured test account can sign in.",
                              steps=["Open the sign-in page", "Enter the test account email and password",
                                     "Submit the form"],
                              expected=["The password field is no longer shown (user is signed in)"], **common))
        js.append(JourneyData(key="tpl-login-wrong-password", name="Login rejects a wrong password", priority=1,
                              description="Signing in with an incorrect password must fail.", negative=True,
                              steps=["Open the sign-in page", "Enter the test account email",
                                     "Enter an incorrect password", "Submit the form"],
                              expected=["The user stays on the sign-in form"], **common))
        js.append(JourneyData(key="tpl-login-case-password", name="Login rejects a case-changed password",
                              priority=1, negative=True,
                              description="Passwords must be case-sensitive: the correct password with "
                                          "swapped letter case must be rejected.",
                              steps=["Open the sign-in page", "Enter the test account email",
                                     "Enter the correct password with upper/lower case swapped", "Submit the form"],
                              expected=["The user stays on the sign-in form"], **common))
    n_forms = 0
    for p in ctx.ok_pages:
        for f in (p.dom_summary or {}).get("forms", []):
            fields = f.get("fields", [])
            if not fields or any(x.get("type") == "password" for x in fields):
                continue
            if f.get("method", "get") != "post" or looks_destructive(p.url, f.get("submit", "")):
                continue
            path = ctx.path_of(p.url)
            js.append(JourneyData(key=f"tpl-form-{slug(path)}", name=f"Submit the form on {path}", kind="form",
                                  description="Fill every field with valid sample data and submit.", priority=2,
                                  start_url=path, pages=[path], source="template",
                                  steps=["Open the page", "Fill all fields with valid data", "Submit"],
                                  expected=["No server error is shown after submitting"]))
            n_forms += 1
            break
        if n_forms >= 2:
            break
    return js


def _sanitize(ctx: RunContext, plan: JourneyPlan, covered: set[str]) -> list[JourneyData]:
    crawled = {ctx.path_of(p.url) for p in ctx.pages}
    out: list[JourneyData] = []
    seen: set[str] = set(covered)
    for j in sorted(plan.journeys, key=lambda j: j.priority):
        start = ctx.path_of(j.start_url if j.start_url.startswith("http") else ctx.base_url.rstrip("/") +
                            "/" + j.start_url.lstrip("/"))
        if start not in crawled:
            continue  # scope enforcement: only crawled same-site pages
        if looks_destructive(start, " ".join(j.steps)) and not ctx.allow_destructive:
            continue
        key = f"llm-{slug(j.kind + '-' + j.name)}"
        if key in seen:
            continue
        seen.add(key)
        pages = [pp for pp in (ctx.path_of(x if x.startswith("http") else "/" + x.lstrip("/")) for x in j.pages)
                 if pp in crawled]
        out.append(JourneyData(key=key, name=j.name, kind=j.kind, description=j.description, priority=j.priority,
                               start_url=start, pages=pages or [start], steps=j.steps, expected=j.expected,
                               negative=j.negative, source="llm"))
        if len(out) >= ctx.max_journeys:
            break
    return out


async def plan(ctx: RunContext) -> list[JourneyData]:
    baseline = template_journeys(ctx)
    journeys = list(baseline)
    if not ctx.ok_pages:
        return journeys
    site_map = compact_site_map(ctx, ctx.pages)
    covered = [f"{j.name} ({j.start_url})" for j in baseline]
    prompt = prompts.render(
        "planner", base_url=ctx.base_url, has_credentials=str(bool(ctx.credentials)).lower(),
        max_journeys=ctx.max_journeys, already_covered=json.dumps(covered),
        site_map=prompts.untrusted("site_map", site_map, max_chars=14_000))
    try:
        result = await ctx.llm.structured("plan", prompt, JourneyPlan, max_tokens=3000)
        journeys += _sanitize(ctx, result, {j.key for j in baseline})
    except LLMError as exc:
        ctx.degraded["plan"] = f"{type(exc).__name__}: {exc}"
    return journeys
