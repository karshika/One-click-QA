"""Mistral La Plateforme implementation of LLMProvider (POST /v1/chat/completions)."""

from __future__ import annotations

import email.utils
import time
from typing import Any

import httpx

from app.llm.provider import (
    LLMAuthError,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    RetryableLLMError,
)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class MistralProvider(LLMProvider):
    name = "mistral"

    def __init__(self, api_key: str, base_url: str = "https://api.mistral.ai/v1", timeout: float = 90.0,
                 use_json_schema: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise LLMAuthError("MISTRAL_API_KEY is empty")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._use_json_schema = use_json_schema
        self._transport = transport

    def __repr__(self) -> str:  # never leak the key
        return f"MistralProvider(base_url={self._base!r})"

    def _body(self, req: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.seed is not None:
            body["random_seed"] = req.seed
        if req.json_schema is not None and self._use_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": req.json_schema, "strict": True},
            }
        elif req.json_mode or req.json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.post(f"{self._base}/chat/completions", json=self._body(request),
                                         headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableLLMError(f"network error talking to Mistral: {type(exc).__name__}") from exc

        if resp.status_code == 429:
            raise RetryableLLMError("Mistral rate limit (429)", status=429,
                                    retry_after=parse_retry_after(resp.headers.get("retry-after")))
        if resp.status_code >= 500:
            raise RetryableLLMError(f"Mistral server error ({resp.status_code})", status=resp.status_code,
                                    retry_after=parse_retry_after(resp.headers.get("retry-after")))
        if resp.status_code in (401, 403):
            raise LLMAuthError(f"Mistral rejected the API key ({resp.status_code}). Check MISTRAL_API_KEY.")
        if resp.status_code >= 400:
            detail = resp.text[:500].replace(self._key, "[REDACTED]")
            raise LLMError(f"Mistral request failed ({resp.status_code}): {detail}")

        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected Mistral response shape") from exc
        if isinstance(text, list):  # content chunks
            text = "".join(c.get("text", "") for c in text if isinstance(c, dict))
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text or "",
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or request.model),
            raw={"id": data.get("id")},
        )
