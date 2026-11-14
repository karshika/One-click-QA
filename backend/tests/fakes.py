"""Test doubles for the LLM provider (record/replay style)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.llm.limiter import RateLimiter
from app.llm.provider import LLMProvider, LLMRequest, LLMResponse

FIXTURES = Path(__file__).parent / "fixtures" / "mistral"


class ScriptedProvider(LLMProvider):
    """Returns queued responses (or raises queued exceptions) in order."""

    name = "scripted"

    def __init__(self, items: list[str | Exception] | None = None,
                 responder: Callable[[LLMRequest], str] | None = None) -> None:
        self.items = list(items or [])
        self.responder = responder
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.responder is not None:
            return LLMResponse(self.responder(request), 100, 50, request.model)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(item, 100, 50, request.model)


class ReplayProvider(LLMProvider):
    """Replays recorded Mistral answers from tests/fixtures/mistral/<prompt>.json.

    The prompt name is detected from a marker each system prompt contains:
    `[prompt:<name>]`. Fixtures hold {"default": "...", "match": [{"contains": "...", "text": "..."}]}.
    """

    name = "replay"

    def __init__(self, fixtures_dir: Path = FIXTURES) -> None:
        self.dir = fixtures_dir
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
        user = "\n".join(m["content"] for m in request.messages if m["role"] == "user" and
                         isinstance(m["content"], str))
        name = system.split("[prompt:", 1)[1].split("]", 1)[0] if "[prompt:" in system else "unknown"
        data: dict[str, Any] = json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))
        for rule in data.get("match", []):
            if rule["contains"] in user:
                return LLMResponse(rule["text"], 500, 300, request.model)
        return LLMResponse(data["default"], 500, 300, request.model)


class MockMistral(ReplayProvider):
    """Replay fixtures for planner/generator/repair/summary; the analyst answer is derived from
    the issue list in the prompt (ids are run-specific), mimicking a recorded classification."""

    name = "mock-mistral"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
        if "[prompt:analyst]" in system:
            self.requests.append(request)
            user = request.messages[1]["content"]
            issues = json.loads(user.split("Issues (JSON):", 1)[1].strip())
            out = []
            for i in issues:
                cls = {"flaky_test": "flaky", "test_error": "test_error", "ui_environment": "environment"}.get(
                    i["kind"], "app_bug")
                out.append({"issue_id": i["issue_id"], "classification": cls, "severity": i["default_severity"],
                            "title": i["default_title"][:150], "impact": "Recorded analyst impact.",
                            "root_cause": "Recorded likely root cause.", "repro_steps": ["Step 1", "Step 2"],
                            "suggested_fix": "Recorded suggested fix.", "duplicate_of": None})
            return LLMResponse(json.dumps({"issues": out}), 900, 600, request.model)
        return await super().complete(request)


async def _no_sleep(_: float) -> None:
    return None


def fast_limiter() -> RateLimiter:
    return RateLimiter(rps=1000, tpm=10_000_000, sleep=_no_sleep)
