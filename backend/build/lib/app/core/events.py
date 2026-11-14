"""Progress events. Events are persisted (RunEvent table) so the SSE endpoint can replay
them on reconnect and they survive restarts; the SSE route polls for new rows."""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import col, select

from app.db.models import RunEvent
from app.db.session import session_scope

log = logging.getLogger(__name__)

STAGES: list[tuple[str, str]] = [
    ("crawl", "Crawl"),
    ("plan", "Plan"),
    ("generate", "Generate"),
    ("run", "Run"),
    ("api", "API"),
    ("a11y", "A11y"),
    ("perf", "Perf"),
    ("analyze", "Analyze"),
    ("report", "Report"),
]
STAGE_KEYS = [k for k, _ in STAGES]


def emit(run_id: int, type_: str, message: str = "", *, stage: str | None = None,
         data: dict[str, Any] | None = None) -> None:
    try:
        with session_scope() as s:
            s.add(RunEvent(run_id=run_id, type=type_, stage=stage, message=message[:2000], data=data or {}))
            s.commit()
    except Exception:  # events must never break the pipeline
        log.exception("failed to persist event")


def events_after(run_id: int, after_id: int, limit: int = 200) -> list[RunEvent]:
    with session_scope() as s:
        stmt = (
            select(RunEvent)
            .where(RunEvent.run_id == run_id, col(RunEvent.id) > after_id)
            .order_by(col(RunEvent.id))
            .limit(limit)
        )
        return list(s.exec(stmt).all())
