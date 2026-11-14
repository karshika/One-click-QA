"""Stage 3 – Generate one Playwright test per journey.

Order of preference per journey:
  1. ScriptCache hit (last passing script for project+journey) → 0 LLM calls
  2. template journey → deterministic template script
  3. code model generation (selectors restricted to the captured page data)
Every script passes the static validator; an invalid LLM script gets one fix attempt.
"""

from __future__ import annotations

import hashlib
import json

from sqlmodel import select

from app.core.context import JourneyData, RunContext, TestCaseData
from app.db.models import ScriptCache
from app.db.session import session_scope
from app.llm import prompts
from app.llm.provider import LLMError
from app.security.script_validator import extract_code, validate_script
from app.stages.crawler import PageData

USER_FIELD = "input[type=email], input[autocomplete=username], input[type=text], input:not([type])"
SUBMIT = "button[type=submit], button:not([type]), input[type=submit]"


def script_hash(script: str) -> str:
    return hashlib.sha256(script.encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- templates
def _login_script(path: str, password_expr: str, positive: bool) -> str:
    assertion = ('expect(page.locator("input[type=password]")).to_have_count(0)' if positive else
                 'expect(page.locator("input[type=password]").first).to_be_visible()')
    return f'''def test_journey(page, expect, ctx):
    page.goto(ctx.base_url + {path!r})
    form = page.locator("form:has(input[type=password])").first
    form.locator({USER_FIELD!r}).first.fill(ctx.username)
    form.locator("input[type=password]").first.fill({password_expr})
    form.locator({SUBMIT!r}).first.click()
    page.wait_for_load_state()
    {assertion}
'''


def _field_selector(f: dict) -> str | None:
    if f.get("id"):
        return f'[id="{f["id"]}"]'
    if f.get("name"):
        return f'[name="{f["name"]}"]'
    return None


def _form_script(path: str, form: dict) -> str | None:
    first_sel = next((s for s in (_field_selector(f) for f in form.get("fields", [])) if s), None)
    if not first_sel:
        return None
    lines = [f"    page.goto(ctx.base_url + {path!r})"]
    for f in form.get("fields", []):
        sel = _field_selector(f)
        t = (f.get("type") or "text").lower()
        if not sel or t in ("hidden", "submit", "button", "file", "image", "reset"):
            continue
        name = (f.get("name") or f.get("label") or "").lower()
        if t == "checkbox":
            lines.append(f"    page.locator({sel!r}).first.check()")
            continue
        if t == "radio":
            lines.append(f"    page.locator({sel!r}).first.check()")
            continue
        if t == "select":
            lines.append(f"    page.locator({sel!r}).first.select_option(index=1)")
            continue
        if t == "email" or "email" in name:
            value = "ctx.unique_email()"
        elif t == "tel" or "phone" in name:
            value = repr("5555550100")
        elif t == "number":
            value = repr("1")
        elif t == "url":
            value = repr("https://example.com")
        elif t == "date":
            value = repr("2030-01-15")
        elif t == "textarea":
            value = repr("Automated QA test message from OneClickQA. Please ignore.")
        elif "name" in name:
            value = repr("QA Tester")
        else:
            value = 'ctx.unique_text("qa")'
        lines.append(f"    page.locator({sel!r}).first.fill({value})")
    lines += [
        f"    page.locator('form').filter(has=page.locator({first_sel!r})).locator({SUBMIT!r}).first.click()",
        "    page.wait_for_load_state()",
        '    expect(page.locator("body")).not_to_contain_text(re.compile(r"Internal Server Error|Traceback|'
        'Exception|Bad Gateway", re.IGNORECASE))',
    ]
    return "import re\n\n\ndef test_journey(page, expect, ctx):\n" + "\n".join(lines) + "\n"


def template_script(ctx: RunContext, j: JourneyData) -> str | None:
    if j.key.startswith("tpl-login") and ctx.credentials:
        if j.key == "tpl-login-valid":
            return _login_script(j.start_url, "ctx.password", True)
        if j.key == "tpl-login-wrong-password":
            return _login_script(j.start_url, 'ctx.password + "-wrong-9"', False)
        if j.key == "tpl-login-case-password":
            if ctx.credentials.password.swapcase() == ctx.credentials.password:
                return None  # password has no letters → test would be meaningless
            return _login_script(j.start_url, "ctx.password.swapcase()", False)
    if j.key.startswith("tpl-form-"):
        page = next((p for p in ctx.pages if ctx.path_of(p.url) == j.start_url), None)
        if page:
            for f in (page.dom_summary or {}).get("forms", []):
                if f.get("fields") and not any(x.get("type") == "password" for x in f["fields"]):
                    return _form_script(j.start_url, f)
    return None


# ----------------------------------------------------------------------------- LLM generation
def page_data_for(ctx: RunContext, j: JourneyData) -> str:
    wanted = [j.start_url] + [p for p in j.pages if p != j.start_url]
    blocks = []
    for path in wanted[:3]:
        page: PageData | None = next((p for p in ctx.pages if ctx.path_of(p.url) == path), None)
        if not page:
            continue
        d = page.dom_summary or {}
        info = {
            "path": path, "title": page.title,
            "headings": d.get("headings", [])[:8],
            "forms": d.get("forms", [])[:3],
            "buttons": d.get("buttons", [])[:15],
            "links": [{"text": lnk.get("text"), "path": ctx.path_of(lnk.get("href", ""))}
                      for lnk in d.get("links", [])[:25]],
            "testids": d.get("testids", [])[:20],
            "alerts": d.get("alerts", []),
        }
        body = json.dumps(info, ensure_ascii=False) + "\nARIA snapshot:\n" + (page.a11y_tree or "")[:2500]
        blocks.append(prompts.untrusted(f"page_{path}", body, max_chars=6000))
    return "\n\n".join(blocks)


def journey_json(j: JourneyData) -> str:
    return json.dumps({"name": j.name, "kind": j.kind, "description": j.description, "start_url": j.start_url,
                       "steps": j.steps, "expected": j.expected, "negative": j.negative}, ensure_ascii=False)


async def llm_fix_invalid(ctx: RunContext, j: JourneyData, script: str, errors: str) -> str | None:
    prompt = prompts.render("ui_repair", journey=journey_json(j), script=script, attempt="validation",
                            error=f"The script was rejected by the sandbox validator: {errors}",
                            failure_url=j.start_url, dom_snippet=page_data_for(ctx, j))
    try:
        res = await ctx.llm.text("generate", prompt, max_tokens=2000)
    except LLMError:
        return None
    return extract_code(res.text)


async def generate_one(ctx: RunContext, j: JourneyData) -> TestCaseData | None:
    with session_scope() as s:
        cached = s.exec(select(ScriptCache).where(ScriptCache.project_id == ctx.project_id,
                                                  ScriptCache.journey_key == j.key)).first()
    if cached:
        v = validate_script(cached.script, base_url=ctx.base_url)
        if v.ok:
            return TestCaseData(journey=j, script=cached.script, source="cached",
                                attempts=[{"attempt": 0, "source": "cached"}])

    if j.source == "template":
        script = template_script(ctx, j)
        if script is None:
            return None
        v = validate_script(script, base_url=ctx.base_url)
        return TestCaseData(journey=j, script=script, source="template", valid=v.ok,
                            validation_error=v.message or None, attempts=[{"attempt": 0, "source": "template"}])

    prompt = prompts.render("ui_generator", journey=journey_json(j), base_url=ctx.base_url,
                            pages=page_data_for(ctx, j))
    try:
        res = await ctx.llm.text("generate", prompt, max_tokens=2000)
    except LLMError as exc:
        ctx.degraded["generate"] = f"{type(exc).__name__}: {exc}"
        return None
    script = extract_code(res.text)
    attempts = [{"attempt": 0, "source": "generated", "cache_hit": res.cache_hit}]
    v = validate_script(script, base_url=ctx.base_url)
    if not v.ok:
        attempts[0]["validation_error"] = v.message
        fixed = await llm_fix_invalid(ctx, j, script, v.message)
        if fixed:
            v2 = validate_script(fixed, base_url=ctx.base_url)
            attempts.append({"attempt": 1, "source": "validation_fix", "validation_error": v2.message or None})
            if v2.ok:
                return TestCaseData(journey=j, script=fixed, source="repaired", attempts=attempts)
            script, v = fixed, v2
    return TestCaseData(journey=j, script=script, source="generated", valid=v.ok,
                        validation_error=v.message or None, attempts=attempts)


async def generate(ctx: RunContext) -> list[TestCaseData]:
    out: list[TestCaseData] = []
    for j in ctx.journeys:
        if ctx.should_cancel():
            break
        tc = await generate_one(ctx, j)
        if tc is not None:
            out.append(tc)
    return out
