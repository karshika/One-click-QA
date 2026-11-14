"""Plan → generate → run against the live demo app (deterministic + mocked LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from app.core.context import JourneyData, RunContext, TestCaseData
from app.db.models import Project, ScriptCache, utcnow
from app.db.session import session_scope
from app.llm.client import LLMClient
from app.stages import planner, ui_generator, ui_runner
from app.stages.crawler import CrawlConfig, Credentials, crawl
from tests.fakes import ScriptedProvider, fast_limiter

pytestmark = pytest.mark.browser

CREDS = Credentials("demo@example.com", "Demo-Pass-2024")


def _project(base: str) -> int:
    with session_scope() as s:
        p = Project(name="demo", base_url=base, consent_acknowledged_at=utcnow())
        s.add(p)
        s.commit()
        return int(p.id or 0)


@pytest.fixture(scope="module")
async def crawled(demo_url: str, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("crawl")
    res = await crawl(CrawlConfig(base_url=demo_url, allow_local=True, request_delay_ms=0, credentials=CREDS,
                                  artifacts_dir=tmp, max_pages=8))
    return res


def make_ctx(demo_url: str, tmp: Path, crawled, provider=None) -> RunContext:  # type: ignore[no-untyped-def]
    ctx = RunContext(run_id=0, project_id=_project(demo_url), base_url=demo_url,
                     llm=LLMClient(provider, limiter=fast_limiter()), artifacts_dir=tmp, credentials=CREDS,
                     secrets=[CREDS.password], allow_local=True, test_timeout_s=60)
    ctx.pages = crawled.pages
    return ctx


async def test_deterministic_templates_find_login_flaw(demo_url: str, tmp_path: Path, crawled) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(demo_url, tmp_path, crawled)
    ctx.journeys = await planner.plan(ctx)  # no LLM → baseline only
    keys = {j.key for j in ctx.journeys}
    assert {"tpl-login-valid", "tpl-login-wrong-password", "tpl-login-case-password"} <= keys
    assert any(k.startswith("tpl-form-") for k in keys)
    assert "plan" in ctx.degraded  # fell back deterministically
    ctx.tests = await ui_generator.generate(ctx)
    assert all(t.valid for t in ctx.tests), [t.validation_error for t in ctx.tests]
    assert CREDS.password not in "".join(t.script for t in ctx.tests)  # creds never in scripts
    results = {tc.journey.key: out for tc, out in await ui_runner.run_tests(ctx)}
    assert results["tpl-login-valid"].status == "passed", results["tpl-login-valid"].error
    assert results["tpl-login-wrong-password"].status == "passed"
    # B1: case-changed password is accepted → the negative test's assertion fails (twice → failed)
    case = results["tpl-login-case-password"]
    assert case.status == "failed" and case.error_kind == "assertion"
    assert case.attempts == 2 and case.trace_path and Path(case.trace_path).exists()
    form = next(v for k, v in results.items() if k.startswith("tpl-form-"))
    assert form.status == "passed", form.error


BROKEN = '''def test_journey(page, expect, ctx):
    page.goto(ctx.base_url + "/contact")
    page.get_by_label("Full name").fill("QA")
    expect(page.get_by_role("heading", name="Contact us")).to_be_visible()
'''
FIXED = '''```python
def test_journey(page, expect, ctx):
    page.goto(ctx.base_url + "/contact")
    page.get_by_label("Name").fill("QA")
    expect(page.get_by_role("heading", name="Contact us")).to_be_visible()
```'''


async def test_self_repair_and_script_cache(demo_url: str, tmp_path: Path, crawled) -> None:  # type: ignore[no-untyped-def]
    provider = ScriptedProvider([FIXED])
    ctx = make_ctx(demo_url, tmp_path, crawled, provider)
    j = JourneyData(key="llm-form-contact", name="Contact form", kind="form", description="", priority=2,
                    start_url="/contact", pages=["/contact"], steps=["fill name"], expected=["heading"])
    ctx.tests = [TestCaseData(journey=j, script=BROKEN, source="generated")]
    [(tc, out)] = await ui_runner.run_tests(ctx)
    assert out.status == "passed", out.error
    assert tc.source == "repaired"
    assert len(provider.requests) == 1  # one repair call
    assert "Full name" in provider.requests[0].messages[-1]["content"]  # error + script sent
    with session_scope() as s:
        cached = s.exec(select(ScriptCache).where(ScriptCache.journey_key == "llm-form-contact")).first()
    assert cached and 'get_by_label("Name")' in cached.script
    # re-generation uses the cache: zero LLM calls
    ctx2 = make_ctx(demo_url, tmp_path, crawled, ScriptedProvider([]))
    ctx2.project_id = ctx.project_id
    tc2 = await ui_generator.generate_one(ctx2, j)
    assert tc2 is not None and tc2.source == "cached" and ctx2.llm.usage.calls == 0


async def test_sandbox_blocks_escape_attempts(demo_url: str, tmp_path: Path, crawled) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(demo_url, tmp_path, crawled)
    # network: runtime guard blocks hosts other than the target (even if built dynamically)
    res = await ui_runner.run_in_sandbox(
        'def test_journey(page, expect, ctx):\n    page.goto("https://" + "example" + ".com/")\n',
        ctx=ctx, out_dir=tmp_path / "net")
    assert res["status"] == "failed" and "BLOCKED_BY_CLIENT" in res["error"] and res["blocked"]
    # builtins: open/__import__ of os unavailable even without the static validator
    res = await ui_runner.run_in_sandbox(
        "def test_journey(page, expect, ctx):\n    open('pwned.txt', 'w')\n", ctx=ctx, out_dir=tmp_path / "fs")
    assert res["status"] == "failed" and "NameError" in res["error"]
    res = await ui_runner.run_in_sandbox(
        "import os\ndef test_journey(page, expect, ctx):\n    pass\n", ctx=ctx, out_dir=tmp_path / "imp")
    assert res["status"] == "failed" and "blocked in the sandbox" in res["error"]
