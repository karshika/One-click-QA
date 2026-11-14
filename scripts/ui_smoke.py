"""Drives the real dashboard like a user: create the demo project, press Run QA, watch live
progress, open every tab, take README screenshots, and run axe-core on the dashboard itself.

Usage (with backend :8000, frontend :3000 and demo app :8001 running):
    .venv/Scripts/python scripts/ui_smoke.py [--demo-url http://localhost:8001] [--out docs/screenshots]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
AXE = (ROOT / "backend" / "app" / "stages" / "vendor" / "axe.min.js").read_text(encoding="utf-8")


def axe(page: Page, label: str, results: dict) -> None:
    page.add_script_tag(content=AXE)
    out = page.evaluate(
        "() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
    bad = [{"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"]), "help": v["help"],
            "targets": [{"t": n["target"], "html": n["html"][:160], "why": n["failureSummary"][:200]}
                        for n in v["nodes"][:3]]}
           for v in out["violations"]]
    results[label] = bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://localhost:3000")
    ap.add_argument("--demo-url", default="http://localhost:8001")
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    axe_results: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(args.app)
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Projects", exact=True)).to_be_visible()
        page.wait_for_timeout(500)
        # Create project through the form (consent required)
        if not page.get_by_role("heading", name="New project").is_visible():
            page.get_by_role("button", name="New project").click()
        page.get_by_label("App URL").fill(args.demo_url)
        page.get_by_label("Project name").fill("ShopLite demo")
        page.get_by_label("OpenAPI spec URL").fill("/openapi.json")
        page.get_by_label("Username / email").fill("demo@example.com")
        page.get_by_label("Password", exact=True).fill("Demo-Pass-2024")
        page.screenshot(path=str(out / "01-new-project.png"), full_page=True)
        axe(page, "projects/new", axe_results)
        create = page.get_by_role("button", name="Create project")
        expect(create).to_be_disabled()  # consent not yet given
        page.get_by_role("checkbox").check()
        create.click()
        page.wait_for_url("**/projects/*")
        expect(page.get_by_role("heading", name="ShopLite demo")).to_be_visible()
        axe(page, "project", axe_results)
        page.get_by_role("button", name="Run QA").click()
        page.wait_for_url("**/runs/*")
        run_id = page.url.rstrip("/").split("/")[-1]
        expect(page.get_by_role("list", name="Pipeline progress")).to_be_visible()
        time.sleep(20)
        page.screenshot(path=str(out / "02-run-live-progress.png"), full_page=False)
        t0 = time.time()
        while time.time() - t0 < args.timeout:
            if page.get_by_role("link", name="PDF").count():
                break
            time.sleep(3)
        else:
            print("run did not finish in time", file=sys.stderr)
            return 1
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / "03-run-summary.png"), full_page=True)
        axe(page, "run/summary", axe_results)
        for key, label in [("ui", "UI flows"), ("api", "API"), ("a11y", "Accessibility"), ("perf", "Performance"),
                           ("bugs", "Bugs"), ("llm", "LLM usage")]:
            page.get_by_role("tab", name=label).click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / f"04-tab-{key}.png"), full_page=True)
            axe(page, f"run/{key}", axe_results)
        # dark mode
        page.get_by_role("button", name="Switch to dark mode").click()
        page.get_by_role("tab", name="Summary").click()
        page.wait_for_timeout(800)
        page.screenshot(path=str(out / "05-run-dark.png"), full_page=False)
        axe(page, "run/summary-dark", axe_results)
        page.get_by_role("button", name="Switch to light mode").click()
        # export links work
        for fmt in ("md", "html", "pdf"):
            r = page.request.get(f"http://localhost:8000/runs/{run_id}/report?format={fmt}")
            assert r.ok, (fmt, r.status)
        page.goto(f"{args.app}/settings")
        axe(page, "settings", axe_results)
        browser.close()
    serious = {k: [v for v in vs if v["impact"] in ("serious", "critical")] for k, vs in axe_results.items()}
    print(json.dumps({"run_id": run_id, "axe": axe_results}, indent=2))
    failing = {k: v for k, v in serious.items() if v}
    if failing:
        print("Dashboard has serious/critical axe violations:", json.dumps(failing, indent=2), file=sys.stderr)
        return 2
    print("Dashboard: no serious/critical axe violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
