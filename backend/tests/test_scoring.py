from app.core.scoring import (
    a11y_page_score,
    a11y_score,
    api_score,
    grade,
    overall,
    perf_page_score,
    perf_score,
    ui_score,
)


def test_ui_score() -> None:
    assert ui_score(["passed", "passed", "failed", "failed"]) == 50.0
    assert ui_score(["passed", "flaky"]) == 75.0
    assert ui_score(["passed", "error", "skipped"]) == 100.0  # inconclusive excluded
    assert ui_score(["passed"], console_error_pages=1, broken_links=1) == 90.0
    assert ui_score(["failed"], console_error_pages=100) == 0.0
    assert ui_score(["passed"], console_error_pages=100) == 60.0  # penalty capped at 40
    assert ui_score([], crawled_pages=3) == 100.0
    assert ui_score([]) is None


def test_api_score() -> None:
    assert api_score(["passed", "passed", "failed", "warning", "skipped"]) == 66.7
    assert api_score(["warning"]) is None


def test_a11y_scores() -> None:
    assert a11y_page_score(["critical", "serious"]) == 77.0
    assert a11y_page_score(["critical"] * 10) == 0.0
    assert a11y_score([[], ["critical"]]) == 92.5
    assert a11y_score([]) is None


def test_perf_scores() -> None:
    assert perf_page_score({"lcp": "good", "cls": "poor"}) == 50.0
    assert perf_page_score({"lcp": "good", "cls": "needs-improvement", "fcp": "good", "ttfb": "good"}) == 87.5
    assert perf_score([{"lcp": "good"}, {"lcp": "poor"}]) == 50.0
    assert perf_score([]) is None


def test_overall_weights_and_redistribution() -> None:
    assert overall({"ui": 100, "api": 100, "a11y": 100, "perf": 100}) == 100.0
    assert overall({"ui": 0, "api": 100, "a11y": 100, "perf": 100}) == 60.0  # UI weight 40
    assert overall({"ui": 50, "api": None, "a11y": 100, "perf": None}) == 66.7  # (40*50+20*100)/60
    assert overall({"ui": None}) is None


def test_deterministic() -> None:
    s = {"ui": 61.3, "api": 77.7, "a11y": 82.1, "perf": 90.0}
    assert overall(s) == overall(dict(s)) == 73.9  # 24.52 + 19.425 + 16.42 + 13.5


def test_grade() -> None:
    assert grade(95) == "A" and grade(59) == "F" and grade(None) == "n/a"
