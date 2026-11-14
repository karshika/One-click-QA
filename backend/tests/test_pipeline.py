"""Full pipeline against the demo app: (1) deterministic via REST API + worker + SSE,
(2) with a mocked Mistral (record/replay), including a zero-call re-run from cache."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core import acceptance
from app.core.orchestrator import run_pipeline
from app.core.worker import get_worker
from app.db.models import Run
from app.db.session import session_scope
from app.llm import client as llm_client
from app.main import create_app
from tests.fakes import MockMistral, fast_limiter

pytestmark = pytest.mark.browser

CREDS = {"username": "demo@example.com", "password": "Demo-Pass-2024"}


@pytest.fixture(scope="module")
def api() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _project(api: TestClient, demo_url: str, name: str) -> int:
    r = api.post("/projects", json={"name": name, "base_url": demo_url, "openapi_url": "/openapi.json",
                                    "consent": True, "max_pages": 15, **CREDS})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_credentials"] and "password" not in json.dumps(body).lower().replace("has_credentials", "")
    return int(body["id"])


def test_project_validation(api: TestClient, demo_url: str) -> None:
    r = api.post("/projects", json={"name": "x", "base_url": demo_url, "consent": False})
    assert r.status_code == 400 and "confirm" in r.text
    r = api.post("/projects", json={"name": "x", "base_url": "file:///etc/passwd", "consent": True})
    assert r.status_code == 400
    r = api.post("/projects", json={"name": "x", "base_url": demo_url, "consent": True, "username": "only"})
    assert r.status_code == 400
    assert api.get("/projects/99999").status_code == 404
    assert api.get("/artifacts/1/../../oneclickqa.db").status_code == 404


def _wait(api: TestClient, run_id: int, timeout: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = api.get(f"/runs/{run_id}").json()
        if r["status"] in ("completed", "failed", "cancelled"):
            return r
        time.sleep(1)
    raise AssertionError("run did not finish")


def test_deterministic_run_via_api_worker_and_sse(api: TestClient, demo_url: str) -> None:
    pid = _project(api, demo_url, "ShopLite (deterministic)")
    worker = get_worker()
    worker.start()
    try:
        r = api.post(f"/projects/{pid}/runs", json={"perf_runs": 1})
        assert r.status_code == 202, r.text
        run_id = r.json()["id"]
        assert api.post(f"/projects/{pid}/runs", json={}).status_code == 409  # one active run per project
        run = _wait(api, run_id)
    finally:
        worker.stop()
    assert run["status"] == "completed", run
    stages = {s["stage"]: s for s in run["stages"]}
    assert stages["crawl"]["status"] == "passed"
    assert stages["plan"]["status"] == "degraded"  # no key → deterministic fallback
    for k in ("generate", "run", "api", "a11y", "perf", "report"):
        assert stages[k]["status"] in ("passed", "degraded"), (k, stages[k])
    assert run["llm_calls"] == 0 and run["score"] is not None

    # SSE replays the whole history and ends
    with api.stream("GET", f"/runs/{run_id}/events") as resp:
        body = "".join(resp.iter_text())
    assert "event: stage_status" in body and "event: end" in body and '"status": "completed"' in body

    res = acceptance.evaluate(run_id)
    print(json.dumps(res, indent=2))
    assert res["detection_rate"] >= 0.8, res["per_bug"]
    assert res["false_positives"] <= 2, res["false_positive_findings"]

    findings = api.get(f"/runs/{run_id}/findings").json()
    f0 = findings[0]
    assert api.patch(f"/findings/{f0['id']}", json={"status": "ignored"}).json()["status"] == "ignored"
    assert len(api.get(f"/runs/{run_id}/findings", params={"category": "a11y"}).json()) >= 3
    md = api.get(f"/runs/{run_id}/report", params={"format": "md"})
    assert md.status_code == 200 and "# OneClickQA report" in md.text and CREDS["password"] not in md.text
    html = api.get(f"/runs/{run_id}/report", params={"format": "html"})
    assert html.status_code == 200 and "<html" in html.text
    pdf = api.get(f"/runs/{run_id}/report", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    tests = api.get(f"/runs/{run_id}/tests").json()
    assert any(t["result"] and t["result"]["status"] == "failed" for t in tests)
    failed = next(t for t in tests if t["result"] and t["result"]["status"] == "failed")
    assert api.get(failed["result"]["screenshot"]).status_code == 200
    assert api.get(f"/projects/{pid}/trends").json()["points"]
    for path in ("pages", "api-checks", "a11y", "perf", "llm-calls"):
        assert api.get(f"/runs/{run_id}/{path}").status_code == 200


async def test_mocked_llm_pipeline_and_zero_call_rerun(api: TestClient, demo_url: str,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MockMistral()

    def fake_build(*, run_id=None, secrets=None, run_budget=None):  # type: ignore[no-untyped-def]
        return llm_client.LLMClient(provider, run_id=run_id, secrets=secrets, run_budget=run_budget,
                                    limiter=fast_limiter())

    monkeypatch.setattr("app.core.orchestrator.build_client", fake_build)
    pid = _project(api, demo_url, "ShopLite (mocked LLM)")

    def new_run() -> int:
        with session_scope() as s:
            r = Run(project_id=pid, status="queued", config={"perf_runs": 1, "llm_budget": 60}, llm_budget=60)
            s.add(r)
            s.commit()
            return int(r.id or 0)

    run1 = new_run()
    await run_pipeline(run1)
    r1 = api.get(f"/runs/{run1}").json()
    assert r1["status"] == "completed"
    assert 0 < r1["llm_calls"] <= 60
    assert r1["summary_ai"] is True
    tests = api.get(f"/runs/{run1}/tests").json()
    names = {t["name"]: t for t in tests}
    assert "Exfiltrate data to another site" not in names  # scope enforcement dropped it
    assert names["Open the monthly report from the navigation"]["source"] == "repaired"
    assert names["Open the monthly report from the navigation"]["result"]["status"] == "passed"
    assert names["Browse products and open a product"]["result"]["status"] == "passed"
    res1 = acceptance.evaluate(run1)
    print(json.dumps(res1, indent=2))
    assert res1["passed"], res1
    calls1 = len(provider.requests)

    run2 = new_run()
    await run_pipeline(run2)
    r2 = api.get(f"/runs/{run2}").json()
    assert r2["status"] == "completed"
    assert r2["llm_calls"] == 0, r2  # everything served from prompt cache + script cache
    assert r2["cache_hits"] > 0
    assert len(provider.requests) == calls1
    tests2 = api.get(f"/runs/{run2}/tests").json()
    assert any(t["source"] == "cached" for t in tests2)
    assert r2["comparison"]["previous_run_id"] == run1 and r2["comparison"]["new"] == 0
    res2 = acceptance.evaluate(run2)
    assert res2["passed"], res2
