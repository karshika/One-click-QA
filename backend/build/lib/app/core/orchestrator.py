"""Runs the nine pipeline stages in order with per-stage fault isolation.

* every stage gets a StageResult row: status (passed|degraded|failed|skipped|cancelled), timing,
  LLM-call count and error; a failure in one stage never aborts the others
* `degraded` = the stage completed with its deterministic fallback (no key / budget exhausted)
* cancellation is checked between stages and inside long loops
* run-level LLM usage (calls, cache hits, tokens) is persisted after every stage
"""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlmodel import col, select

from app.config import get_settings
from app.core import events
from app.core.context import RunContext
from app.db.models import (
    A11yViolation,
    ApiCheckResult,
    Finding,
    Journey,
    PerfMetric,
    Project,
    Run,
    StageResult,
    TestCase,
    TestResult,
    utcnow,
)
from app.db.session import session_scope
from app.llm.client import build_client
from app.security.secrets import decrypt_json, mask_text
from app.stages import analyst, planner, reporter, ui_generator, ui_runner
from app.stages.a11y_checks import run_a11y
from app.stages.api_checks import run_api_checks
from app.stages.crawler import CrawlConfig, Credentials, crawl, persist_pages
from app.stages.perf_checks import run_perf

log = logging.getLogger(__name__)
StageFn = Callable[[RunContext], Awaitable[dict[str, Any]]]


class StageSkipped(Exception):
    pass


# ----------------------------------------------------------------------------- context
def build_context(run: Run, project: Project) -> RunContext:
    s = get_settings()
    cfg = run.config or {}
    auth = decrypt_json(project.auth_encrypted)
    creds = None
    if auth.get("username") and auth.get("password"):
        creds = Credentials(auth["username"], auth["password"], auth.get("login_url") or None)
    secrets = [v for k, v in auth.items() if k in ("password", "token") and v]
    if s.mistral_api_key:
        secrets.append(s.mistral_api_key.get_secret_value())
    psettings = project.settings or {}
    art = s.artifacts_dir / str(run.id)
    art.mkdir(parents=True, exist_ok=True)
    run_id = int(run.id or 0)

    def should_cancel() -> bool:
        with session_scope() as sess:
            r = sess.get(Run, run_id)
            return bool(r and r.cancel_requested)

    def log_fn(stage: str, msg: str) -> None:
        events.emit(run_id, "log", mask_text(msg, secrets), stage=stage)

    return RunContext(
        run_id=run_id, project_id=int(project.id or 0), base_url=project.base_url.rstrip("/") + "/",
        openapi_url=project.openapi_url or None,
        llm=build_client(run_id=run_id, secrets=secrets, run_budget=int(cfg.get("llm_budget",
                                                                               s.max_llm_calls_per_run))),
        artifacts_dir=art, credentials=creds, secrets=secrets, allow_local=s.allow_local_targets,
        extra_domains=s.extra_domains + [d.lower() for d in psettings.get("extra_domains", [])],
        max_pages=int(cfg.get("max_pages", s.default_max_pages)),
        max_depth=int(cfg.get("max_depth", s.default_max_depth)),
        request_delay_ms=int(cfg.get("request_delay_ms", s.request_delay_ms)),
        max_journeys=int(cfg.get("max_journeys", s.max_journeys)),
        allow_destructive=bool(psettings.get("allow_destructive", False)),
        allowed_api_methods=[m.upper() for m in psettings.get("allowed_api_methods", ["GET", "HEAD"])],
        test_timeout_s=s.test_timeout_s, perf_runs=int(cfg.get("perf_runs", s.perf_runs_per_page)),
        vision_enabled=s.enable_vision_check and bool(cfg.get("vision", True)),
        should_cancel=should_cancel, log=log_fn,
    )


# ----------------------------------------------------------------------------- stages
async def stage_crawl(ctx: RunContext) -> dict[str, Any]:
    def on_page(p: Any) -> None:
        ctx.log("crawl", f"Crawled {ctx.path_of(p.url)} ({p.status_code or p.load_error})")

    res = await crawl(CrawlConfig(
        base_url=ctx.base_url, max_pages=ctx.max_pages, max_depth=ctx.max_depth, allow_local=ctx.allow_local,
        extra_domains=ctx.extra_domains, request_delay_ms=ctx.request_delay_ms, credentials=ctx.credentials,
        allow_destructive=ctx.allow_destructive, artifacts_dir=ctx.artifacts_dir), should_cancel=ctx.should_cancel,
        on_page=on_page)
    ctx.pages, ctx.login, ctx.storage_state = res.pages, res.login, res.storage_state_path
    persist_pages(ctx.run_id, res.pages, ctx.secrets)
    if not res.pages or all(p.load_error for p in res.pages):
        raise RuntimeError(f"Could not load {ctx.base_url}: "
                           f"{res.pages[0].load_error if res.pages else 'no pages crawled'}")
    return {"pages": len(res.pages), "login": {"attempted": res.login.attempted, "success": res.login.success,
                                               "message": res.login.message},
            "blocked_requests": len(res.blocked_requests)}


