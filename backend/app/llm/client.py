"""LLMClient: the only way stages talk to an LLM.

Order per call: budget check → cache → rate limiter → retry/backoff → provider →
(JSON parse + pydantic validation → one repair retry) → cache store → LLMCall log.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlmodel import col, select

from app.config import Settings, get_settings
from app.db.models import LLMCall
from app.db.session import session_scope
from app.llm.cache import ResponseCache, make_key
from app.llm.limiter import RateLimiter, shared_limiter, with_retries
from app.llm.prompts import RenderedPrompt
from app.llm.provider import (
    BudgetExhausted,
    LLMError,
    LLMInvalidOutput,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
)
from app.security.secrets import mask_text

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


@dataclass
class StageStats:
    calls: int = 0  # real API calls (budget-consuming)
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    fallbacks: int = 0


@dataclass
class UsageStats:
    by_stage: dict[str, StageStats] = field(default_factory=lambda: defaultdict(StageStats))

    @property
    def calls(self) -> int:
        return sum(s.calls for s in self.by_stage.values())

    @property
    def cache_hits(self) -> int:
        return sum(s.cache_hits for s in self.by_stage.values())

    @property
    def tokens_in(self) -> int:
        return sum(s.tokens_in for s in self.by_stage.values())

    @property
    def tokens_out(self) -> int:
        return sum(s.tokens_out for s in self.by_stage.values())


@dataclass
class TextResult:
    text: str
    cache_hit: bool
    key: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    chars = sum(len(m["content"]) if isinstance(m.get("content"), str) else 2000 for m in messages)
    return chars // 3 + 50


def parse_json_loose(text: str) -> Any:
    """Parse JSON, tolerating ```json fences and leading/trailing prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
        end = max(t.rfind("}"), t.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(t[start : end + 1])
        raise


def validate_json(text: str, schema: type[M]) -> M:
    """Parse + validate; raises ValueError with a readable message on failure."""
    try:
        data = parse_json_loose(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Output is not valid JSON: {exc}") from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10])
        raise ValueError(f"JSON does not match schema: {errs}") from exc


class LLMClient:
    def __init__(self, provider: LLMProvider | None, *, settings: Settings | None = None,
                 limiter: RateLimiter | None = None, cache: ResponseCache | None = None,
                 run_id: int | None = None, run_budget: int | None = None,
                 secrets: list[str] | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = provider
        self.limiter = limiter or shared_limiter()
        self.cache = cache or ResponseCache()
        self.run_id = run_id
        self.run_budget = run_budget if run_budget is not None else self.settings.max_llm_calls_per_run
        self.secrets = secrets or []
        self.usage = UsageStats()

    # ------------------------------------------------------------------ helpers
    @property
    def available(self) -> bool:
        return self.provider is not None

    @property
    def budget_remaining(self) -> int:
        return max(0, self.run_budget - self.usage.calls)

    def model_for(self, role: str) -> str:
        s = self.settings
        return {"planner": s.mistral_model_planner, "code": s.mistral_model_code,
                "fast": s.mistral_model_fast, "vision": s.mistral_model_vision}.get(role, s.mistral_model_planner)

    def _calls_today(self) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as s:
            stmt = select(func.count()).select_from(LLMCall).where(
                col(LLMCall.created_at) >= start, col(LLMCall.cache_hit).is_(False),
                col(LLMCall.status).in_(["ok", "error"]),
            )
            return int(s.exec(stmt).one())

    def _check_budget(self) -> None:
        if self.usage.calls >= self.run_budget:
            raise BudgetExhausted(f"per-run LLM budget of {self.run_budget} calls exhausted")
        if self._calls_today() >= self.settings.max_llm_calls_per_day:
            raise BudgetExhausted(f"per-day LLM budget of {self.settings.max_llm_calls_per_day} calls exhausted")

    def _log(self, *, stage: str, model: str, prompt: RenderedPrompt, key: str, tokens_in: int = 0,
             tokens_out: int = 0, latency_ms: int = 0, cache_hit: bool = False, status: str = "ok",
             error: str | None = None, attempts: int = 1) -> None:
        try:
            with session_scope() as s:
                s.add(LLMCall(run_id=self.run_id, stage=stage, model=model, prompt_name=prompt.name,
                              prompt_version=prompt.version, prompt_hash=key[:16], tokens_in=tokens_in,
                              tokens_out=tokens_out, latency_ms=latency_ms, cache_hit=cache_hit,
                              status=status, error=(mask_text(error, self.secrets)[:1000] if error else None),
                              attempts=attempts))
                s.commit()
        except Exception:
            log.exception("failed to log LLM call")

    def _mask_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            c = m.get("content")
            out.append({**m, "content": mask_text(c, self.secrets) if isinstance(c, str) else c})
        return out

    # ------------------------------------------------------------------ core call
    async def _call(self, stage: str, prompt: RenderedPrompt, *, role: str | None = None,
                    max_tokens: int = 2000, json_mode: bool = False,
                    temperature: float | None = None, use_cache: bool = True,
                    extra_messages: list[dict[str, Any]] | None = None) -> TextResult:
        model = self.model_for(role or prompt.role)
        messages = self._mask_messages(prompt.messages + (extra_messages or []))
        req = LLMRequest(model=model, messages=messages,
                         temperature=self.settings.llm_temperature if temperature is None else temperature,
                         max_tokens=max_tokens, json_mode=json_mode)
        key = make_key(model, messages, req.cache_params())
        st = self.usage.by_stage[stage]

        if use_cache:
            hit = self.cache.get(key)
            if hit is not None:
                st.cache_hits += 1
                self._log(stage=stage, model=model, prompt=prompt, key=key, tokens_in=hit.tokens_in,
                          tokens_out=hit.tokens_out, cache_hit=True)
                return TextResult(hit.text, True, key, model, hit.tokens_in, hit.tokens_out)

        if self.provider is None:
            st.fallbacks += 1
            self._log(stage=stage, model=model, prompt=prompt, key=key, status="unavailable",
                      error="MISTRAL_API_KEY not configured")
            raise LLMUnavailable("LLM not configured (MISTRAL_API_KEY missing)")
        try:
            self._check_budget()
        except BudgetExhausted as exc:
            st.fallbacks += 1
            self._log(stage=stage, model=model, prompt=prompt, key=key, status="budget_exhausted", error=str(exc))
            raise

        provider = self.provider
        est = estimate_tokens(messages) + max_tokens

        async def attempt() -> LLMResponse:
            await self.limiter.acquire(est)
            return await provider.complete(req)

        t0 = time.perf_counter()
        st.calls += 1  # counts against the budget even if it fails
        try:
            resp, attempts = await with_retries(
                attempt, max_attempts=self.settings.mistral_max_retries,
                on_retry=lambda n, e, d: log.warning("LLM retry %s for %s after %.1fs: %s", n, stage, d, e))
        except LLMError as exc:
            self._log(stage=stage, model=model, prompt=prompt, key=key, status="error", error=str(exc),
                      latency_ms=int((time.perf_counter() - t0) * 1000),
                      attempts=self.settings.mistral_max_retries)
            raise
        latency = int((time.perf_counter() - t0) * 1000)
        st.tokens_in += resp.tokens_in
        st.tokens_out += resp.tokens_out
        self._log(stage=stage, model=model, prompt=prompt, key=key, tokens_in=resp.tokens_in,
                  tokens_out=resp.tokens_out, latency_ms=latency, attempts=attempts)
        return TextResult(resp.text, False, key, model, resp.tokens_in, resp.tokens_out)

    # ------------------------------------------------------------------ public API
    async def text(self, stage: str, prompt: RenderedPrompt, *, role: str | None = None,
                   max_tokens: int = 1500, temperature: float | None = None) -> TextResult:
        res = await self._call(stage, prompt, role=role, max_tokens=max_tokens, temperature=temperature)
        if not res.cache_hit and res.text.strip():
            self.cache.put(res.key, res.model, res.text, res.tokens_in, res.tokens_out)
        return res

    async def structured(self, stage: str, prompt: RenderedPrompt, schema: type[M], *, role: str | None = None,
                         max_tokens: int = 3000) -> M:
        res = await self._call(stage, prompt, role=role, max_tokens=max_tokens, json_mode=True)
        obj, error = self._try_validate(res.text, schema)
        if obj is None and res.cache_hit:
            # stale/invalid cache entry (e.g. schema changed): drop it and fetch fresh
            self.cache.delete(res.key)
            res = await self._call(stage, prompt, role=role, max_tokens=max_tokens, json_mode=True,
                                   use_cache=False)
            obj, error = self._try_validate(res.text, schema)
        if obj is not None:
            if not res.cache_hit:
                self.cache.put(res.key, res.model, res.text, res.tokens_in, res.tokens_out)
            return obj

        log.info("structured output invalid for %s (%s); repair retry", stage, error)
        repair_msgs = [
            {"role": "assistant", "content": res.text[:6000]},
            {"role": "user", "content": (
                f"Your previous answer was invalid: {error}\n"
                "Return ONLY the corrected JSON object that matches the requested schema. No prose."
            )},
        ]
        res2 = await self._call(stage, prompt, role=role, max_tokens=max_tokens, json_mode=True,
                                extra_messages=repair_msgs)
        obj, error2 = self._try_validate(res2.text, schema)
        if obj is None:
            self._log(stage=stage, model=res2.model, prompt=prompt, key=res2.key, status="invalid", error=error2)
            raise LLMInvalidOutput(f"{stage}: invalid structured output after repair: {error2}")
        # Store the repaired answer under the ORIGINAL prompt key so re-runs are 0-call cache hits.
        self.cache.put(res.key, res2.model, res2.text, res2.tokens_in, res2.tokens_out)
        return obj

    @staticmethod
    def _try_validate(text: str, schema: type[M]) -> tuple[M | None, str]:
        try:
            return validate_json(text, schema), ""
        except ValueError as exc:
            return None, str(exc)


def build_client(*, run_id: int | None = None, secrets: list[str] | None = None,
                 run_budget: int | None = None) -> LLMClient:
    s = get_settings()
    provider: LLMProvider | None = None
    if s.has_llm_key and s.mistral_api_key is not None:
        from app.llm.mistral import MistralProvider

        provider = MistralProvider(s.mistral_api_key.get_secret_value().strip(), s.mistral_base_url,
                                   s.mistral_timeout_s)
    return LLMClient(provider, settings=s, run_id=run_id, run_budget=run_budget, secrets=secrets)
