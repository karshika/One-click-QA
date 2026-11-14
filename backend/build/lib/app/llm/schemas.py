"""Pydantic models for every structured LLM output (validated after JSON mode)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Classification = Literal["app_bug", "flaky", "test_error", "environment"]
Severity = Literal["critical", "high", "medium", "low"]
JourneyKind = Literal["login", "signup", "search", "form", "navigation", "cart", "checkout", "content", "other"]


class PlannedJourney(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    kind: JourneyKind = "other"
    description: str = Field(default="", max_length=600)
    priority: int = Field(default=3, ge=1, le=5, description="1 = most important")
    start_url: str
    pages: list[str] = Field(default_factory=list, max_length=10)
    steps: list[str] = Field(min_length=1, max_length=15)
    expected: list[str] = Field(min_length=1, max_length=10)
    negative: bool = Field(default=False, description="true if the journey checks that invalid input is rejected")

    @field_validator("steps", "expected", "pages")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [s.strip()[:300] for s in v if s and s.strip()]


class JourneyPlan(BaseModel):
    journeys: list[PlannedJourney] = Field(min_length=1, max_length=12)


class AnalyzedIssue(BaseModel):
    issue_id: str
    classification: Classification
    severity: Severity
    title: str = Field(min_length=3, max_length=160)
    impact: str = Field(default="", max_length=800)
    root_cause: str = Field(default="", max_length=800)
    repro_steps: list[str] = Field(default_factory=list, max_length=12)
    suggested_fix: str = Field(default="", max_length=800)
    duplicate_of: str | None = None


class AnalysisResult(BaseModel):
    issues: list[AnalyzedIssue]


class VisionIssue(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=600)
    severity: Severity = "low"


class VisionResult(BaseModel):
    issues: list[VisionIssue] = Field(default_factory=list, max_length=5)
