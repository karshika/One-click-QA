# Assumptions and decisions

Recorded while building. Each item is a judgment call made without asking.

## Environment
* The build machine had no Python, Git or Docker. Python 3.12 and Git were installed in user
  scope with winget. Docker Desktop needs admin rights and WSL, so it was **not** installed.
  The `docker-compose.yml` and Dockerfiles are written to the same layout as the verified
  local commands, but `docker compose up` could not be executed on this machine. All
  verification used the documented local commands (`scripts/dev.ps1` / `scripts/dev.sh`).
* No `MISTRAL_API_KEY` was available while building, so no real Mistral call has been made.
  Tests cover the LLM layer in two ways:
  * the HTTP provider is tested with `respx` (mocked `/v1/chat/completions`: JSON mode
    body, 429 + Retry-After, 5xx, 401);
  * the full pipeline runs with `MockMistral`, which replays recorded answers from
    `backend/tests/fixtures/mistral/*.json`.

  Acceptance runs were done in deterministic mode (no key) and mock mode. Both gave 10/10
  detected and 0 false positives. The real e2e test (`pytest -m e2e`) and
  `scripts/acceptance.py` with a key are ready but were not executed. Real model output may
  differ from the fixtures. The deterministic baseline (login negative tests, a11y, perf,
  API and crawl checks) already covers every planted bug on its own, so detection does not
  depend on model quality.

## Mistral (checked against docs.mistral.ai, Nov 2026)
* Model overview lists Mistral Large 4 (v26.10, multimodal), Mistral Medium 3.5, Mistral Small 4
  (v26.03), Codestral 25.08 and Ministral 3. The API reference examples use the
  `mistral-large-latest` / `mistral-small-latest` aliases. Defaults:
  * planner: `mistral-large-latest` (strongest general model; Magistral entries are all
    deprecated)
  * code: `codestral-latest`
  * fast: `mistral-small-latest`
  * vision: `mistral-large-latest` (Large 4 is multimodal; `ministral-8b-latest` is a
    cheaper option)

  All of these are env-configurable.
* JSON mode is `response_format: {"type": "json_object"}`, and the docs say the prompt
  must also ask for JSON. `json_schema` mode exists, but its sub-fields could not be
  confirmed from the docs, so the client uses JSON mode plus pydantic validation plus one
  repair retry. Strict schema mode is supported in the provider behind the
  `use_json_schema` flag (off by default).
* Free-tier rate-limit numbers were not on the docs pages fetched (the tier page 404'd). The
  task statement says about 1 request/second, so the defaults are `MISTRAL_RPS=1` and a
  conservative `MISTRAL_TPM=400000`. Both can be configured.
* Codestral is reached through the same `/v1/chat/completions` endpoint with the normal key.
  If your plan has no Codestral access, set `MISTRAL_MODEL_CODE=mistral-large-latest`.

## Product decisions
* **API testing**: a custom OpenAPI-driven generator (httpx + jsonschema) instead of
  schemathesis. Schemathesis' Hypothesis-based fuzzing is non-deterministic, sends many
  requests (bad for "don't hammer the target"), and its in-process API changed heavily
  between v3 and v4. The custom generator is deterministic, GET-only in safe mode, and
  covers the required checks: status codes, response schema, content type, 5xx on valid
  input, boundary/negative inputs, and 401/403 on auth-required endpoints without
  credentials. `schemathesis` remains an optional extra (`pip install .[schemathesis]`)
  for manual deep fuzzing.
* **Deterministic UI fallback**: with no key, or once the budget runs out, journeys and
  tests come from templates built from the site map:
  * page smoke tests
  * login positive and negative tests (wrong password, case-changed password, empty
    password)
  * form submission

  This is the "closest deterministic fallback".
* **Credentials and the LLM**: generated tests use `ctx.username` / `ctx.password`. The
  real values never appear in prompts; they are injected into the sandbox at runtime.
* **Restart semantics**: a run interrupted by a process restart is re-queued and restarts
  from scratch. Partial stage resume adds complexity for little value with runs under 10
  minutes.
* **a11y scope**: axe runs with WCAG 2.0/2.1/2.2 A and AA tags only. Best-practice rules
  such as `region` are excluded by default, because they create noise and false positives
  for small apps.
* **Fernet key**: read from `OCQA_SECRET_KEY`. If it is unset, a key is generated once into
  `$OCQA_DATA_DIR/.secret_key` (dev convenience), with a logged warning.
* **Sandbox resource limits**: on Linux (Docker), test subprocesses get `RLIMIT_CPU`,
  `RLIMIT_FSIZE` and `RLIMIT_NOFILE` limits. On Windows only the wall-clock timeout and a
  process-tree kill apply.
* **Worker concurrency**: the worker runs one run at a time globally, not just per
  project. Browsers are heavy and the LLM rate limit is shared anyway. The API also
  returns 409 if a project already has an active run.
* **Bug-claim counting**: for acceptance, only findings classified `app_bug` count as
  claims about the app. `test_error`, `flaky` and `environment` items are listed separately
  in `docs/ACCEPTANCE-*.md`. Classifications of tool-verified findings are locked to
  `app_bug`, so the LLM cannot hide them.
* **Perf findings**: a page gets a finding if any Core Web Vital is not "good".
  "Needs-improvement" is low severity and "poor" is medium.
* **Analyst prompt stability**: perf timings sent to the analyst are bucketed (0.5 s steps;
  CLS in 0.05 steps) so that run-to-run noise doesn't break the prompt cache. The
  deterministic description keeps exact values.
* **PerfMetric column `cls_score`**: a column named `cls` collides with SQLModel internals,
  hence the different name.
* **Finding status carry-over**: if a finding fingerprint was marked `ignored` in an earlier
  run of the same project, new runs keep it `ignored`.
