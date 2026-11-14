"""Guards that the demo app keeps its planted bugs (and its clean parts stay clean)."""

import pytest

demo = pytest.importorskip("demo_app.main")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def c() -> TestClient:
    return TestClient(demo.app, raise_server_exceptions=False)


def test_planted_api_bugs(c: TestClient) -> None:
    assert c.get("/api/products/0").status_code == 500  # B7
    assert c.get("/api/products/1").status_code == 200
    assert c.get("/api/products/999").status_code == 404
    assert isinstance(c.get("/api/stats").json()["total_orders"], str)  # B8
    assert c.get("/api/orders").status_code == 401  # correct auth behavior


def test_planted_login_flaw(c: TestClient) -> None:
    ok = c.post("/login", data={"email": "demo@example.com", "password": "Demo-Pass-2024"}, follow_redirects=False)
    assert ok.status_code == 303
    flaw = c.post("/login", data={"email": "demo@example.com", "password": "dEMO-pASS-2024"}, follow_redirects=False)
    assert flaw.status_code == 303  # B1: case-insensitive password
    bad = c.post("/login", data={"email": "demo@example.com", "password": "wrong"}, follow_redirects=False)
    assert bad.status_code == 200 and "Invalid email or password" in bad.text


def test_planted_page_bugs(c: TestClient) -> None:
    home = c.get("/").text
    assert '<img src="/static/team.svg" width="320" height="120">' in home  # B2
    assert "initCarousel()" in home  # B9
    assert c.get("/old-pricing").status_code == 404  # B10
