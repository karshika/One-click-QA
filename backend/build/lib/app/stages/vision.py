"""Optional vision check (ENABLE_VISION_CHECK=true; off by default).

Sends crawl screenshots (max 3 pages) to the vision model to flag OBVIOUS layout problems.
Findings are marked "AI-suggested, unverified" and are excluded from the score.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.core.context import RunContext
from app.llm import prompts
from app.llm.provider import LLMError
from app.llm.schemas import VisionResult

MAX_PAGES = 3


async def run_vision(ctx: RunContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in ctx.ok_pages[:MAX_PAGES]:
        if not p.screenshot_path or not Path(p.screenshot_path).is_file():
            continue
        b64 = base64.b64encode(Path(p.screenshot_path).read_bytes()).decode()
        base = prompts.render("vision", url=ctx.path_of(p.url))
        user = base.messages[-1]
        msg = {"role": "user", "content": [{"type": "text", "text": user["content"]},
                                           {"type": "image_url", "image_url": f"data:image/png;base64,{b64}"}]}
        prompt = prompts.RenderedPrompt(base.name, base.version, base.role, base.messages[:-1] + [msg])
        try:
            res = await ctx.llm.structured("analyze", prompt, VisionResult, role="vision", max_tokens=800)
        except LLMError as exc:
            ctx.degraded.setdefault("analyze", f"vision skipped: {type(exc).__name__}")
            break
        for i in res.issues:
            out.append({"url": p.url, "title": i.title, "description": i.description, "severity": i.severity,
                        "screenshot": p.screenshot_path})
    return out