async def stage_plan(ctx: RunContext) -> dict[str, Any]:
    if not ctx.ok_pages:
        raise StageSkipped("no crawled pages")
    ctx.journeys = await planner.plan(ctx)
    with session_scope() as s:
        for j in ctx.journeys:
            row = Journey(run_id=ctx.run_id, key=j.key, name=j.name, description=j.description, priority=j.priority,
                          steps=j.steps, expected=j.expected, pages=j.pages, source=j.source)
            s.add(row)
            s.flush()
            j.db_id = row.id
        s.commit()
    return {"journeys": len(ctx.journeys), "llm_journeys": sum(1 for j in ctx.journeys if j.source == "llm")}


async def stage_generate(ctx: RunContext) -> dict[str, Any]:
    if not ctx.journeys:
        raise StageSkipped("no journeys planned")
    ctx.tests = await ui_generator.generate(ctx)
    with session_scope() as s:
        for t in ctx.tests:
            row = TestCase(run_id=ctx.run_id, journey_id=t.journey.db_id, journey_key=t.journey.key,
                           name=t.journey.name, script=t.script, script_hash=ui_generator.script_hash(t.script),
                           source=t.source, valid=t.valid, validation_error=t.validation_error, attempts=t.attempts)
            s.add(row)
            s.flush()
            t.db_id = row.id
        s.commit()
    by_source: dict[str, int] = {}
    for t in ctx.tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    return {"tests": len(ctx.tests), "by_source": by_source, "invalid": sum(1 for t in ctx.tests if not t.valid)}


def _artifact(ctx: RunContext, p: str | None) -> str | None:
    if not p:
        return None
    try:
        return str(Path(p).resolve().relative_to(ctx.artifacts_dir.resolve().parent))
    except ValueError:
        return None


async def stage_run(ctx: RunContext) -> dict[str, Any]:
    if not ctx.tests:
        raise StageSkipped("no tests generated")
    ctx.ui_results = await ui_runner.run_tests(ctx)
    with session_scope() as s:
        for tc, out in ctx.ui_results:
            s.add(TestResult(run_id=ctx.run_id, test_case_id=tc.db_id or 0, status=out.status, error=out.error,
                             error_kind=out.error_kind, duration_ms=out.duration_ms, attempts=out.attempts,
                             trace_path=_artifact(ctx, out.trace_path),
                             screenshot_path=_artifact(ctx, out.screenshot_path),
                             video_path=_artifact(ctx, out.video_path)))
            row = s.get(TestCase, tc.db_id)
            if row is not None:
                row.script, row.source, row.attempts = tc.script, tc.source, tc.attempts
                row.script_hash = ui_generator.script_hash(tc.script)
                s.add(row)
        s.commit()
    counts: dict[str, int] = {}
    for _, o in ctx.ui_results:
        counts[o.status] = counts.get(o.status, 0) + 1
    return {"results": counts}


async def stage_api(ctx: RunContext) -> dict[str, Any]:
    checks, mode = await run_api_checks(ctx)
    ctx.api_checks, ctx.api_mode = checks, mode
    with session_scope() as s:
        for c in checks:
            s.add(ApiCheckResult(run_id=ctx.run_id, method=c.method, url=mask_text(c.url, ctx.secrets), path=c.path,
                                 check=c.check, status=c.status, status_code=c.status_code, latency_ms=c.latency_ms,
                                 message=mask_text(c.message, ctx.secrets), details=c.details))
        s.commit()
    if mode == "none":
        raise StageSkipped("no OpenAPI spec and no API calls observed during the crawl")
    return {"mode": mode, "checks": len(checks), "failed": sum(1 for c in checks if c.status == "failed")}


