"""Credential encryption (Fernet) and secret masking for logs, reports and LLM prompts."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

log = logging.getLogger(__name__)

MASK = "[REDACTED]"

_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"),
    re.compile(r"(?i)(set-cookie[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
]


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    if s.secret_key and s.secret_key.get_secret_value().strip():
        key = s.secret_key.get_secret_value().strip().encode()
    else:
        path = s.data_dir / ".secret_key"
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.write_bytes(key)
            log.warning("OCQA_SECRET_KEY not set; generated a local key at %s (set it in .env for production)", path)
    return Fernet(key)


def encrypt_json(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return dict(json.loads(_fernet().decrypt(token.encode())))
    except (InvalidToken, ValueError):
        log.error("Could not decrypt stored credentials (wrong OCQA_SECRET_KEY?)")
        return {}


def mask_text(text: str, secrets: list[str] | None = None) -> str:
    """Remove known secret values and common secret-looking patterns from text."""
    if not text:
        return text
    out = text
    for sec in sorted({s for s in (secrets or []) if s and len(s) >= 3}, key=len, reverse=True):
        out = out.replace(sec, MASK)
    for pat in _PATTERNS:
        out = pat.sub(lambda m: m.group(1) + MASK, out)
    return out


def mask_obj(obj: Any, secrets: list[str] | None = None) -> Any:
    """Recursively mask strings in a JSON-like structure."""
    if isinstance(obj, str):
        return mask_text(obj, secrets)
    if isinstance(obj, list):
        return [mask_obj(o, secrets) for o in obj]
    if isinstance(obj, dict):
        return {k: (MASK if _is_secret_key(k) else mask_obj(v, secrets)) for k, v in obj.items()}
    return obj


def _is_secret_key(k: object) -> bool:
    return isinstance(k, str) and bool(re.search(r"(?i)password|passwd|secret|token|api[_-]?key|authorization|cookie", k))
