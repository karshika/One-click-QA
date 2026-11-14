"""Cancellation, restart recovery and stage fault isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core import orchestrator
from app.core.worker import Worker
from app.db.models import Project, Run, StageResult, utcnow
from app.db.session import session_scope
from app.main import create_app


def _project() -> int:
    with session_scope() as s:
        p = Project(name="p", base_url="http://127.0.0.1:9/", consent_acknowledged_at=utcnow())
        s.add(p)
        s.commit()
        return int(p.id or 0)


def test_cancel_queued_run_via_api() -> None:
    pid = _project()
    with TestClient(create_app()) as c:
        run = c.post(f"/projects/{pid}/runs", json={"stages": ["crawl"]}).json()
        assert run["status"] == "queued"
        r = c.post(f"/runs/{run['id']}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert c.post(f"/runs/{run['id']}/cancel").status_code == 409
        assert c.post(f"/projects/{pid}/runs", json={"stages": ["bogus"]}).status_code == 422


def test_restart_requeues_interrupted_runs() -> None:
    pid = _project()
    with session_scope() as s:
        r = Run(project_id=pid, status="running")
        s.add(r)
        s.commit()
        rid = r.id
    Worker.recover()
    with session_scope() as s:
        assert s.get(Run, rid).status == "queued"  # type: ignore[union-attr]
        s.get(Run, rid).status = "cancelled"  # type: ignore[union-attr]
        s.commit()


async def test_stage_failure_is_isolated_and_cancel_mid_run(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _project()

    async def boom(ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("crawler exploded")

    async def ok(ctx):  # type: ignore[no-untyped-def]
        return {"ok": True}

    async def cancel_after(ctx):  # type: ignore[no-untyped-def]
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            run.cancel_requested = True  # type: ignore[union-attr]
            s.commit()
        return {}

    pipeline = [("crawl", boom), ("plan", ok), ("generate", cancel_after), ("run", ok), ("report", ok)]
    monkeypatch.setattr(orchestrator, "PIPELINE", pipeline)
    with session_scope() as s:
        r = Run(project_id=pid, status="queued", config={})
        s.add(r)
        s.commit()
        rid = int(r.id or 0)
    await orchestrator.run_pipeline(rid)
    with session_scope() as s:
        stages = {x.stage: x for x in s.exec(select(StageResult).where(StageResult.run_id == rid)).all()}
        run = s.get(Run, rid)
    assert stages["crawl"].status == "failed" and "crawler exploded" in (stages["crawl"].error or "")
    assert stages["plan"].status == "passed"  # later stages still ran
    assert stages["generate"].status == "passed"
    assert stages["run"].status == "cancelled" and stages["report"].status == "cancelled"
    assert run is not None and run.status == "cancelled"