async def stage_a11y(ctx: RunContext) -> dict[str, Any]:
    if not ctx.ok_pages:
        raise StageSkipped("no crawled pages")
    ctx.a11y_results = await run_a11y(ctx)
    with session_scope() as s:
        for r in ctx.a11y_results:
            for v in r.violations:
                s.add(A11yViolation(run_id=ctx.run_id, page_url=r.url, rule_id=v["rule_id"], impact=v["impact"],
                                    wcag_tags=v["wcag_tags"], description=v["description"], help=v["help"],
                                    help_url=v["help_url"], node_count=v["node_count"], nodes=v["nodes"]))
        s.commit()
    return {"pages": len(ctx.a11y_results), "violations": sum(len(r.violations) for r in ctx.a11y_results),
            "page_scores": {r.url: reporter.scoring.a11y_page_score([v["impact"] for v in r.violations])
                            for r in ctx.a11y_results}}


async def stage_perf(ctx: RunContext) -> dict[str, Any]:
    if not ctx.ok_pages:
        raise StageSkipped("no crawled pages")
    ctx.perf_results = await run_perf(ctx)
    with session_scope() as s:
        for p in ctx.perf_results:
            m = p.metrics
            s.add(PerfMetric(run_id=ctx.run_id, page_url=p.url, ttfb=m.get("ttfb"), fcp=m.get("fcp"),
                             lcp=m.get("lcp"), cls_score=m.get("cls"), dcl=m.get("dcl"), load=m.get("load"),
                             transfer_size=_int(m.get("transfer_size")), request_count=_int(m.get("request_count")),
                             ratings=p.ratings, samples=p.samples,
                             score=reporter.scoring.perf_page_score(p.ratings)))
        s.commit()
    return {"pages": len(ctx.perf_results), "runs_per_page": ctx.perf_runs}


def _int(v: float | None) -> int | None:
    return None if v is None else int(v)


async def stage_analyze(ctx: RunContext) -> dict[str, Any]:
    if ctx.vision_enabled:
        from app.stages.vision import run_vision

        ctx.vision_issues = await run_vision(ctx)
    issues = await analyst.analyze(ctx)
    ctx.issues = issues
    with session_scope() as s:
        ignored = {f.fingerprint for f in s.exec(select(Finding).where(
            Finding.project_id == ctx.project_id, Finding.status == "ignored")).all()}
        for i in issues:
            ev = {**i["evidence"], "kind": i["kind"]}
            for k in ("screenshot", "trace", "video"):
                if ev.get(k):
                    ev[k] = _artifact(ctx, ev[k]) or ev[k]
            s.add(Finding(run_id=ctx.run_id, project_id=ctx.project_id, fingerprint=i["fingerprint"],
                          category=i["category"], severity=i["severity"], classification=i["classification"],
                          title=i["title"][:300], description=i["description"], impact=i["impact"],
                          root_cause=i["root_cause"], repro_steps=i["repro_steps"],
                          suggested_fix=i["suggested_fix"], evidence=ev,
                          status="ignored" if i["fingerprint"] in ignored else "open",
                          ai_enriched=i["ai_enriched"], ai_unverified=i["ai_unverified"]))
        s.commit()
    return {"findings": len(issues), "ai_enriched": sum(1 for i in issues if i["ai_enriched"])}


async def stage_report(ctx: RunContext) -> dict[str, Any]:
    scores = reporter.compute_scores(ctx)
    summary, ai = await reporter.write_summary(ctx, scores, ctx.issues)
    fps = {i["fingerprint"] for i in ctx.issues if i["classification"] == "app_bug"}
    comparison = reporter.compare_with_previous(ctx.project_id, ctx.run_id, fps)
    with session_scope() as s:
        run = s.get(Run, ctx.run_id)
        assert run is not None
        run.score, run.ui_score, run.api_score = scores["overall"], scores["ui"], scores["api"]
        run.a11y_score, run.perf_score = scores["a11y"], scores["perf"]
        run.summary, run.summary_ai, run.comparison = mask_text(summary, ctx.secrets), ai, comparison
        s.add(run)
        s.commit()
    (ctx.artifacts_dir / "report.md").write_text(reporter.render_markdown(ctx.run_id), encoding="utf-8")
    (ctx.artifacts_dir / "report.html").write_text(reporter.render_html(ctx.run_id), encoding="utf-8")
    return {"scores": scores, "summary_ai": ai, "comparison": comparison}


PIPELINE: list[tuple[str, StageFn]] = [
    ("crawl", stage_crawl), ("plan", stage_plan), ("generate", stage_generate), ("run", stage_run),
    ("api", stage_api), ("a11y", stage_a11y), ("perf", stage_perf), ("analyze", stage_analyze),
    ("report", stage_report),
]


