"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import routes
from app.config import get_settings
from app.db.session import init_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oneclickqa")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_engine(settings.db_url)
    worker = None
    if settings.start_worker:
        from app.core.worker import get_worker

        worker = get_worker()
        worker.start()
    log.info("OneClickQA %s started (LLM configured: %s)", __version__, settings.has_llm_key)
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OneClickQA API",
        version=__version__,
        description="One-click QA: crawl, plan, generate & run tests, API/a11y/perf checks, AI bug reports.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    return app


app = create_app()
