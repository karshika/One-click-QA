"""Acceptance run: OneClickQA vs. the ShopLite demo app (demo-app/EXPECTED_BUGS.md).

Runs the full pipeline twice (2nd run shows prompt/script cache reuse) and reports detected
vs planted bugs, false positives, LLM calls, cache hits and run time.

    .venv/Scripts/python scripts/acceptance.py                 # uses MISTRAL_API_KEY if set, else deterministic
    .venv/Scripts/python scripts/acceptance.py --mode mock     # recorded Mistral fixtures (no network)
    .venv/Scripts/python scripts/acceptance.py --mode deterministic
Writes docs/ACCEPTANCE.md (use --no-write to skip).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_demo() -> str:
    import httpx
    import uvicorn
    from demo_app.main import app as demo

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(demo, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/api/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "real", "mock", "deterministic"], default="auto")
    ap.add_argument("--demo-url", default=None, help="use an already running demo app")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("OCQA_DATA_DIR", tempfile.mkdtemp(prefix="ocqa-accept-"))
    os.environ["ALLOW_LOCAL_TARGETS"] = "true"
    os.environ["OCQA_START_WORKER"] = "false"
    mode = args.mode
    if mode == "auto":
        mode = "real" if os.environ.get("MISTRAL_API_KEY") else "deterministic"
    if mode != "real":
        os.environ["MISTRAL_API_KEY"] = ""

    from app.config import get_settings
    from app.core import acceptance, orchestrator
    from app.db.models import Project, Run, utcnow
    from app.db.session import init_engine, session_scope
    from app.security.secrets import encrypt_json

    get_settings.cache_clear()
    settings = get_settings()
    init_engine(settings.db_url)
    if mode == "mock":
        sys.path.insert(0, str(ROOT / "backend"))
        from app.llm.client import LLMClient
        from tests.fakes import MockMistral, fast_limiter

        provider = MockMistral()
        orchestrator.build_client = lambda *, run_id=None, secrets=None, run_budget=None: LLMClient(  # type: ignore
            provider, run_id=run_id, secrets=secrets, run_budget=run_budget, limiter=fast_limiter())

    demo_url = args.demo_url or start_demo()
    with session_scope() as s:
        p = Project(name="ShopLite acceptance", base_url=demo_url, openapi_url="/openapi.json",
                    consent_acknowledged_at=utcnow(),
                    auth_encrypted=encrypt_json({"username": "demo@example.com", "password": "Demo-Pass-2024"}))
        s.add(p)
        s.commit()
        pid = int(p.id or 0)

    results = []
    for i in range(args.runs):
        with session_scope() as s:
            r = Run(project_id=pid, status="queued", config={"llm_budget": settings.max_llm_calls_per_run},
                    llm_budget=settings.max_llm_calls_per_run)
            s.add(r)
            s.commit()
            rid = int(r.id or 0)
        print(f"[acceptance] run {i + 1}/{args.runs} (#{rid}) mode={mode} …", flush=True)
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # type: ignore[attr-defined]
        asyncio.run(orchestrator.run_pipeline(rid))
        res = acceptance.evaluate(rid)
        results.append(res)
        print(json.dumps({k: v for k, v in res.items() if k != "per_bug"}, indent=2))

    first = results[0]
    table = "\n".join(f"| {b['id']} | {b['title']} | {'yes' if b['detected'] else '**no**'} | "
                      f"{'; '.join(b['by'])[:90]} |" for b in first["per_bug"])
    runs_md = "\n".join(
        f"| #{r['run_id']} | {r['detected']}/{r['planted']} ({r['detection_rate']:.0%}) | {r['false_positives']} | "
        f"{r['llm_calls']} | {r['cache_hits']} | {r['duration_s']} s | {r['score']} | "
        f"{'PASS' if r['passed'] else 'FAIL'} |" for r in results)
    fps = "\n".join(f"- {f['title']} ({f['category']}, {f['severity']})" for f in first["false_positive_findings"]) \
        or "- none"
    non_app = "\n".join(f"- {f['title']} ({f['classification']})" for f in first["non_app_findings"]) or "- none"
    md = f"""# Acceptance results

Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `scripts/acceptance.py --mode {mode}`.
Mode: **{mode}** ({ {'real': 'real Mistral API', 'mock': 'recorded Mistral fixtures, no network',
                    'deterministic': 'no LLM: deterministic fallbacks only'}[mode] }).
Target: ShopLite demo app (`demo-app/`, {first['planted']} planted bugs, see `demo-app/EXPECTED_BUGS.md`).
Criteria: detect ≥ 80% of the planted bugs with ≤ 2 false positives, within free-tier limits
(≤ {settings.max_llm_calls_per_run} LLM calls per run at ≤ {settings.mistral_rps} request/s).

## Runs

| Run | Detected | False positives | LLM calls | Cache hits | Run time | Score | Result |
|---|---|---|---|---|---|---|---|
{runs_md}

## Planted bugs (first run)

| ID | Bug | Detected | By finding |
|---|---|---|---|
{table}

## False positives (first run)

{fps}

## Non-app findings (test/flaky/environment, not counted as bug claims)

{non_app}
"""
    if not args.no_write:
        out = ROOT / "docs" / (f"ACCEPTANCE-{mode}.md" if mode != "real" else "ACCEPTANCE.md")
        out.write_text(md, encoding="utf-8")
        print(f"[acceptance] wrote {out}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
