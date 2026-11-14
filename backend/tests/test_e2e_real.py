"""Real end-to-end run against the demo app with a REAL Mistral key.

Skipped unless MISTRAL_API_KEY is set AND OCQA_E2E=1 (so CI never calls the real API):
    OCQA_E2E=1 MISTRAL_API_KEY=... pytest -m e2e -s
"""

from __future__ import annotations

import json
import os

import pytest

from app.config import get_settings
from app.core import acceptance
from app.core.orchestrator import run_pipeline
from app.db.models import Project, Run, utcnow
from app.db.session import session_scope
from app.security.secrets import encrypt_json

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (os.environ.get("OCQA_E2E") and os.environ.get("MISTRAL_API_KEY")),
                       reason="needs OCQA_E2E=1 and a real MISTRAL_API_KEY"),
]


async def test_real_pipeline_against_demo_app(demo_url: str) -> None:
    get_settings.cache_clear()
    s = get_settings()
    assert s.has_llm_key
    with session_scope() as sess:
        p = Project(name="ShopLite e2e", base_url=demo_url, openapi_url="/openapi.json",
                    consent_acknowledged_at=utcnow(),
                    auth_encrypted=encrypt_json({"username": "demo@example.com", "password": "Demo-Pass-2024"}))
        sess.add(p)
        sess.commit()
        r = Run(project_id=p.id or 0, status="queued", config={"llm_budget": s.max_llm_calls_per_run})
        sess.add(r)
        sess.commit()
        rid = int(r.id or 0)
    await run_pipeline(rid)
    res = acceptance.evaluate(rid)
    print(json.dumps(res, indent=2))
    assert res["llm_calls"] <= s.max_llm_calls_per_run
    assert res["detection_rate"] >= 0.8 and res["false_positives"] <= 2
