from pathlib import Path

import pytest

from app.stages.crawler import CrawlConfig, Credentials, crawl, looks_destructive, normalize_url

pytestmark = pytest.mark.browser


def test_normalize_and_destructive() -> None:
    assert normalize_url("/a#x", "http://h.com/b") == "http://h.com/a"
    assert normalize_url("mailto:x@y", "http://h.com/") is None
    assert looks_destructive("http://h.com/logout")
    assert looks_destructive("http://h.com/items/3", "Delete item")
    assert not looks_destructive("http://h.com/products")


async def test_crawl_demo_app(demo_url: str, tmp_path: Path) -> None:
    cfg = CrawlConfig(base_url=demo_url, max_pages=15, allow_local=True, request_delay_ms=0,
                      credentials=Credentials("demo@example.com", "Demo-Pass-2024"), artifacts_dir=tmp_path)
    res = await crawl(cfg)
    by_path = {p.url.replace(demo_url, ""): p for p in res.pages}
    assert res.login.success, res.login.message
    assert "/" in by_path and "/products" in by_path and "/contact" in by_path
    assert by_path["/old-pricing"].status_code == 404  # B10 broken link
    assert any("initCarousel" in e for e in by_path["/"].console_errors)  # B9
    assert not any("/logout" in u for u in by_path)  # destructive link skipped
    assert any("/products/" in u for u in by_path)  # JS-rendered links discovered
    assert any(c["url"].endswith("/api/products") for c in by_path["/products"].api_calls)
    assert by_path["/contact"].forms and by_path["/contact"].forms[0]["fields"][0]["label"] == "Name"
    assert "/account" in by_path  # reachable only after login
    assert Path(by_path["/"].screenshot_path or "").exists()
    assert "button" in by_path["/"].a11y_tree
