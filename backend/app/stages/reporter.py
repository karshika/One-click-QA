"""Stage 9 – Report: deterministic scores, executive summary (fast model or template),
run-to-run comparison, and Markdown / standalone HTML / PDF exports."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import col, select

from app.core import scoring
from app.core.context import RunContext
from app.db.models import (
    A11yViolation,
    ApiCheckResult,
    Finding,
    Journey,
    LLMCall,
    Page,
    PerfMetric,
    Project,
    Run,
    StageResult,
    TestCase,
    TestResult,
)
from app.db.session import session_scope
from app.llm import prompts
from app.llm.provider import LLMError

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))


# ----------------------------------------------------------------------------- scores
def compute_scores(ctx: RunContext) -> dict[str, float | None]:
    statuses = [out.status for _, out in ctx.ui_results]
    console_pages = sum(1 for p in ctx.pages if p.console_errors)
    broken = sum(1 for p in ctx.pages if (p.status_code or 0) >= 400 or p.load_error)
    s: dict[str, float | None] = {
        "ui": scoring.ui_score(statuses, console_pages, broken, crawled_pages=len(ctx.pages)),
        "api": scoring.api_score(c.status for c in ctx.api_checks),
        "a11y": scoring.a11y_score([[v["impact"] for v in r.violations] for r in ctx.a11y_results if not r.error]),
        "perf": scoring.perf_score([p.ratings for p in ctx.perf_results if p.ratings]),
    }
    s["overall"] = scoring.overall(s)
    return s


# ----------------------------------------------------------------------------- summary
def template_summary(scores: dict[str, float | None], issues: list[dict[str, Any]], ctx: RunContext) -> str:
    total = scores.get("overall")
    bugs = [i for i in issues if i["classification"] == "app_bug"]
    crit = [i for i in bugs if i["severity"] in ("critical", "high")]
    parts = [f"OneClickQA scored this app {total:.0f}/100 (grade {scoring.grade(total)})." if total is not None
             else "OneClickQA could not compute a score for this run."]
    passed = sum(1 for _, o in ctx.ui_results if o.status == "passed")
    parts.append(f"It crawled {len(ctx.pages)} page(s), ran {len(ctx.ui_results)} UI test(s) ({passed} passed), "
                 f"{len([c for c in ctx.api_checks if c.status in ('passed', 'failed')])} API check(s), and audited "
                 f"accessibility and performance.")
    if bugs:
        parts.append(f"It found {len(bugs)} application issue(s), {len(crit)} of them critical or high severity.")
        top = ", ".join(i["title"] for i in bugs[:3])
        parts.append(f"Fix first: {top}.")
    else:
        parts.append("No application bugs were detected by the automated checks.")
    others = len(issues) - len(bugs)
    if others:
        parts.append(f"{others} other item(s) are test or environment issues, not app bugs.")
    return " ".join(parts)


async def write_summary(ctx: RunContext, scores: dict[str, float | None],
                        issues: list[dict[str, Any]]) -> tuple[str, bool]:
    fallback = template_summary(scores, issues, ctx)
    if not ctx.llm.available:
        return fallback, False
    facts = {
        "scores": scores, "pages_crawled": len(ctx.pages),
        "ui_tests": {s: sum(1 for _, o in ctx.ui_results if o.status == s) for s in
                     ("passed", "failed", "flaky", "error")},
        "top_issues": [{"title": i["title"], "severity": i["severity"], "category": i["category"],
                        "classification": i["classification"]} for i in issues[:8]],
        "counts": {c: sum(1 for i in issues if i["category"] == c) for c in ("ui", "api", "a11y", "perf")},
    }
    prompt = prompts.render("summary", facts=prompts.untrusted("facts", json.dumps(facts, default=str), 4000))
    try:
        res = await ctx.llm.text("report", prompt, max_tokens=500, temperature=0.2)
    except LLMError as exc:
        ctx.degraded["report"] = f"{type(exc).__name__}: {exc}"
        return fallback, False
    text = res.text.strip()
    return (text, True) if len(text) > 40 else (fallback, False)


# ----------------------------------------------------------------------------- comparison
def compare_with_previous(project_id: int, run_id: int, fingerprints: set[str]) -> dict[str, Any]:
    with session_scope() as s:
        prev = s.exec(select(Run).where(Run.project_id == project_id, col(Run.id) < run_id,
                                        Run.status == "completed").order_by(col(Run.id).desc())).first()
        if prev is None:
            return {"previous_run_id": None}
        prev_f = s.exec(select(Finding).where(Finding.run_id == prev.id)).all()
        prev_fps = {f.fingerprint: f.title for f in prev_f if f.classification == "app_bug"}
        return {
            "previous_run_id": prev.id,
            "previous_score": prev.score,
            "new": len(fingerprints - set(prev_fps)),
            "fixed": len(set(prev_fps) - fingerprints),
            "persisting": len(fingerprints & set(prev_fps)),
            "fixed_titles": [prev_fps[f] for f in set(prev_fps) - fingerprints][:10],
        }


# ----------------------------------------------------------------------------- report data
def _rel(run_id: int, path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    parts = p.parts
    if str(run_id) in parts:
        idx = parts.index(str(run_id))
        return f"/artifacts/{run_id}/" + "/".join(parts[idx + 1:])
    return None


def report_data(run_id: int) -> dict[str, Any]:
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise KeyError(run_id)
        project = s.get(Project, run.project_id)
        findings = s.exec(select(Finding).where(Finding.run_id == run_id)).all()
        stages = s.exec(select(StageResult).where(StageResult.run_id == run_id).order_by(col(StageResult.order))).all()
        tests = s.exec(select(TestCase).where(TestCase.run_id == run_id)).all()
        results = {r.test_case_id: r for r in s.exec(select(TestResult).where(TestResult.run_id == run_id)).all()}
        journeys = {j.id: j for j in s.exec(select(Journey).where(Journey.run_id == run_id)).all()}
        pages = s.exec(select(Page).where(Page.run_id == run_id)).all()
        api = s.exec(select(ApiCheckResult).where(ApiCheckResult.run_id == run_id)).all()
        a11y = s.exec(select(A11yViolation).where(A11yViolation.run_id == run_id)).all()
        perf = s.exec(select(PerfMetric).where(PerfMetric.run_id == run_id)).all()
        calls = s.exec(select(LLMCall).where(LLMCall.run_id == run_id)).all()
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    fl = sorted(findings, key=lambda f: (f.status != "open", sev.get(f.severity, 9), f.category))
    ui_rows = []
    for t in tests:
        r = results.get(t.id or -1)
        j = journeys.get(t.journey_id)
        ui_rows.append({"name": t.name, "journey": j.name if j else t.name, "source": t.source,
                        "status": r.status if r else "not run", "error": (r.error or "")[:400] if r else "",
                        "screenshot": r.screenshot_path if r else None, "attempts": r.attempts if r else 0})
    return {
        "run": run, "project": project, "findings": fl, "stages": stages, "ui": ui_rows, "pages": pages,
        "api": api, "a11y": a11y, "perf": perf,
        "llm": {"calls": run.llm_calls, "cache_hits": run.cache_hits, "tokens_in": run.tokens_in,
                "tokens_out": run.tokens_out, "budget": run.llm_budget, "log_rows": len(calls)},
        "grade": scoring.grade(run.score), "weights": scoring.WEIGHTS,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def render_markdown(run_id: int) -> str:
    d = report_data(run_id)
    run, project = d["run"], d["project"]
    lines = [
        f"# OneClickQA report: {project.name if project else ''}",
        "",
        f"Target: {project.base_url if project else ''}  ",
        f"Run #{run.id} · {run.status} · generated {d['generated_at']}",
        "",
        f"## Quality score: {_fmt(run.score)}/100 (grade {d['grade']})",
        "",
        "| Category | Score | Weight |",
        "|---|---|---|",
        f"| UI flows | {_fmt(run.ui_score)} | 40 |",
        f"| API | {_fmt(run.api_score)} | 25 |",
        f"| Accessibility | {_fmt(run.a11y_score)} | 20 |",
        f"| Performance | {_fmt(run.perf_score)} | 15 |",
        "",
        "## Summary",
        "",
        (run.summary or "") + ("" if run.summary_ai else "\n\n_(Summary generated without AI.)_"),
        "",
    ]
    cmp = run.comparison or {}
    if cmp.get("previous_run_id"):
        lines += [f"Compared with run #{cmp['previous_run_id']}: {cmp.get('new', 0)} new issue(s), "
                  f"{cmp.get('fixed', 0)} fixed, {cmp.get('persisting', 0)} still open.", ""]
    open_f = [f for f in d["findings"] if f.status == "open"]
    lines += [f"## Findings ({len(open_f)} open)", ""]
    for f in d["findings"]:
        tag = " · AI-suggested, unverified" if f.ai_unverified else ""
        lines += [f"### [{f.severity.upper()}] {f.title}",
                  f"*{f.category} · {f.classification.replace('_', ' ')} · {f.status}{tag}*", "",
                  f.description or "", ""]
        if f.impact:
            lines += [f"**Impact:** {f.impact}", ""]
        if f.root_cause:
            lines += [f"**Likely cause:** {f.root_cause}", ""]
        if f.repro_steps:
            lines += ["**Steps to reproduce:**", ""] + [f"{n}. {s}" for n, s in enumerate(f.repro_steps, 1)] + [""]
        if f.suggested_fix:
            lines += [f"**Suggested fix:** {f.suggested_fix}", ""]
        ev = f.evidence or {}
        links = [f"[{k}]({_rel(run.id or 0, ev.get(k))})" for k in ("screenshot", "trace", "video")
                 if _rel(run.id or 0, ev.get(k))]
        if ev.get("help_url"):
            links.append(f"[axe rule docs]({ev['help_url']})")
        if links:
            lines += ["Evidence: " + " · ".join(links), ""]
    lines += ["## UI journeys", "", "| Journey | Script | Result |", "|---|---|---|"]
    lines += [f"| {u['journey']} | {u['source']} | {u['status']} |" for u in d["ui"]]
    lines += ["", "## Performance (median)", "", "| Page | TTFB | FCP | LCP | CLS |", "|---|---|---|---|---|"]
    for p in d["perf"]:
        lines.append(f"| {p.page_url} | {_fmt(p.ttfb)} ms | {_fmt(p.fcp)} ms | {_fmt(p.lcp)} ms | "
                     f"{p.cls if p.cls is not None else 'n/a'} |")
    llm = d["llm"]
    lines += ["", "## LLM usage", "", f"{llm['calls']} API call(s) of a {llm['budget']}-call budget · "
              f"{llm['cache_hits']} cache hit(s) · {llm['tokens_in']} tokens in / {llm['tokens_out']} out", "",
              "---", "Pass/fail verdicts come from Playwright assertions, axe-core, HTTP/schema checks and measured "
              "metrics. AI (Mistral) only planned tests, wrote code and explained findings.", ""]
    return "\n".join(lines)


def _embed(path: str | None, max_bytes: int = 450_000) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file() or p.stat().st_size > max_bytes:
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def render_html(run_id: int) -> str:
    d = report_data(run_id)
    shots = {f.id: _embed((f.evidence or {}).get("screenshot")) for f in d["findings"][:12]}
    body_md = md_lib.markdown(d["run"].summary or "")
    return _env.get_template("report.html.j2").render(**d, shots=shots, summary_html=body_md, fmt=_fmt,
                                                     rel=lambda p: _rel(d["run"].id or 0, p))


def render_pdf(html: str) -> bytes:
    """Print-to-PDF with headless Chromium (free). Runs the sync API in the calling thread."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda r: r.abort() if r.request.url.startswith("http") else r.continue_())
        page.set_content(html, wait_until="load")
        pdf = page.pdf(format="A4", print_background=True,
                       margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        browser.close()
    return pdf
