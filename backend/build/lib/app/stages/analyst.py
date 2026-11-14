"""Stage 8 – Analyze: turn deterministic results into deduplicated, explained findings.

1. collect_issues(): rule-based grouping of every failure into "issues" with deterministic
   default classification, severity and plain-English text (this alone is the fallback).
2. enrich(): the planner model classifies/explains in batches (≤ 20 issues per call).
   Guardrails: tool-verified categories (a11y/perf/api/crawl) keep classification `app_bug`
   (the LLM can only adjust severity and prose); only UI-test failures may be reclassified.
   Duplicates may only merge within the same category. Pass/fail never changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from app.core.context import RunContext
from app.llm import prompts
from app.llm.provider import LLMError
from app.llm.schemas import AnalysisResult
from app.security.secrets import mask_obj, mask_text

BATCH = 20
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
A11Y_SEVERITY = {"critical": "high", "serious": "medium", "moderate": "low", "minor": "low"}
RECLASSIFIABLE_KINDS = {"ui_test_failure", "flaky_test", "test_error", "ui_environment"}


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _norm_msg(msg: str) -> str:
    msg = re.sub(r"https?://[^\s'\"]+", "<url>", msg)
    return re.sub(r"\d+", "#", msg)[:160]


def _issue(kind: str, category: str, key: str, *, title: str, classification: str, severity: str,
           description: str, repro: list[str], fix: str, evidence: dict[str, Any], impact: str = "",
           root_cause: str = "") -> dict[str, Any]:
    return {"kind": kind, "category": category, "fingerprint": fingerprint(kind, key), "title": title,
            "classification": classification, "severity": severity, "description": description,
            "impact": impact, "root_cause": root_cause, "repro_steps": repro, "suggested_fix": fix,
            "evidence": evidence, "ai_enriched": False, "ai_unverified": False}


def collect_issues(ctx: RunContext) -> list[dict[str, Any]]:  # noqa: C901 - flat rule list
    issues: list[dict[str, Any]] = []
    base = ctx.base_url.rstrip("/")

    # --- UI tests -------------------------------------------------------------------------
    for tc, out in ctx.ui_results:
        j = tc.journey
        ev = {"journey": j.name, "journey_key": j.key, "error": (out.error or "")[:1200],
              "error_kind": out.error_kind, "attempts": out.attempts, "screenshot": out.screenshot_path,
              "trace": out.trace_path, "video": out.video_path, "script_source": tc.source,
              "start_url": j.start_url, "negative": j.negative}
        steps = [f"Open {base}{j.start_url}"] + j.steps[1:] if j.steps else [f"Open {base}{j.start_url}"]
        if out.status == "failed":
            sev = "critical" if (j.kind == "login" and j.negative) else "high"
            issues.append(_issue(
                "ui_test_failure", "ui", j.key, title=f"User journey failed: {j.name}", classification="app_bug",
                severity=sev, evidence=ev, repro=steps + [f"Expected: {'; '.join(j.expected)}"],
                description=f"The '{j.name}' test failed its assertion on both attempts. "
                            f"Expected: {'; '.join(j.expected)}. Error: {(out.error or '')[:300]}",
                impact=("Authentication can be bypassed with an incorrect password." if sev == "critical"
                        else "Users cannot complete this flow as expected."),
                fix="Check the behavior behind this flow against the expected outcome; see the trace and "
                    "screenshot for the exact failing step."))
        elif out.status == "flaky":
            issues.append(_issue(
                "flaky_test", "ui", j.key, title=f"Flaky journey: {j.name}", classification="flaky", severity="low",
                evidence=ev, repro=steps, description="The test failed once and passed on retry.",
                fix="Look for timing-dependent behavior (slow responses, animations, race conditions)."))
        elif out.status == "error":
            env = out.error_kind in ("environment", "timeout")
            issues.append(_issue(
                "ui_environment" if env else "test_error", "ui", j.key,
                title=f"{'Environment problem' if env else 'Generated test could not run'}: {j.name}",
                classification="environment" if env else "test_error", severity="low", evidence=ev, repro=steps,
                description=f"The test was inconclusive ({out.error_kind}): {(out.error or '')[:300]}",
                fix="No app change needed unless the page structure changed; the test will be regenerated."))

    # --- crawl: console errors, broken links, failed requests ---------------------------------
    console: dict[str, list[str]] = defaultdict(list)
    for p in ctx.pages:
        for e in p.console_errors:
            console[_norm_msg(e)].append(p.url)
    for norm, urls in console.items():
        raw = next(e for p in ctx.pages for e in p.console_errors if _norm_msg(e) == norm)
        issues.append(_issue(
            "console_error", "ui", norm, title=f"JavaScript error: {raw[:80]}", classification="app_bug",
            severity="medium", evidence={"message": raw, "pages": sorted(set(urls))[:10]},
            repro=[f"Open {sorted(set(urls))[0]}", "Open the browser developer console"],
            description=f"The browser reported a JavaScript error on {len(set(urls))} page(s): {raw[:300]}",
            impact="Scripts after the failing line do not run, so parts of the page may not work.",
            fix="Fix or guard the failing script call; make sure every function used on load is defined."))
    for p in ctx.pages:
        if (p.status_code or 0) >= 400 or p.load_error:
            what = f"HTTP {p.status_code}" if p.status_code else (p.load_error or "load error")
            issues.append(_issue(
                "broken_link", "ui", ctx.path_of(p.url), title=f"Broken link: {ctx.path_of(p.url)} ({what})",
                classification="app_bug", severity="high" if (p.status_code or 0) >= 500 else "medium",
                evidence={"url": p.url, "status": p.status_code, "linked_from": p.linked_from,
                          "error": p.load_error},
                repro=[f"Open {p.linked_from or base}", f"Follow the link to {p.url}"],
                description=f"A link on {p.linked_from or 'the site'} points to {p.url}, which returns {what}.",
                impact="Visitors following this link hit an error page.",
                fix="Update or remove the link, or add a redirect to the new location."))
    failed_req: dict[str, dict[str, Any]] = {}
    for p in ctx.pages:
        for f in p.network_failures:
            failed_req.setdefault(f["url"], {**f, "pages": []})["pages"].append(p.url)
    for url, f in failed_req.items():
        status = f.get("status")
        issues.append(_issue(
            "failed_request", "ui", ctx.path_of(url), title=f"Failed request: {ctx.path_of(url)} ({status or 'error'})",
            classification="app_bug", severity="high" if (status or 0) >= 500 else "medium",
            evidence={"url": url, "status": status, "error": f.get("error"), "pages": sorted(set(f["pages"]))[:10]},
            repro=[f"Open {f['pages'][0]}", "Watch the network panel"],
            description=f"The page requested {url}, which failed ({status or f.get('error')}).",
            fix="Fix the referenced resource/endpoint or remove the reference."))
    if ctx.login.attempted and not ctx.login.success:
        issues.append(_issue(
            "login_failed", "ui", "login", title="Could not sign in with the configured test account",
            classification="environment", severity="medium", evidence={"message": ctx.login.message,
                                                                         "login_url": ctx.login.login_url},
            repro=["Open the sign-in page", "Sign in with the configured account"],
            description=f"Automatic login failed: {ctx.login.message}. Pages behind login were not tested.",
            fix="Check the project's login settings (URL, username, password)."))

    # --- API ------------------------------------------------------------------------------------
    api_groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for c in ctx.api_checks:
        if c.status == "failed":
            api_groups[(c.method, c.path, c.check)].append(c)
    for (method, path, check), cs in api_groups.items():
        c = cs[0]
        sev = {"auth_required": "critical", "server_error": "high", "boundary": "high", "schema": "medium",
               "content_type": "low", "reachable": "medium", "spec": "low"}.get(check, "medium")
        label = {"boundary": "Server error on unexpected input", "server_error": "Server error on a valid request",
                 "schema": "Response does not match the API schema", "auth_required": "Endpoint is not protected",
                 "content_type": "Wrong content type", "reachable": "Endpoint unreachable",
                 "spec": "OpenAPI spec could not be loaded"}.get(check, check)
        issues.append(_issue(
            f"api_{check}", "api", f"{method} {path} {check}", title=f"{label}: {method} {path}",
            classification="app_bug", severity=sev,
            evidence={"method": method, "path": path, "check": check, "url": c.url, "status_code": c.status_code,
                      "messages": [x.message for x in cs][:5], "details": cs[0].details},
            repro=[f"Send {method} {c.url}", f"Observe HTTP {c.status_code}" if c.status_code else "Observe the error"],
            description="; ".join(sorted({x.message for x in cs}))[:600],
            impact={"boundary": "Unvalidated input crashes the server (possible denial of service).",
                    "server_error": "Clients get 5xx errors for valid requests.",
                    "schema": "Clients relying on the documented contract may break.",
                    "auth_required": "Data is exposed without authentication."}.get(check, ""),
            fix={"boundary": "Validate the parameter (e.g. reject ids <= 0 with 400/404) before using it.",
                 "server_error": "Check the server logs for the exception behind this endpoint.",
                 "schema": "Make the response match the documented schema (or update the spec).",
                 "auth_required": "Require authentication on this endpoint."}.get(check, "Investigate the endpoint.")))

    # --- accessibility -----------------------------------------------------------------------------
    rules: dict[str, dict[str, Any]] = {}
    for r in ctx.a11y_results:
        for v in r.violations:
            g = rules.setdefault(v["rule_id"], {"v": v, "pages": [], "nodes": 0})
            g["pages"].append(r.url)
            g["nodes"] += v.get("node_count", 0)
    for rule_id, g in rules.items():
        v = g["v"]
        issues.append(_issue(
            f"a11y_{rule_id}", "a11y", rule_id, title=f"Accessibility: {v.get('help') or rule_id}",
            classification="app_bug", severity=A11Y_SEVERITY.get(v.get("impact") or "minor", "low"),
            evidence={"rule_id": rule_id, "impact": v.get("impact"), "wcag": v.get("wcag_tags"),
                      "help_url": v.get("help_url"), "pages": sorted(set(g["pages"]))[:10],
                      "nodes": v.get("nodes", [])[:3], "node_count": g["nodes"]},
            repro=[f"Open {sorted(set(g['pages']))[0]}",
                   "Inspect: " + ", ".join(str(n.get("target")) for n in v.get("nodes", [])[:3])],
            description=f"{v.get('description', '')} ({g['nodes']} element(s) on {len(set(g['pages']))} page(s)).",
            impact="People using screen readers, keyboards or low vision may be unable to use this content.",
            fix=f"See {v.get('help_url')} for how to fix '{rule_id}'."))

    # --- performance ------------------------------------------------------------------------------
    for pp in ctx.perf_results:
        bad = {m: r for m, r in pp.ratings.items() if r != "good"}
        if not bad:
            continue
        sev = "medium" if "poor" in bad.values() else "low"
        parts = []
        for m in bad:
            val = pp.metrics.get(m)
            parts.append(f"{m.upper()} {val:.3f}" if m == "cls" else f"{m.upper()} {val:.0f} ms" if val is not None
                         else m.upper())
        issues.append(_issue(
            "perf_page", "perf", ctx.path_of(pp.url), title=f"Slow or unstable page: {ctx.path_of(pp.url)}",
            classification="app_bug", severity=sev,
            evidence={"url": pp.url, "metrics": pp.metrics, "ratings": pp.ratings, "samples": len(pp.samples)},
            repro=[f"Open {pp.url} with a cold cache", "Measure with the browser performance panel"],
            description=f"Median of {len(pp.samples)} loads: " + ", ".join(parts) + " (Core Web Vitals thresholds).",
            impact="Slow or jumping pages frustrate users and hurt search ranking.",
            fix=("Reserve space for late-inserted content (banners, images, ads) to avoid layout shift."
                 if "cls" in bad else "Speed up the server response (caching, slow queries) and reduce render-"
                                      "blocking work.")))

    # --- vision (feature-flagged, never verified) ---------------------------------------------------
    for v in ctx.vision_issues:
        i = _issue("vision", "ui", f"{v['url']}|{v['title']}", title=f"AI-suggested: {v['title']}",
                   classification="app_bug", severity=v.get("severity", "low"),
                   evidence={"url": v["url"], "screenshot": v.get("screenshot")}, repro=[f"Open {v['url']}"],
                   description=v.get("description", ""), fix="Verify manually; this was suggested by a vision model.")
        i["ai_unverified"] = True
        issues.append(i)

    for n, i in enumerate(issues, start=1):
        i["id"] = f"I{n}"
    return [mask_obj(i, ctx.secrets) for i in issues]


def _bucket(metric: str, v: Any) -> Any:
    """Coarse values for the LLM so run-to-run timing noise doesn't defeat the prompt cache."""
    if not isinstance(v, int | float):
        return v
    if metric == "cls":
        return round(round(v / 0.05) * 0.05, 2)
    if metric in ("transfer_size", "request_count"):
        return v
    return f"~{round(v / 500) * 0.5:.1f}s"


