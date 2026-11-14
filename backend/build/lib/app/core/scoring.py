"""Deterministic quality score (0–100). Documented in docs/ARCHITECTURE.md#quality-score.

The LLM never influences these numbers: they are computed only from Playwright results,
axe-core output, HTTP/schema checks and measured performance metrics.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

WEIGHTS: dict[str, int] = {"ui": 40, "api": 25, "a11y": 20, "perf": 15}
A11Y_PENALTY = {"critical": 15, "serious": 8, "moderate": 3, "minor": 1}
RATING_POINTS = {"good": 100.0, "needs-improvement": 50.0, "poor": 0.0}
UI_PENALTY_CONSOLE = 5.0
UI_PENALTY_BROKEN = 5.0
UI_PENALTY_CAP = 40.0


def _clamp(v: float) -> float:
    return round(max(0.0, min(100.0, v)), 1)


def ui_score(test_statuses: Iterable[str], console_error_pages: int = 0, broken_links: int = 0,
             crawled_pages: int = 0) -> float | None:
    """passed=1, flaky=0.5, failed=0; 'error' (inconclusive script/env failures) and 'skipped' excluded."""
    statuses = list(test_statuses)
    conclusive = [s for s in statuses if s in ("passed", "flaky", "failed")]
    if conclusive:
        base = 100.0 * sum(1.0 if s == "passed" else 0.5 if s == "flaky" else 0.0 for s in conclusive) \
            / len(conclusive)
    elif crawled_pages > 0:
        base = 100.0
    else:
        return None
    penalty = min(UI_PENALTY_CAP, UI_PENALTY_CONSOLE * console_error_pages + UI_PENALTY_BROKEN * broken_links)
    return _clamp(base - penalty)


def api_score(check_statuses: Iterable[str]) -> float | None:
    s = [x for x in check_statuses if x in ("passed", "failed")]
    if not s:
        return None
    return _clamp(100.0 * s.count("passed") / len(s))


def a11y_page_score(impacts: Iterable[str | None]) -> float:
    """One entry per violated RULE on the page (not per node)."""
    return _clamp(100.0 - sum(A11Y_PENALTY.get(i or "minor", 1) for i in impacts))


def a11y_score(pages: Iterable[Iterable[str | None]]) -> float | None:
    scores = [a11y_page_score(p) for p in pages]
    if not scores:
        return None
    return _clamp(sum(scores) / len(scores))


def perf_page_score(ratings: Mapping[str, str]) -> float | None:
    pts = [RATING_POINTS[r] for r in ratings.values() if r in RATING_POINTS]
    if not pts:
        return None
    return _clamp(sum(pts) / len(pts))


def perf_score(pages: Iterable[Mapping[str, str]]) -> float | None:
    scores = [s for s in (perf_page_score(r) for r in pages) if s is not None]
    if not scores:
        return None
    return _clamp(sum(scores) / len(scores))


def overall(scores: Mapping[str, float | None]) -> float | None:
    """Weighted mean; categories without data have their weight redistributed proportionally."""
    present = {k: v for k, v in scores.items() if v is not None and k in WEIGHTS}
    if not present:
        return None
    total_w = sum(WEIGHTS[k] for k in present)
    return _clamp(sum(WEIGHTS[k] * v for k, v in present.items()) / total_w)


def grade(score: float | None) -> str:
    if score is None:
        return "n/a"
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
