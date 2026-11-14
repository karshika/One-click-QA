"""REST + SSE routes."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.db.session import ping

router = APIRouter()


@router.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    s = get_settings()
    db_ok = False
    try:
        db_ok = ping()
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "db": db_ok,
            "llm_configured": s.has_llm_key}