def _llm_view(i: dict[str, Any]) -> dict[str, Any]:
    ev = {k: v for k, v in i["evidence"].items() if k not in ("screenshot", "trace", "video", "samples")}
    if isinstance(ev.get("metrics"), dict):
        ev["metrics"] = {k: _bucket(k, v) for k, v in ev["metrics"].items() if k in ("ttfb", "fcp", "lcp", "cls")}
    if "error" in ev and isinstance(ev["error"], str):
        ev["error"] = re.sub(r"\d+(\.\d+)?ms", "<n>ms", ev["error"])
    text = json.dumps(ev, ensure_ascii=False, default=str)[:900]
    return {"issue_id": i["id"], "category": i["category"], "kind": i["kind"], "default_title": i["title"],
            "default_severity": i["severity"], "retry_passed": i["kind"] == "flaky_test",
            "error_kind": i["evidence"].get("error_kind"), "evidence": prompts.untrusted(i["id"], text, 1200)}


async def enrich(ctx: RunContext, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {i["id"]: i for i in issues}
    for start in range(0, len(issues), BATCH):
        batch = issues[start:start + BATCH]
        prompt = prompts.render("analyst", base_url=ctx.base_url,
                                issues=json.dumps([_llm_view(i) for i in batch], ensure_ascii=False))
        try:
            result = await ctx.llm.structured("analyze", prompt, AnalysisResult, max_tokens=4000)
        except LLMError as exc:
            ctx.degraded["analyze"] = f"{type(exc).__name__}: {exc}"
            break
        for a in result.issues:
            i = by_id.get(a.issue_id)
            if i is None or i["ai_unverified"]:
                continue
            if i["kind"] in RECLASSIFIABLE_KINDS:
                i["classification"] = a.classification
            i["severity"] = a.severity
            i["title"] = mask_text(a.title, ctx.secrets)
            i["impact"] = mask_text(a.impact, ctx.secrets) or i["impact"]
            i["root_cause"] = mask_text(a.root_cause, ctx.secrets)
            if a.repro_steps:
                i["repro_steps"] = [mask_text(s, ctx.secrets) for s in a.repro_steps]
            i["suggested_fix"] = mask_text(a.suggested_fix, ctx.secrets) or i["suggested_fix"]
            i["ai_enriched"] = True
            dup = by_id.get(a.duplicate_of or "")
            if dup is not None and dup is not i and dup["category"] == i["category"] and not i.get("merged_into"):
                i["merged_into"] = dup["id"]
    # merge duplicates
    final = []
    for i in issues:
        target = by_id.get(i.get("merged_into", ""))
        if target is not None and not target.get("merged_into"):
            target["evidence"].setdefault("duplicates", []).append({"title": i["title"], "evidence": i["evidence"]})
            if SEV_ORDER[i["severity"]] < SEV_ORDER[target["severity"]]:
                target["severity"] = i["severity"]
            continue
        final.append(i)
    return final


async def analyze(ctx: RunContext) -> list[dict[str, Any]]:
    issues = collect_issues(ctx)
    if issues and ctx.llm.available:
        issues = await enrich(ctx, issues)
    elif issues:
        ctx.degraded["analyze"] = "LLM unavailable: deterministic classification and text"
    return sorted(issues, key=lambda i: (SEV_ORDER[i["severity"]], i["category"], i["title"]))
