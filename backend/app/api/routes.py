"""REST + SSE routes. OpenAPI docs at /docs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlmodel import Session, col, select

from app import __version__
from app.config import get_settings
from app.core import events
from app.core.events import STAGES
from app.db.models import (
    A11yViolation,
    ApiCheckResult,
    Finding,
    Journey,
    LLMCall,
    Page,
    PerfMetric,
    Project,
    Run,
    RunEvent,
    ScriptCache,
    StageResult,
    TestCase,
    TestResult,
    utcnow,
)
from app.db.session import get_session, ping, session_scope
from app.security.secrets import decrypt_json, encrypt_json
from app.security.ssrf import SSRFError, check_url
from app.stages import reporter

router = APIRouter()
SessionDep = Depends(get_session)
STAGE_KEYS = [k for k, _ in STAGES]


# ----------------------------------------------------------------------------- schemas
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=2000)
    openapi_url: str | None = Field(default=None, max_length=2000)
    username: str | None = Field(default=None, max_length=300)
    password: str | None = Field(default=None, max_length=300)
    login_url: str | None = Field(default=None, max_length=2000)
    max_pages: int = Field(default=15, ge=1, le=50)
    max_depth: int = Field(default=3, ge=0, le=6)
    consent: bool = Field(description="User confirms they own/may test the site and that the free Mistral "
                                      "tier may use data for training (staging/demo apps only).")
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    openapi_url: str | None = None
    username: str | None = None
    password: str | None = None
    login_url: str | None = None
    max_pages: int | None = Field(default=None, ge=1, le=50)
    max_depth: int | None = Field(default=None, ge=0, le=6)
    settings: dict[str, Any] | None = None


class RunIn(BaseModel):
    max_pages: int | None = Field(default=None, ge=1, le=50)
    max_depth: int | None = Field(default=None, ge=0, le=6)
    stages: list[str] | None = None
    max_journeys: int | None = Field(default=None, ge=0, le=12)
    llm_budget: int | None = Field(default=None, ge=0, le=500)
    perf_runs: int | None = Field(default=None, ge=1, le=5)

    @field_validator("stages")
    @classmethod
    def _stages(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            bad = [s for s in v if s not in STAGE_KEYS]
            if bad:
                raise ValueError(f"unknown stages {bad}; valid: {STAGE_KEYS}")
        return v


class FindingPatch(BaseModel):
    status: Literal["open", "ignored", "fixed"]


# ----------------------------------------------------------------------------- helpers
def _validate_target(url: str, field: str) -> str:
    try:
        return check_url(url, allow_local=get_settings().allow_local_targets)
    except SSRFError as exc:
        raise HTTPException(400, f"{field}: {exc}") from exc


def _project_out(p: Project, s: Session) -> dict[str, Any]:
    auth = decrypt_json(p.auth_encrypted)
    last = s.exec(select(Run).where(Run.project_id == p.id).order_by(col(Run.id).desc())).first()
    return {
        "id": p.id, "name": p.name, "base_url": p.base_url, "openapi_url": p.openapi_url,
        "has_credentials": bool(auth.get("username")), "username": auth.get("username"),
        "login_url": auth.get("login_url"), "settings": p.settings,
        "consent_acknowledged_at": p.consent_acknowledged_at, "created_at": p.created_at,
        "last_run": _run_brief(last) if last else None,
    }


def _run_brief(r: Run) -> dict[str, Any]:
    return {"id": r.id, "status": r.status, "score": r.score, "created_at": r.created_at,
            "started_at": r.started_at, "finished_at": r.finished_at, "llm_calls": r.llm_calls,
            "cache_hits": r.cache_hits}


def _get(s: Session, model: Any, id_: int, what: str) -> Any:
    obj = s.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{what} not found")
    return obj


# ----------------------------------------------------------------------------- meta
@router.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    s = get_settings()
    try:
        db_ok = ping()
    except Exception:
        db_ok = False
    from app.core.worker import get_worker

    w = get_worker()
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "db": db_ok,
            "llm_configured": s.has_llm_key, "worker_running": bool(w._thread and w._thread.is_alive()),
            "current_run": w.current_run}


@router.get("/settings", tags=["meta"])
def public_settings(s: Session = SessionDep) -> dict[str, object]:
    from datetime import UTC, datetime

    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = s.exec(select(func.count()).select_from(LLMCall).where(
        col(LLMCall.created_at) >= start, col(LLMCall.cache_hit).is_(False),
        col(LLMCall.status).in_(["ok", "error"]))).one()
    return {**get_settings().public_view(), "llm_calls_today": int(used), "stages": STAGES}


# ----------------------------------------------------------------------------- projects
@router.post("/projects", status_code=201, tags=["projects"])
def create_project(body: ProjectIn, s: Session = SessionDep) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(400, "You must confirm that you own or may test this site and that the free Mistral "
                                 "tier may use submitted data for training (test staging/demo apps only).")
    base = _validate_target(body.base_url, "base_url")
    if body.openapi_url and body.openapi_url.startswith("http"):
        _validate_target(body.openapi_url, "openapi_url")
    auth = None
    if body.username or body.password:
        if not (body.username and body.password):
            raise HTTPException(400, "Provide both username and password, or neither")
        auth = encrypt_json({"username": body.username, "password": body.password, "login_url": body.login_url})
    p = Project(name=body.name.strip(), base_url=base, openapi_url=(body.openapi_url or None),
                auth_encrypted=auth, consent_acknowledged_at=utcnow(),
                settings={**body.settings, "max_pages": body.max_pages, "max_depth": body.max_depth})
    s.add(p)
    s.commit()
    s.refresh(p)
    return _project_out(p, s)


@router.get("/projects", tags=["projects"])
def list_projects(s: Session = SessionDep) -> list[dict[str, Any]]:
    return [_project_out(p, s) for p in s.exec(select(Project).order_by(col(Project.id).desc())).all()]


@router.get("/projects/{project_id}", tags=["projects"])
def get_project(project_id: int, s: Session = SessionDep) -> dict[str, Any]:
    return _project_out(_get(s, Project, project_id, "Project"), s)


@router.patch("/projects/{project_id}", tags=["projects"])
def patch_project(project_id: int, body: ProjectPatch, s: Session = SessionDep) -> dict[str, Any]:
    p: Project = _get(s, Project, project_id, "Project")
    if body.name is not None:
        p.name = body.name.strip()
    if body.base_url is not None:
        p.base_url = _validate_target(body.base_url, "base_url")
    if body.openapi_url is not None:
        if body.openapi_url.startswith("http"):
            _validate_target(body.openapi_url, "openapi_url")
        p.openapi_url = body.openapi_url or None
    if body.username is not None or body.password is not None or body.login_url is not None:
        auth = decrypt_json(p.auth_encrypted)
        for k in ("username", "password", "login_url"):
            v = getattr(body, k)
            if v is not None:
                auth[k] = v
        p.auth_encrypted = encrypt_json(auth) if auth.get("username") and auth.get("password") else None
    settings = dict(p.settings or {})
    if body.settings is not None:
        settings.update(body.settings)
    if body.max_pages is not None:
        settings["max_pages"] = body.max_pages
    if body.max_depth is not None:
        settings["max_depth"] = body.max_depth
    p.settings = settings
    s.add(p)
    s.commit()
    s.refresh(p)
    return _project_out(p, s)


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(project_id: int, s: Session = SessionDep) -> Response:
    p: Project = _get(s, Project, project_id, "Project")
    runs = s.exec(select(Run).where(Run.project_id == project_id)).all()
    if any(r.status in ("queued", "running") for r in runs):
        raise HTTPException(409, "Cancel the active run before deleting the project")
    run_ids = [r.id for r in runs]
    for model in (Finding, PerfMetric, A11yViolation, ApiCheckResult, TestResult, TestCase, Journey, Page,
                  StageResult, RunEvent, LLMCall):
        for row in s.exec(select(model).where(col(model.run_id).in_(run_ids))).all():  # type: ignore[attr-defined]
            s.delete(row)
    for row in s.exec(select(ScriptCache).where(ScriptCache.project_id == project_id)).all():
        s.delete(row)
    for r in runs:
        s.delete(r)
    s.delete(p)
    s.commit()
    import shutil

    for rid in run_ids:
        shutil.rmtree(get_settings().artifacts_dir / str(rid), ignore_errors=True)
    return Response(status_code=204)


# ----------------------------------------------------------------------------- runs
@router.post("/projects/{project_id}/runs", status_code=202, tags=["runs"])
def start_run(project_id: int, body: RunIn | None = None, s: Session = SessionDep) -> dict[str, Any]:
    p: Project = _get(s, Project, project_id, "Project")
    active = s.exec(select(Run).where(Run.project_id == project_id,
                                      col(Run.status).in_(["queued", "running"]))).first()
    if active:
        raise HTTPException(409, f"Run #{active.id} is already {active.status} for this project")
    b = body or RunIn()
    st = get_settings()
    ps = p.settings or {}
    config = {
        "max_pages": b.max_pages or ps.get("max_pages") or st.default_max_pages,
        "max_depth": b.max_depth if b.max_depth is not None else ps.get("max_depth", st.default_max_depth),
        "stages": b.stages or STAGE_KEYS,
        "max_journeys": b.max_journeys if b.max_journeys is not None else st.max_journeys,
        "llm_budget": b.llm_budget if b.llm_budget is not None else st.max_llm_calls_per_run,
        "perf_runs": b.perf_runs or st.perf_runs_per_page,
        "models": st.public_view()["models"], "llm_configured": st.has_llm_key,
        "allow_local_targets": st.allow_local_targets,
    }
    r = Run(project_id=project_id, status="queued", config=config, llm_budget=int(str(config["llm_budget"])))
    s.add(r)
    s.commit()
    s.refresh(r)
    events.emit(int(r.id or 0), "run_status", "Run queued", data={"status": "queued"})
    from app.core.worker import get_worker

    get_worker().notify()
    return _run_out(r, s)


def _run_out(r: Run, s: Session) -> dict[str, Any]:
    stages = s.exec(select(StageResult).where(StageResult.run_id == r.id).order_by(col(StageResult.order))).all()
    findings = s.exec(select(Finding).where(Finding.run_id == r.id)).all()
    counts: dict[str, dict[str, int]] = {"severity": {}, "category": {}, "classification": {}}
    for f in findings:
        if f.status != "open":
            continue
        for k in counts:
            v = getattr(f, k)
            counts[k][v] = counts[k].get(v, 0) + 1
    labels = dict(STAGES)
    return {
        **r.model_dump(exclude={"config"}), "config": r.config,
        "stages": [{"stage": st.stage, "label": labels.get(st.stage, st.stage), "status": st.status,
                    "duration_ms": st.duration_ms, "llm_calls": st.llm_calls, "error": st.error,
                    "started_at": st.started_at, "finished_at": st.finished_at,
                    "details": {k: v for k, v in (st.details or {}).items() if k != "traceback"}}
                   for st in stages] or [{"stage": k, "label": v, "status": "pending"} for k, v in STAGES],
        "finding_counts": counts, "open_findings": sum(1 for f in findings if f.status == "open"),
        "budget_remaining": max(0, (r.llm_budget or 0) - r.llm_calls),
    }


@router.get("/projects/{project_id}/runs", tags=["runs"])
def list_runs(project_id: int, s: Session = SessionDep) -> list[dict[str, Any]]:
    _get(s, Project, project_id, "Project")
    runs = s.exec(select(Run).where(Run.project_id == project_id).order_by(col(Run.id).desc()).limit(50)).all()
    return [{**_run_brief(r), "ui_score": r.ui_score, "api_score": r.api_score, "a11y_score": r.a11y_score,
             "perf_score": r.perf_score} for r in runs]


@router.get("/runs/{run_id}", tags=["runs"])
def get_run(run_id: int, s: Session = SessionDep) -> dict[str, Any]:
    return _run_out(_get(s, Run, run_id, "Run"), s)


@router.post("/runs/{run_id}/cancel", tags=["runs"])
def cancel_run(run_id: int, s: Session = SessionDep) -> dict[str, Any]:
    r: Run = _get(s, Run, run_id, "Run")
    if r.status not in ("queued", "running"):
        raise HTTPException(409, f"Run is already {r.status}")
    r.cancel_requested = True
    if r.status == "queued":
        r.status, r.finished_at = "cancelled", utcnow()
    s.add(r)
    s.commit()
    events.emit(run_id, "log", "Cancellation requested")
    if r.status == "cancelled":
        events.emit(run_id, "run_status", "Run cancelled", data={"status": "cancelled"})
    return {"id": run_id, "status": r.status, "cancel_requested": True}


@router.get("/runs/{run_id}/events", tags=["runs"])
async def run_events(run_id: int, request: Request) -> StreamingResponse:
    """Server-Sent Events: replays all events, then streams new ones until the run finishes."""
    with session_scope() as s:
        _get(s, Run, run_id, "Run")
    last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

    async def stream() -> AsyncIterator[str]:
        nonlocal last
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            rows = await run_in_threadpool(events.events_after, run_id, last)
            for e in rows:
                last = int(e.id or last)
                payload = {"id": e.id, "type": e.type, "stage": e.stage, "message": e.message, "data": e.data,
                           "ts": e.ts.isoformat()}
                yield f"id: {e.id}\nevent: {e.type}\ndata: {json.dumps(payload, default=str)}\n\n"
            if rows:
                idle = 0
                continue
            done = await run_in_threadpool(_run_finished, run_id)
            if done:
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_finished(run_id: int) -> bool:
    with session_scope() as s:
        r = s.get(Run, run_id)
        return r is None or r.status in ("completed", "failed", "cancelled")


# ----------------------------------------------------------------------------- run details (dashboard tabs)
@router.get("/runs/{run_id}/findings", tags=["findings"])
def list_findings(run_id: int, severity: str | None = None, category: str | None = None,
                  classification: str | None = None, status: str | None = None,
                  s: Session = SessionDep) -> list[Finding]:
    _get(s, Run, run_id, "Run")
    q = select(Finding).where(Finding.run_id == run_id)
    for field_name, val in (("severity", severity), ("category", category), ("classification", classification),
                            ("status", status)):
        if val:
            q = q.where(getattr(Finding, field_name) == val)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(s.exec(q).all(), key=lambda f: (order.get(f.severity, 9), f.category, f.id or 0))


@router.patch("/findings/{finding_id}", tags=["findings"])
def patch_finding(finding_id: int, body: FindingPatch, s: Session = SessionDep) -> Finding:
    f: Finding = _get(s, Finding, finding_id, "Finding")
    f.status = body.status
    s.add(f)
    s.commit()
    s.refresh(f)
    return f


@router.get("/runs/{run_id}/pages", tags=["runs"])
def run_pages(run_id: int, s: Session = SessionDep) -> list[dict[str, Any]]:
    rows = s.exec(select(Page).where(Page.run_id == run_id).order_by(col(Page.id))).all()
    return [{"id": p.id, "url": p.url, "title": p.title, "status_code": p.status_code, "depth": p.depth,
             "load_error": p.load_error, "console_errors": p.console_errors, "network_failures": p.network_failures,
             "api_calls": len(p.api_calls), "screenshot": _art(p.screenshot_path, run_id),
             "forms": len(p.forms), "links": len(p.links)} for p in rows]


def _art(path: str | None, run_id: int) -> str | None:
    if not path:
        return None
    pp = Path(path)
    if not pp.is_absolute():
        return f"/artifacts/{path.replace(chr(92), '/')}"
    try:
        rel = pp.resolve().relative_to(get_settings().artifacts_dir.resolve())
        return "/artifacts/" + rel.as_posix()
    except ValueError:
        return None


@router.get("/runs/{run_id}/tests", tags=["runs"])
def run_tests(run_id: int, s: Session = SessionDep) -> list[dict[str, Any]]:
    journeys = {j.id: j for j in s.exec(select(Journey).where(Journey.run_id == run_id)).all()}
    results = {r.test_case_id: r for r in s.exec(select(TestResult).where(TestResult.run_id == run_id)).all()}
    out = []
    planned_ids = set()
    for t in s.exec(select(TestCase).where(TestCase.run_id == run_id).order_by(col(TestCase.id))).all():
        j = journeys.get(t.journey_id)
        planned_ids.add(t.journey_id)
        r = results.get(t.id or -1)
        out.append({
            "id": t.id, "journey": {"name": j.name, "description": j.description, "steps": j.steps,
                                    "expected": j.expected, "priority": j.priority, "source": j.source} if j else None,
            "name": t.name, "script": t.script, "source": t.source, "valid": t.valid,
            "validation_error": t.validation_error, "attempts": t.attempts,
            "result": None if r is None else {
                "status": r.status, "error": r.error, "error_kind": r.error_kind, "duration_ms": r.duration_ms,
                "attempts": r.attempts, "screenshot": _art(r.screenshot_path, run_id),
                "trace": _art(r.trace_path, run_id), "video": _art(r.video_path, run_id)},
        })
    for jid, j in journeys.items():
        if jid not in planned_ids:
            out.append({"id": None, "journey": {"name": j.name, "description": j.description, "steps": j.steps,
                                                "expected": j.expected, "priority": j.priority, "source": j.source},
                        "name": j.name, "script": "", "source": "none", "valid": False,
                        "validation_error": "No test could be generated (LLM unavailable or budget exhausted)",
                        "attempts": [], "result": None})
    return out


@router.get("/runs/{run_id}/api-checks", tags=["runs"])
def run_api(run_id: int, s: Session = SessionDep) -> list[ApiCheckResult]:
    return list(s.exec(select(ApiCheckResult).where(ApiCheckResult.run_id == run_id)
                       .order_by(col(ApiCheckResult.id))).all())


@router.get("/runs/{run_id}/a11y", tags=["runs"])
def run_a11y_route(run_id: int, s: Session = SessionDep) -> list[A11yViolation]:
    return list(s.exec(select(A11yViolation).where(A11yViolation.run_id == run_id)).all())


@router.get("/runs/{run_id}/perf", tags=["runs"])
def run_perf_route(run_id: int, s: Session = SessionDep) -> list[PerfMetric]:
    return list(s.exec(select(PerfMetric).where(PerfMetric.run_id == run_id).order_by(col(PerfMetric.id))).all())


@router.get("/runs/{run_id}/llm-calls", tags=["runs"])
def run_llm_calls(run_id: int, s: Session = SessionDep) -> dict[str, Any]:
    r: Run = _get(s, Run, run_id, "Run")
    calls = s.exec(select(LLMCall).where(LLMCall.run_id == run_id).order_by(col(LLMCall.id))).all()
    by_stage: dict[str, dict[str, int]] = {}
    for c in calls:
        b = by_stage.setdefault(c.stage, {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0,
                                          "fallbacks": 0})
        if c.cache_hit:
            b["cache_hits"] += 1
        elif c.status in ("ok", "error"):
            b["calls"] += 1
            b["tokens_in"] += c.tokens_in
            b["tokens_out"] += c.tokens_out
        elif c.status in ("unavailable", "budget_exhausted"):
            b["fallbacks"] += 1
    return {"calls": calls, "by_stage": by_stage,
            "totals": {"calls": r.llm_calls, "cache_hits": r.cache_hits, "tokens_in": r.tokens_in,
                       "tokens_out": r.tokens_out, "budget": r.llm_budget,
                       "budget_remaining": max(0, (r.llm_budget or 0) - r.llm_calls)}}


@router.get("/runs/{run_id}/report", tags=["reports"])
async def run_report(run_id: int, format: Literal["md", "html", "pdf"] = Query("html")) -> Response:  # noqa: A002
    def _load() -> Run | None:
        with session_scope() as s:
            return s.get(Run, run_id)

    run = await run_in_threadpool(_load)
    if run is None:
        raise HTTPException(404, "Run not found")
    name = f"oneclickqa-run-{run_id}"
    if format == "md":
        text = await run_in_threadpool(reporter.render_markdown, run_id)
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    html = await run_in_threadpool(reporter.render_html, run_id)
    if format == "html":
        return HTMLResponse(html, headers={"Content-Disposition": f'inline; filename="{name}.html"'})
    pdf = await run_in_threadpool(reporter.render_pdf, html)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.get("/projects/{project_id}/trends", tags=["projects"])
def trends(project_id: int, s: Session = SessionDep) -> dict[str, Any]:
    _get(s, Project, project_id, "Project")
    runs = s.exec(select(Run).where(Run.project_id == project_id, Run.status == "completed")
                  .order_by(col(Run.id))).all()
    points = []
    for r in runs[-30:]:
        n = s.exec(select(func.count()).select_from(Finding).where(
            Finding.run_id == r.id, Finding.classification == "app_bug")).one()
        points.append({"run_id": r.id, "finished_at": r.finished_at, "score": r.score, "ui": r.ui_score,
                       "api": r.api_score, "a11y": r.a11y_score, "perf": r.perf_score, "bugs": int(n),
                       "llm_calls": r.llm_calls, "cache_hits": r.cache_hits})
    latest = runs[-1] if runs else None
    return {"points": points, "latest_comparison": latest.comparison if latest else None}


# ----------------------------------------------------------------------------- artifacts
@router.get("/artifacts/{run_id}/{path:path}", tags=["artifacts"], include_in_schema=False)
def artifact(run_id: int, path: str) -> FileResponse:
    root = (get_settings().artifacts_dir / str(run_id)).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or "auth" in target.relative_to(root).parts or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    media = {".png": "image/png", ".zip": "application/zip", ".webm": "video/webm", ".html": "text/html",
             ".md": "text/markdown"}.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media)
