"""In-process job worker. The DB is the queue (Run.status == 'queued'), so queued and
interrupted runs survive restarts. A dedicated thread owns its own asyncio loop
(ProactorEventLoop on Windows so Playwright/subprocesses work regardless of the server loop).
Runs execute one at a time, which also guarantees one run at a time per project.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

from sqlmodel import col, select

from app.core import events
from app.db.models import Run
from app.db.session import session_scope

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, poll_interval: float = 1.0) -> None:
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.current_run: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(target=self._main, name="ocqa-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    @staticmethod
    def recover() -> None:
        """Runs that were 'running' when the process died are re-queued."""
        with session_scope() as s:
            for r in s.exec(select(Run).where(Run.status == "running")).all():
                r.status = "queued"
                s.add(r)
                log.warning("re-queued interrupted run %s", r.id)
            s.commit()

    def _next_run(self) -> int | None:
        with session_scope() as s:
            r = s.exec(select(Run).where(Run.status == "queued").order_by(col(Run.id))).first()
            if r is None:
                return None
            if r.cancel_requested:
                r.status = "cancelled"
                s.add(r)
                s.commit()
                events.emit(int(r.id or 0), "run_status", "Run cancelled before start", data={"status": "cancelled"})
                return None
            return int(r.id or 0)

    def _main(self) -> None:
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()  # type: ignore[attr-defined]
        asyncio.set_event_loop(loop)
        from app.core.orchestrator import run_pipeline

        while not self._stop.is_set():
            try:
                run_id = self._next_run()
            except Exception:
                log.exception("worker poll failed")
                run_id = None
            if run_id is None:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            self.current_run = run_id
            try:
                loop.run_until_complete(run_pipeline(run_id))
            except Exception as exc:
                log.exception("run %s crashed", run_id)
                with session_scope() as s:
                    r = s.get(Run, run_id)
                    if r is not None:
                        r.status, r.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
                        s.add(r)
                        s.commit()
                events.emit(run_id, "run_status", "Run failed", data={"status": "failed"})
            finally:
                self.current_run = None
        loop.close()


_worker: Worker | None = None


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker
