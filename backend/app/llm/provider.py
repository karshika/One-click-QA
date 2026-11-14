"""Provider-agnostic LLM interface. Only Mistral is implemented (app/llm/mistral.py)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMRequest:
    model: str
    messages: list[dict[str, Any]]
    temperature: float = 0.1
    max_tokens: int = 2000
    json_mode: bool = False
    json_schema: dict[str, Any] | None = None
    seed: int | None = 7

    def cache_params(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "json_mode": self.json_mode,
            "json_schema": self.json_schema,
            "seed": self.seed,
        }


@dataclass
class LLMResponse:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(Exception):
    """Non-retryable provider error."""


class RetryableLLMError(LLMError):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class LLMAuthError(LLMError):
    pass


class LLMUnavailable(LLMError):
    """No API key configured / provider disabled → callers use deterministic fallback."""


class BudgetExhausted(LLMError):
    """Per-run or per-day call budget used up → callers use deterministic fallback."""


class LLMInvalidOutput(LLMError):
    """Structured output failed validation even after the repair retry."""


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Perform one chat completion. Raise RetryableLLMError for 429/5xx/timeouts."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
