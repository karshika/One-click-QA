"""Prompt-hash response cache stored in the LLMCache table.

key = sha256(canonical JSON of {model, messages, params}). Identical inputs never hit the
API twice (only successful, validated responses are stored).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.db.models import LLMCache
from app.db.session import session_scope


def make_key(model: str, messages: list[dict[str, Any]], params: dict[str, Any]) -> str:
    payload = json.dumps({"model": model, "messages": messages, "params": params}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CachedResponse:
    text: str
    tokens_in: int
    tokens_out: int


class ResponseCache:
    def get(self, key: str) -> CachedResponse | None:
        with session_scope() as s:
            row = s.get(LLMCache, key)
            if row is None:
                return None
            row.hits += 1
            s.add(row)
            s.commit()
            return CachedResponse(row.response_text, row.tokens_in, row.tokens_out)

    def put(self, key: str, model: str, text: str, tokens_in: int, tokens_out: int) -> None:
        with session_scope() as s:
            row = s.get(LLMCache, key)
            if row is None:
                row = LLMCache(key=key, model=model)
            row.response_text = text
            row.tokens_in = tokens_in
            row.tokens_out = tokens_out
            s.add(row)
            s.commit()

    def delete(self, key: str) -> None:
        with session_scope() as s:
            row = s.get(LLMCache, key)
            if row is not None:
                s.delete(row)
                s.commit()
