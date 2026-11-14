# Progress

## Done
- [x] **M1 Skeleton**: repo layout, env config, SQLModel models + forward-only migrations, FastAPI `/health`,
  Next.js 16 shell (Tailwind v4, light/dark), Dockerfiles, docker-compose, GitHub Actions CI.
- [x] **M2 LLM layer**:
  - `LLMProvider` interface and `MistralProvider` (httpx; JSON mode; optional json_schema)
  - process-wide token bucket (RPS) + tokens-per-minute window
  - exponential backoff with full jitter that honours Retry-After
  - prompt-hash cache (DB), per-run/per-day budget
  - pydantic validation + one repair retry
  - `LLMCall` log, secret masking
- [x] **M3 Demo app**: ShopLite with 10 planted bugs, `EXPECTED_BUGS.md` + machine-readable matchers.
- [x] **M4 Crawler**:
  - same-origin BFS with page/depth limits, politeness delay and destructive-link filter
  - generic login, network/SSRF route guard
  - per page: DOM summary, ARIA snapshot, screenshot, console/page errors, failed requests, XHR/fetch log
- [x] **M5 Planner + generator + runner**:
  - LLM journeys with scope enforcement, plus deterministic baseline journeys
  - code-model test generation, static validator, sandboxed subprocess harness (trace/screenshot/video)
  - flakiness retry, ≤ 2 self-repairs, `ScriptCache`
- [x] **M6 API / a11y / perf**:
  - OpenAPI-driven checks (status, schema, content type, auth, boundary) and observed-traffic replay
  - axe-core WCAG A/AA checks
  - Core Web Vitals, median of N cold loads
- [x] **M7 Analyst + reporter**:
  - rule-based issue grouping, guarded LLM enrichment, deterministic scoring
  - summary with template fallback, run comparison
  - Markdown / standalone HTML / PDF (Chromium print) exports
- [x] **M8 Orchestrator**:
  - per-stage fault isolation, `degraded` status for fallbacks
  - cancellation, DB-backed worker that survives restarts
  - SSE (persisted events, replay on reconnect), full REST API, acceptance evaluator
- [x] **M9 Dashboard**:
  - projects page with consent, project page (Run QA, trends, comparison, history)
  - run page (live stepper, score cards, 7 tabs, charts, exports), settings page
  - axe-clean in light and dark (`scripts/ui_smoke.py`)
- [x] **M10 Hardening + docs**:
  - cancellation/restart/fault-isolation tests, real e2e test (skipped without a key)
  - `scripts/acceptance.py`
  - README, ARCHITECTURE, PROMPTS, SECURITY, ASSUMPTIONS

## Verified
- Backend: ruff clean, mypy clean, pytest suite green (Mistral mocked; the real e2e test is skipped without a key).
- Frontend: ESLint, `tsc` and Vitest clean; `next build` succeeds.
- Acceptance against ShopLite:
  - deterministic: 10/10 detected, 0 false positives, 0 LLM calls
  - mocked Mistral: 10/10 detected, 0 false positives, 7 LLM calls on the first run, then 0 calls and 3 cache hits on the re-run
- End to end through the real UI (`scripts/ui_smoke.py`): create project → Run QA → live progress → all tabs → exports.

- GitHub Actions (https://github.com/karshika/One-click-QA/actions) is green on commit `94acc5b`.
  The `backend` job (ruff, mypy, pytest) and the `frontend` job (eslint, tsc, vitest, build) pass.
  The `docker` job passes. It runs `docker compose up --build`, adds the demo app, completes a
  QA run with no failed stage and at least 8 findings, and downloads the PDF report.
- Fixes needed on the way to green CI:
  - `pytest` `pythonpath`, so `tests.fakes` imports when `pytest` is called directly
  - `libatomic1` in the backend image, required by Playwright's Node driver

## Known issues / next
- Docker is verified on GitHub's Linux runners only. The build machine has no Docker.
- No real Mistral run yet. Run `python scripts/acceptance.py` with a key to produce `docs/ACCEPTANCE.md`.
- Ideas:
  - POST/PUT API checks with generated bodies (opt-in)
  - multi-step/SSO login support
  - Lighthouse integration when the CLI is installed
  - a Postgres + external queue option for multi-user deployments
