"""Database models (SQLModel). Schema is SQLite-default but Postgres-compatible:
only portable column types (Integer, Float, String/Text, Boolean, DateTime, JSON).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def _json(default: Any = None) -> Any:
    factory = (lambda: {}) if default is None else default
    return Field(default_factory=factory, sa_column=Column(JSON))


def _text() -> Any:
    return Field(default=None, sa_column=Column(Text))


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    base_url: str
    openapi_url: str | None = None
    auth_encrypted: str | None = Field(default=None, sa_column=Column(Text))  # Fernet token
    settings: dict[str, Any] = _json()
    consent_acknowledged_at: datetime
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    status: str = "queued"  # queued|running|completed|failed|cancelled
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    score: float | None = None
    ui_score: float | None = None
    api_score: float | None = None
    a11y_score: float | None = None
    perf_score: float | None = None
    llm_calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_budget: int = 0
    config: dict[str, Any] = _json()
    summary: str | None = _text()
    summary_ai: bool = False
    comparison: dict[str, Any] = _json()
    cancel_requested: bool = False
    error: str | None = _text()


class StageResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    stage: str
    order: int = 0
    status: str = "pending"  # pending|running|passed|degraded|failed|skipped|cancelled
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    llm_calls: int = 0
    error: str | None = _text()
    details: dict[str, Any] = _json()


class RunEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    ts: datetime = Field(default_factory=utcnow)
    type: str  # run_status|stage_status|log|progress
    stage: str | None = None
    message: str = ""
    data: dict[str, Any] = _json()


class Page(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    url: str
    title: str = ""
    status_code: int | None = None
    depth: int = 0
    load_error: str | None = _text()
    dom_summary: dict[str, Any] = _json()
    a11y_tree: str | None = _text()
    screenshot_path: str | None = None
    console_errors: list[str] = _json(list)
    network_failures: list[dict[str, Any]] = _json(list)
    api_calls: list[dict[str, Any]] = _json(list)
    links: list[str] = _json(list)
    forms: list[dict[str, Any]] = _json(list)


class Journey(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    key: str
    name: str
    description: str = ""
    priority: int = 3
    steps: list[str] = _json(list)
    expected: list[str] = _json(list)
    pages: list[str] = _json(list)
    source: str = "llm"  # llm|template


class TestCase(SQLModel, table=True):
    __test__ = False  # not a pytest class
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    journey_id: int | None = Field(default=None, foreign_key="journey.id")
    journey_key: str = ""
    name: str = ""
    script: str = Field(default="", sa_column=Column(Text))
    script_hash: str = ""
    source: str = "generated"  # generated|repaired|cached|template
    valid: bool = True
    validation_error: str | None = _text()
    attempts: list[dict[str, Any]] = _json(list)


class TestResult(SQLModel, table=True):
    __test__ = False
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    test_case_id: int = Field(foreign_key="testcase.id")
    status: str  # passed|failed|flaky|error|skipped
    error: str | None = _text()
    error_kind: str | None = None  # assertion|script|timeout|environment|validation
    duration_ms: int = 0
    attempts: int = 1
    trace_path: str | None = None
    screenshot_path: str | None = None
    video_path: str | None = None
    log: str | None = _text()


class ScriptCache(SQLModel, table=True):
    """Last known-good (passing) script per project+journey, reused with zero LLM calls."""

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    journey_key: str = Field(index=True)
    script: str = Field(default="", sa_column=Column(Text))
    script_hash: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class ApiCheckResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    method: str
    url: str
    path: str
    check: str
    status: str  # passed|failed|warning
    status_code: int | None = None
    latency_ms: float | None = None
    message: str = Field(default="", sa_column=Column(Text))
    details: dict[str, Any] = _json()


class A11yViolation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    page_url: str
    rule_id: str
    impact: str | None = None
    wcag_tags: list[str] = _json(list)
    description: str = Field(default="", sa_column=Column(Text))
    help: str = ""
    help_url: str = ""
    node_count: int = 0
    nodes: list[dict[str, Any]] = _json(list)


class PerfMetric(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    page_url: str
    ttfb: float | None = None
    fcp: float | None = None
    lcp: float | None = None
    cls: float | None = None
    dcl: float | None = None
    load: float | None = None
    transfer_size: int | None = None
    request_count: int | None = None
    ratings: dict[str, str] = _json()
    samples: list[dict[str, Any]] = _json(list)
    score: float | None = None


class Finding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    fingerprint: str = Field(index=True)
    category: str  # ui|api|a11y|perf
    severity: str  # critical|high|medium|low
    classification: str  # app_bug|flaky|test_error|environment
    title: str
    description: str = Field(default="", sa_column=Column(Text))
    impact: str = Field(default="", sa_column=Column(Text))
    root_cause: str = Field(default="", sa_column=Column(Text))
    repro_steps: list[str] = _json(list)
    suggested_fix: str = Field(default="", sa_column=Column(Text))
    evidence: dict[str, Any] = _json()
    status: str = "open"  # open|ignored|fixed
    ai_enriched: bool = False
    ai_unverified: bool = False  # vision findings: "AI-suggested, unverified"


class LLMCall(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = Field(default=None, index=True)
    stage: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cache_hit: bool = False
    status: str = "ok"  # ok|error|invalid|budget_exhausted|unavailable
    error: str | None = _text()
    attempts: int = 1
    created_at: datetime = Field(default_factory=utcnow, index=True)


class LLMCache(SQLModel, table=True):
    key: str = Field(primary_key=True)
    model: str = ""
    response_text: str = Field(default="", sa_column=Column(Text))
    tokens_in: int = 0
    tokens_out: int = 0
    hits: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class SchemaVersion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    version: int
    applied_at: datetime = Field(default_factory=utcnow)
