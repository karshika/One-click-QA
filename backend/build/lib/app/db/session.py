"""Engine/session management and a tiny forward-only migration system."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401  (register tables)
from app.db.models import SchemaVersion

log = logging.getLogger(__name__)

_engine: Engine | None = None

# Forward-only migrations. Version 1 = create_all of the initial schema. Append new
# callables for later changes (e.g. ALTER TABLE ... ADD COLUMN) – they run exactly once.
MIGRATIONS: list[Callable[[Engine], None]] = [
    lambda engine: SQLModel.metadata.create_all(engine),
]


def init_engine(url: str) -> Engine:
    global _engine
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    _engine = eng
    migrate(eng)
    return eng


def migrate(engine: Engine) -> int:
    SchemaVersion.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    with Session(engine) as s:
        rows = s.exec(select(SchemaVersion.version)).all()
        current = max(rows) if rows else 0
    for idx, fn in enumerate(MIGRATIONS, start=1):
        if idx > current:
            log.info("Applying DB migration %s", idx)
            fn(engine)
            with Session(engine) as s:
                s.add(SchemaVersion(version=idx))
                s.commit()
    return len(MIGRATIONS)


def get_engine() -> Engine:
    if _engine is None:
        from app.config import get_settings

        return init_engine(get_settings().db_url)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


def ping() -> bool:
    with get_engine().connect() as c:
        c.execute(text("SELECT 1"))
    return True
