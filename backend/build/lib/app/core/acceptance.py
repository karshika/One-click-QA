"""Scores a run against demo-app/expected_bugs.json (planted bugs).

A finding counts as a bug claim when classification == app_bug and it is not an unverified
AI suggestion. Each claim is matched against the planted bugs; claims matching none are
false positives. Test/environment/flaky items are reported separately (they are not claims
about the app).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from app.db.models import Finding, Run
from app.db.session import session_scope

EXPECTED = Path(__file__).resolve().parents[3] / "demo-app" / "expected_bugs.json"


def _haystack(f: Finding) -> str:
    ev = f.evidence or {}
    parts = [f.title, f.description, str(ev.get("url", "")), str(ev.get("path", "")),
             " ".join(map(str, ev.get("pages", []) or [])), str(ev.get("start_url", "")), str(ev.get("journey", ""))]
    return " ".join(parts).lower()


def matches(bug: dict[str, Any], f: Finding) -> bool:
    ev = f.evidence or {}
    if f.category != bug["category"]:
        return False
    hay = _haystack(f)
    if "rule" in bug and ev.get("rule_id") != bug["rule"]:
        return False
    if bug.get("url_contains") and bug["url_contains"].lower() not in hay:
        return False
    if "metric" in bug and (ev.get("ratings", {}) or {}).get(bug["metric"], "good") == "good":
        return False
    if "check_any" in bug and ev.get("check") not in bug["check_any"]:
        return False
    if "kind" in bug and ev.get("kind") != bug["kind"]:
        return False
    if "any_text" in bug:
        if ev.get("kind") != "ui_test_failure":
            return False
        if not any(w in hay for w in bug["any_text"]):
            return False
    return True


def evaluate(run_id: int, expected_path: Path = EXPECTED) -> dict[str, Any]:
    bugs = json.loads(expected_path.read_text(encoding="utf-8"))["bugs"]
    with session_scope() as s:
        run = s.get(Run, run_id)
        findings = s.exec(select(Finding).where(Finding.run_id == run_id)).all()
    claims = [f for f in findings if f.classification == "app_bug" and not f.ai_unverified]
    others = [f for f in findings if f not in claims]
    detected: dict[str, list[str]] = {}
    false_pos = []
    for f in claims:
        hit = [b["id"] for b in bugs if matches(b, f)]
        for h in hit:
            detected.setdefault(h, []).append(f.title)
        if not hit:
            false_pos.append({"title": f.title, "category": f.category, "severity": f.severity})
    rate = len(detected) / len(bugs) if bugs else 0.0
    return {
        "run_id": run_id,
        "planted": len(bugs),
        "detected": len(detected),
        "detection_rate": round(rate, 3),
        "false_positives": len(false_pos),
        "false_positive_findings": false_pos,
        "non_app_findings": [{"title": f.title, "classification": f.classification} for f in others],
        "per_bug": [{"id": b["id"], "title": b["title"], "detected": b["id"] in detected,
                     "by": detected.get(b["id"], [])} for b in bugs],
        "llm_calls": run.llm_calls if run else None,
        "cache_hits": run.cache_hits if run else None,
        "score": run.score if run else None,
        "duration_s": (round((run.finished_at - run.started_at).total_seconds(), 1)
                       if run and run.finished_at and run.started_at else None),
        "passed": rate >= 0.8 and len(false_pos) <= 2,
    }
