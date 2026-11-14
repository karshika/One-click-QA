# OneClickQA architecture

OneClickQA is a single-process FastAPI backend with an in-process job worker, a
Next.js dashboard, and a SQLite database (Postgres-compatible schema). One "run" pushes
a target web app through nine pipeline stages and produces a deterministic quality
score plus an AI-written (or deterministic fallback) bug report.

```
 Browser (Next.js dashboard :3000)
    │  REST (JSON)            ▲ SSE /runs/{id}/events (polls RunEvent rows)
    ▼                         │
 FastAPI (:8000) ── app/api/routes.py
    │  POST /projects/{id}/runs → Run(status=queued) row
    ▼
 Worker thread (app/core/worker.py)  ── own asyncio loop (Proactor on Windows → Playwright OK)
    │  picks queued runs, one run at a time per project, survives restarts (re-queues)
    ▼
 Orchestrator (app/core/orchestrator.py)
    crawl → plan → generate → run → api → a11y → perf → analyze → report
    each stage: try/except isolation, StageResult row (status, duration, llm_calls, error),
    events emitted, cancellation checked between stages and inside long loops
    │
    ├── Playwright (Chromium)          crawler, a11y (axe-core), perf, PDF export
    ├── Test subprocesses              generated scripts run in `python -I harness.py` sandbox
    ├── httpx (SSRF-guarded)           API checks, OpenAPI spec fetch
    └── LLM layer (app/llm)            Mistral behind LLMProvider
            client.py  : budget → cache → rate limiter → retry → provider → validate → log
            limiter.py : token bucket (RPS) + tokens/minute window, process-wide
            cache.py   : sha256(model+messages+params) → LLMCache table
            schemas.py : pydantic models for every structured output
            prompts/   : versioned prompt templates (see docs/PROMPTS.md)
 SQLite (data/oneclickqa.db) + artifacts (data/artifacts/<run>/...: screenshots, traces, videos, reports)
```

## Pipeline stages

| # | Stage | Module | LLM | Deterministic output | Fallback when LLM unavailable / budget exhausted |
|---|-------|--------|-----|-----------------------|-------------------------------------------------|
| 1 | Crawl | `stages/crawler.py` | – | Page rows: title, DOM summary, a11y tree, forms, links, screenshot, console errors, failed requests, XHR/fetch log | – |
| 2 | Plan | `stages/planner.py` | planner model, 1 call | Journey rows | Template journeys from the site map (smoke, login positive and negative, forms, navigation) |
| 3 | Generate | `stages/ui_generator.py` | code model, 1 call per journey | TestCase rows (validated) | Template Playwright scripts; reuse of cached passing scripts (0 calls) |
| 4 | Run | `stages/ui_runner.py` | code model for repair (≤2 per test) | TestResult rows, trace/screenshot/video on failure, retry ×1 for flakiness | No repair; failure stays as is |
| 5 | API | `stages/api_checks.py` | – | ApiCheckResult rows (OpenAPI-driven or observed-traffic replay) | – |
| 6 | A11y | `stages/a11y_checks.py` | – | A11yViolation rows (axe-core, WCAG 2.x A/AA tags) | – |
| 7 | Perf | `stages/perf_checks.py` | – | PerfMetric rows (median of 3 loads) | – |
| 8 | Analyze | `stages/analyst.py` | planner model, 1 call per ≤20 issues | Finding rows: rule-based grouping, classification, severity | Rule-based titles/descriptions/repro/fix text |
| 9 | Report | `stages/reporter.py` | fast model, 1 call | Score, summary, Markdown/HTML (PDF via Chromium print) | Template summary |
| – | Vision (flag) | `stages/vision.py` | vision model | "AI-suggested, unverified" findings | Skipped |

A typical first run against the demo app uses about 10 to 20 API calls, well under the
per-run budget of 60. A re-run against an unchanged site uses 0 API calls: the planner,
generator, repair and analyst prompts are identical, so they hit the cache, and passing
scripts are reused from `ScriptCache`.

## Verdicts are deterministic

