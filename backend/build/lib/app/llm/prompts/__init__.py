"""Versioned prompt templates.

File format (`<name>.md`):

    ---
    name: planner
    version: 3
    role: planner          # which model role runs it (planner|code|fast|vision)
    ---
    === system ===
    ...
    === user ===
    ... $variables ...

Variables use `string.Template` syntax (`$name`) so JSON braces need no escaping.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent

UNTRUSTED_OPEN = "<<<UNTRUSTED_PAGE_DATA"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_PAGE_DATA>>>"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    role: str
    system: str
    user: str


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    role: str
    messages: list[dict[str, Any]]


@lru_cache
def load(name: str) -> PromptTemplate:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"prompt {name} has no front matter")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.split("#")[0].strip() for k, v in meta.items()}
    body = m.group(2)
    parts = re.split(r"^=== (system|user) ===\s*$", body, flags=re.M)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    return PromptTemplate(name=meta.get("name", name), version=meta.get("version", "1"),
                          role=meta.get("role", "planner"), system=sections.get("system", ""),
                          user=sections.get("user", ""))


def render(name: str, **variables: Any) -> RenderedPrompt:
    t = load(name)
    vals = {k: (v if isinstance(v, str) else str(v)) for k, v in variables.items()}
    msgs = []
    if t.system:
        msgs.append({"role": "system", "content": string.Template(t.system).safe_substitute(vals)})
    msgs.append({"role": "user", "content": string.Template(t.user).safe_substitute(vals)})
    return RenderedPrompt(t.name, t.version, t.role, msgs)


def untrusted(label: str, text: str, max_chars: int = 12_000) -> str:
    """Wrap untrusted (page-derived) content in a delimited data block.

    Delimiter look-alikes inside the content are neutralized so the page can't "close"
    the block and smuggle instructions.
    """
    clean = text.replace("<<<", "‹‹‹").replace(">>>", "›››")
    if len(clean) > max_chars:
        clean = clean[:max_chars] + "\n…[truncated]"
    safe_label = re.sub(r"[^a-zA-Z0-9_\-]", "_", label)[:40]
    return f"{UNTRUSTED_OPEN} label={safe_label}>>>\n{clean}\n{UNTRUSTED_CLOSE}"