# ----------------------------------------------------------------------------- driver
def _set_stage(run_id: int, stage: str, **fields: Any) -> None:
    with session_scope() as s:
        row = s.exec(select(StageResult).where(StageResult.run_id == run_id, StageResult.stage == stage)).first()
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def _persist_usage(ctx: RunContext) -> None:
    u = ctx.llm.usage
    with session_scope() as s:
        run = s.get(Run, ctx.run_id)
        if run is not None:
            run.llm_calls, run.cache_hits = u.calls, u.cache_hits
            run.tokens_in, run.tokens_out = u.tokens_in, u.tokens_out
            s.add(run)
            s.commit()


def reset_stages(run_id: int, enabled: list[str]) -> None:
    with session_scope() as s:
        for row in s.exec(select(StageResult).where(StageResult.run_id == run_id)).all():
            s.delete(row)
        for n, (name, _) in enumerate(PIPELINE):
            s.add(StageResult(run_id=run_id, stage=name, order=n,
                              status="pending" if name in enabled else "skipped"))
        s.commit()


async def run_pipeline(run_id: int) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            return
        project = s.get(Project, run.project_id)
        if project is None:
            run.status, run.error = "failed", "project deleted"
            s.add(run)
            s.commit()
            return
        run.status, run.started_at, run.finished_at, run.error = "running", utcnow(), None, None
        run.llm_budget = int((run.config or {}).get("llm_budget", get_settings().max_llm_calls_per_run))
        s.add(run)
        s.commit()
        enabled = list((run.config or {}).get("stages") or [name for name, _ in PIPELINE])
        if "report" not in enabled:
            enabled.append("report")
    _cleanup_previous_attempt(run_id)
    reset_stages(run_id, enabled)
    ctx = build_context(run, project)
    events.emit(run_id, "run_status", "Run started", data={"status": "running"})
    cancelled = False
    try:
        for name, fn in PIPELINE:
            if name not in enabled:
                continue
            if ctx.should_cancel():
                cancelled = True
                _set_stage(run_id, name, status="cancelled")
                events.emit(run_id, "stage_status", f"{name} cancelled", stage=name, data={"status": "cancelled"})
                continue
            t0 = time.perf_counter()
            calls0 = ctx.llm.usage.calls
            _set_stage(run_id, name, status="running", started_at=utcnow())
            events.emit(run_id, "stage_status", f"{name} started", stage=name, data={"status": "running"})
            status, error, details = "passed", None, {}
            try:
                details = await fn(ctx)
                if name in ctx.degraded:
                    status, error = "degraded", ctx.degraded[name]
            except StageSkipped as exc:
                status, error = "skipped", str(exc)
            except Exception as exc:  # fault isolation: log, record and continue with next stage
                log.exception("stage %s failed", name)
                status = "failed"
                error = mask_text(f"{type(exc).__name__}: {exc}", ctx.secrets)[:1500]
                details = {"traceback": mask_text(traceback.format_exc(limit=4), ctx.secrets)[-3000:]}
            ms = int((time.perf_counter() - t0) * 1000)
            calls = ctx.llm.usage.calls - calls0
            _set_stage(run_id, name, status=status, finished_at=utcnow(), duration_ms=ms, llm_calls=calls,
                       error=error, details=details)
            _persist_usage(ctx)
            events.emit(run_id, "stage_status", f"{name} {status}" + (f": {error}" if error else ""), stage=name,
                        data={"status": status, "duration_ms": ms, "llm_calls": calls,
                              "llm_total": ctx.llm.usage.calls, "cache_hits": ctx.llm.usage.cache_hits})
    finally:
        if ctx.storage_state:  # session cookies: never keep them after the run
            shutil.rmtree(Path(ctx.storage_state).parent, ignore_errors=True)
        final = "cancelled" if cancelled or ctx.should_cancel() else "completed"
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is not None:
                run.status, run.finished_at = final, utcnow()
                s.add(run)
                s.commit()
        _persist_usage(ctx)
        events.emit(run_id, "run_status", f"Run {final}", data={"status": final})


def _cleanup_previous_attempt(run_id: int) -> None:
    """When a run is restarted after a crash, wipe its partial rows so it starts clean."""
    from app.db.models import Page, RunEvent

    with session_scope() as s:
        for model in (Finding, PerfMetric, A11yViolation, ApiCheckResult, TestResult, TestCase, Journey, Page):
            for row in s.exec(select(model).where(model.run_id == run_id)).all():  # type: ignore[attr-defined]
                s.delete(row)
        if s.exec(select(RunEvent).where(RunEvent.run_id == run_id, col(RunEvent.type) == "run_status")).first():
            s.add(RunEvent(run_id=run_id, type="log", message="Restarting run from scratch after an interruption"))
        s.commit()