* UI pass/fail = Playwright assertions inside the generated test (exit status of the sandbox).
* API pass/fail = HTTP status / JSON-schema validation / content type / latency rules.
* A11y = axe-core results. Perf = thresholds (web.dev Core Web Vitals).
* The LLM only plans, writes code, classifies failures (app_bug/flaky/test_error/environment),
  assigns severity and writes prose. It can never flip a pass into a fail or vice versa. The
  score uses only deterministic data.

## Quality score (0–100)

Weights: UI 40, API 25, Accessibility 20, Performance 15. If a category didn't produce data
(e.g. no API endpoints found), its weight is redistributed proportionally over the others.

* **UI** = 100 × passed / (passed + failed), counting flaky as 0.5 passed. Tests that end as
  *script errors* after all repairs (locator never resolved, syntax/name errors) are
  inconclusive and excluded. Then subtract 5 per page with JS console errors and 5 per
  broken link or page load ≥ 400 (cap −40). Clamped to [0, 100].
* **API** = 100 × passed checks / (passed + failed checks). Warnings don't count.
* **Accessibility** = mean over pages of `max(0, 100 − Σ rule penalties)`, with penalties
  critical 15, serious 8, moderate 3, minor 1 per violated rule (not per node).
* **Performance** = mean over pages of the mean metric rating, where good = 100,
  needs-improvement = 50 and poor = 0. Metrics are LCP (2.5 s / 4 s), FCP (1.8 s / 3 s),
  CLS (0.1 / 0.25) and TTFB (0.8 s / 1.8 s).

Implemented in `app/core/scoring.py`, unit-tested in `tests/test_scoring.py`.

## LLM layer (free-tier survival)

`LLMClient.structured()` / `.text()` order of operations:

1. **Budget**: per-run counter (`MAX_LLM_CALLS_PER_RUN`) and per-day count from the
   `LLMCall` table (`MAX_LLM_CALLS_PER_DAY`). If either is exhausted, it raises
   `BudgetExhausted` and the stage uses its deterministic fallback.
2. **Cache**: the key is `sha256(model, messages, temperature, max_tokens, response_format)`.
   A cache hit costs no API call or budget, and it is logged with `cache_hit=true`.
3. **Rate limiter**: a process-wide token bucket (`MISTRAL_RPS`, default 1) plus a sliding
   60 s window of estimated tokens (`MISTRAL_TPM`).
4. **Retry**: on HTTP 429 and 5xx, timeouts and connection errors, it uses exponential
   backoff `min(60, 2^n)` with full jitter and honours `Retry-After`. It stops after
   `MISTRAL_MAX_RETRIES` attempts and stores a clear error on the stage.
5. **Structured output**: uses `response_format={"type":"json_object"}` and validates the
   result with pydantic. If validation fails, it sends one repair retry with the
   validation errors appended. If that also fails, it raises `LLMInvalidOutput` and that
   item falls back.
6. **Logging**: an `LLMCall` row for every call (stage, model, prompt name/version/hash,
   tokens, latency, cache hit, status). The API key is never logged or stored.

## Prompt-injection posture

Page text is untrusted. Prompts wrap it in `<<<UNTRUSTED_PAGE_DATA ... >>>` blocks, and the
system prompts state that content inside those blocks is data and never instructions. The
crawler strips scripts and styles and caps text length. The scope of the test plan is
enforced in code, not by the model:

* journeys may only reference crawled URLs;
* scripts must pass the validator;
* the runner's network guard blocks other hosts;
* verdicts come from deterministic checks.

So even a successful injection can at most produce a bad test or wrong prose. It cannot
change pass/fail results or reach other hosts.

## Data model

See `app/db/models.py`: `Project → Run → {StageResult, RunEvent, Page, Journey, TestCase, TestResult,
ApiCheckResult, A11yViolation, PerfMetric, Finding, LLMCall}`, plus `ScriptCache` (per project) and
`LLMCache` (global). Migrations: forward-only list in `app/db/session.py` with a `SchemaVersion`
table.

## Runs, restarts and cancellation

Runs are rows. On startup, the worker re-queues runs that were `running` when the process
died, and they restart from the crawl (stage results are reset). `POST /runs/{id}/cancel`
sets `cancel_requested`. The orchestrator checks the flag between stages and inside the
crawl, test, perf and a11y loops, then marks the remaining stages as `cancelled`.
