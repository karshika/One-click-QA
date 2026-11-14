"""Stage 6 – Accessibility: axe-core (vendored axe.min.js) on every crawled page.

Runs WCAG 2.0/2.1/2.2 A + AA rules only (best-practice rules are excluded to avoid noise).
Pages that returned >= 400 are skipped: they are reported as broken links instead.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PWError

from app.core.context import RunContext
from app.stages.browser import GuardConfig, launch_browser, new_context

AXE_JS = (Path(__file__).parent / "vendor" / "axe.min.js").read_text(encoding="utf-8")
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


@dataclass
class PageA11y:
    url: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    passes: int = 0


def simplify(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": v.get("id"),
        "impact": v.get("impact"),
        "wcag_tags": [t for t in v.get("tags", []) if t.startswith("wcag")],
        "description": v.get("description", ""),
        "help": v.get("help", ""),
        "help_url": v.get("helpUrl", ""),
        "nodes": [{"target": n.get("target"), "html": (n.get("html") or "")[:300],
                   "summary": (n.get("failureSummary") or "")[:300]} for n in v.get("nodes", [])[:10]],
        "node_count": len(v.get("nodes", [])),
    }


async def run_a11y(ctx: RunContext) -> list[PageA11y]:
    results: list[PageA11y] = []
    guard = GuardConfig(base_url=ctx.base_url, allow_local=ctx.allow_local, extra_domains=ctx.extra_domains)
    async with launch_browser() as browser:
        context = await new_context(browser, guard, storage_state=ctx.storage_state)
        try:
            for p in ctx.ok_pages:
                if ctx.should_cancel():
                    break
                res = PageA11y(url=p.url)
                page = await context.new_page()
                try:
                    await page.goto(p.url, wait_until="load", timeout=20_000)
                    with contextlib.suppress(PWError):
                        await page.wait_for_load_state("networkidle", timeout=2500)
                    await page.add_script_tag(content=AXE_JS)
                    out = await page.evaluate(
                        """(tags) => axe.run(document, {runOnly: {type: 'tag', values: tags},
                             resultTypes: ['violations']})""", WCAG_TAGS)
                    res.violations = [simplify(v) for v in out.get("violations", [])]
                    res.passes = len(out.get("passes", []))
                except PWError as exc:
                    res.error = str(exc).splitlines()[0][:300]
                finally:
                    await page.close()
                results.append(res)
        finally:
            await context.close()
    return results
