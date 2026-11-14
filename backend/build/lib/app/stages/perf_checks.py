"""Stage 7 – Performance: Navigation Timing + PerformanceObserver (LCP, CLS, FCP) + resource timing.

Each page is loaded N times (default 3) in a fresh browser context (cold cache) and the
median of each metric is used. Ratings follow the web.dev Core Web Vitals thresholds.
Lighthouse is not required (optional, not used when absent).
"""

from __future__ import annotations

import contextlib
import statistics
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Error as PWError

from app.core.context import RunContext
from app.stages.browser import GuardConfig, launch_browser, new_context

# metric: (good_max, poor_above)  – values in ms except CLS
THRESHOLDS: dict[str, tuple[float, float]] = {
    "lcp": (2500, 4000),
    "fcp": (1800, 3000),
    "cls": (0.1, 0.25),
    "ttfb": (800, 1800),
}
SCORED_METRICS = ["lcp", "fcp", "cls", "ttfb"]

OBSERVER_JS = """
(() => {
  window.__ocqa = { lcp: null, cls: 0, fcp: null };
  const obs = (type, fn) => new PerformanceObserver((l) => l.getEntries().forEach(fn))
    .observe({ type, buffered: true });
  try {
    obs('largest-contentful-paint', (e) => { window.__ocqa.lcp = e.startTime; });
    obs('layout-shift', (e) => { if (!e.hadRecentInput) window.__ocqa.cls += e.value; });
    obs('paint', (e) => { if (e.name === 'first-contentful-paint') window.__ocqa.fcp = e.startTime; });
  } catch (e) {}
})();
"""

COLLECT_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource');
  const transfer = (nav.transferSize || 0) + res.reduce((a, r) => a + (r.transferSize || 0), 0);
  return {
    ttfb: nav.responseStart != null ? nav.responseStart : null,
    dcl: nav.domContentLoadedEventEnd || null,
    load: nav.loadEventEnd || null,
    transfer_size: transfer,
    request_count: res.length + 1,
    lcp: window.__ocqa ? window.__ocqa.lcp : null,
    fcp: window.__ocqa ? window.__ocqa.fcp : null,
    cls: window.__ocqa ? Math.round(window.__ocqa.cls * 10000) / 10000 : null,
  };
}
"""


def rate(metric: str, value: float | None) -> str | None:
    if value is None or metric not in THRESHOLDS:
        return None
    good, poor = THRESHOLDS[metric]
    if value <= good:
        return "good"
    if value > poor:
        return "poor"
    return "needs-improvement"


@dataclass
class PagePerf:
    url: str
    metrics: dict[str, float | None] = field(default_factory=dict)
    ratings: dict[str, str] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def median_metrics(samples: list[dict[str, Any]]) -> dict[str, float | None]:
    keys = ["ttfb", "fcp", "lcp", "cls", "dcl", "load", "transfer_size", "request_count"]
    out: dict[str, float | None] = {}
    for k in keys:
        vals = [float(s[k]) for s in samples if s.get(k) is not None]
        out[k] = round(statistics.median(vals), 4 if k == "cls" else 1) if vals else None
    return out


async def run_perf(ctx: RunContext) -> list[PagePerf]:
    results: list[PagePerf] = []
    guard = GuardConfig(base_url=ctx.base_url, allow_local=ctx.allow_local, extra_domains=ctx.extra_domains)
    async with launch_browser() as browser:
        for p in ctx.ok_pages:
            if ctx.should_cancel():
                break
            pp = PagePerf(url=p.url)
            for _ in range(max(1, ctx.perf_runs)):
                context = await new_context(browser, guard, storage_state=ctx.storage_state)
                await context.add_init_script(OBSERVER_JS)
                page = await context.new_page()
                try:
                    await page.goto(p.url, wait_until="load", timeout=30_000)
                    with contextlib.suppress(PWError):
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    await page.wait_for_timeout(1500)  # let late layout shifts / LCP candidates land
                    pp.samples.append(await page.evaluate(COLLECT_JS))
                except PWError as exc:
                    pp.error = str(exc).splitlines()[0][:300]
                finally:
                    await context.close()
            pp.metrics = median_metrics(pp.samples)
            pp.ratings = {m: r for m in SCORED_METRICS if (r := rate(m, pp.metrics.get(m)))}
            results.append(pp)
    return results
