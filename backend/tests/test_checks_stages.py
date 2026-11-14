"""a11y / perf / API stages against the live demo app."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.context import RunContext
from app.llm.client import LLMClient
from app.stages.a11y_checks import run_a11y
from app.stages.api_checks import boundary_values, resolve_refs, schema_errors, template_of
from app.stages.crawler import CrawlConfig, crawl
from app.stages.perf_checks import median_metrics, rate, run_perf
from tests.fakes import fast_limiter


def test_perf_rating_thresholds() -> None:
    assert rate("lcp", 2400) == "good" and rate("lcp", 3000) == "needs-improvement" and rate("lcp", 4100) == "poor"
    assert rate("cls", 0.05) == "good" and rate("cls", 0.3) == "poor"
    assert median_metrics([{"lcp": 1}, {"lcp": 5}, {"lcp": 3}])["lcp"] == 3


def test_api_helpers() -> None:
    spec = {"components": {"schemas": {"A": {"type": "object", "properties": {"n": {"type": "integer"}},
                                             "required": ["n"]}}}}
    s = resolve_refs({"$ref": "#/components/schemas/A"}, spec)
    assert schema_errors({"n": 1}, s) == []
    assert schema_errors({"n": "1"}, s)
    assert schema_errors(None, {"type": "integer", "nullable": True}) == []
    assert ("zero", 0) in boundary_values({"type": "integer"})
    assert template_of("http://h/api/products/12") == "/api/products/{id}"


@pytest.fixture(scope="module")
async def ctx(demo_url: str, tmp_path_factory: pytest.TempPathFactory) -> RunContext:
    tmp: Path = tmp_path_factory.mktemp("checks")
    res = await crawl(CrawlConfig(base_url=demo_url, allow_local=True, request_delay_ms=0, artifacts_dir=tmp,
                                  max_pages=12))
    c = RunContext(run_id=0, project_id=0, base_url=demo_url, llm=LLMClient(None, limiter=fast_limiter()),
                   artifacts_dir=tmp, openapi_url="/openapi.json", allow_local=True, request_delay_ms=0, perf_runs=1)
    c.pages = res.pages
    return c


@pytest.mark.browser
async def test_a11y_finds_planted_and_nothing_else(ctx: RunContext) -> None:
    results = await run_a11y(ctx)
    by_url = {r.url.replace(ctx.base_url, ""): {v["rule_id"] for v in r.violations} for r in results}
    print(by_url)
    assert {"image-alt", "button-name", "color-contrast"} <= by_url["/"]
    others = {u: rules for u, rules in by_url.items() if u != "/" and rules}
    assert others == {}, others  # no false positives elsewhere
    assert "/old-pricing" not in by_url  # 404 pages are not audited


@pytest.mark.browser
async def test_perf_detects_slow_page_and_cls(ctx: RunContext) -> None:
    results = await run_perf(ctx)
    by_url = {r.url.replace(ctx.base_url, ""): r for r in results}
    print({u: (r.metrics, r.ratings) for u, r in by_url.items()})
    assert by_url["/reports"].ratings["ttfb"] == "poor"
    assert by_url["/"].metrics["cls"] is not None and by_url["/"].metrics["cls"] > 0.1
    fast = [u for u, r in by_url.items() if u not in ("/", "/reports") and "poor" in r.ratings.values()]
    assert fast == []


async def test_api_openapi_checks(ctx: RunContext) -> None:
    from app.stages.api_checks import run_api_checks

    checks, mode = await run_api_checks(ctx)
    assert mode == "openapi"
    failed = {(c.path, c.check) for c in checks if c.status == "failed"}
    print(failed, [(c.path, c.check, c.message) for c in checks if c.status == "warning"])
    assert ("/api/products/{product_id}", "boundary") in failed  # B7
    assert ("/api/stats", "schema") in failed  # B8
    assert failed == {("/api/products/{product_id}", "boundary"), ("/api/stats", "schema")}
    auth = [c for c in checks if c.check == "auth_required"]
    assert auth and all(c.status == "passed" for c in auth)


async def test_api_observed_mode(ctx: RunContext) -> None:
    from app.stages.api_checks import run_api_checks

    ctx.openapi_url = None
    checks, mode = await run_api_checks(ctx)
    ctx.openapi_url = "/openapi.json"
    assert mode == "observed"
    assert any(c.check == "boundary" and c.status == "failed" and "/api/products/" in c.url for c in checks)
