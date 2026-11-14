"""State shared between pipeline stages during one run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.llm.client import LLMClient
from app.stages.crawler import Credentials, LoginOutcome, PageData


@dataclass
class JourneyData:
    key: str
    name: str
    kind: str
    description: str
    priority: int
    start_url: str  # path
    pages: list[str]
    steps: list[str]
    expected: list[str]
    negative: bool = False
    source: str = "llm"  # llm|template
    db_id: int | None = None


@dataclass
class TestCaseData:
    __test__ = False
    journey: JourneyData
    script: str
    source: str  # generated|repaired|cached|template
    valid: bool = True
    validation_error: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    db_id: int | None = None


@dataclass
class RunContext:
    run_id: int
    project_id: int
    base_url: str
    llm: LLMClient
    artifacts_dir: Path
    openapi_url: str | None = None
    credentials: Credentials | None = None
    secrets: list[str] = field(default_factory=list)
    allow_local: bool = False
    extra_domains: list[str] = field(default_factory=list)
    max_pages: int = 15
    max_depth: int = 3
    request_delay_ms: int = 250
    max_journeys: int = 6
    allow_destructive: bool = False
    allowed_api_methods: list[str] = field(default_factory=lambda: ["GET", "HEAD", "OPTIONS"])
    test_timeout_s: int = 60
    perf_runs: int = 3
    vision_enabled: bool = False
    should_cancel: Callable[[], bool] = lambda: False
    log: Callable[[str, str], None] = lambda stage, msg: None
    # stage outputs
    pages: list[PageData] = field(default_factory=list)
    login: LoginOutcome = field(default_factory=LoginOutcome)
    storage_state: str | None = None
    journeys: list[JourneyData] = field(default_factory=list)
    tests: list[TestCaseData] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)  # raw issues for the analyst
    degraded: dict[str, str] = field(default_factory=dict)  # stage → reason it fell back

    def path_of(self, url: str) -> str:
        from urllib.parse import urlsplit

        p = urlsplit(url)
        return (p.path or "/") + (f"?{p.query}" if p.query else "")

    @property
    def ok_pages(self) -> list[PageData]:
        return [p for p in self.pages if not p.load_error and (p.status_code or 0) < 400]
