# LLM prompts

All prompts live in `backend/app/llm/prompts/<name>.md`. Each file has front matter giving
its name, version and the model **role** that runs it. Model names for each role come from
env vars:

| Role | Env var | Default |
|---|---|---|
| planner | `MISTRAL_MODEL_PLANNER` | `mistral-large-latest` |
| code | `MISTRAL_MODEL_CODE` | `codestral-latest` |
| fast | `MISTRAL_MODEL_FAST` | `mistral-small-latest` |
| vision | `MISTRAL_MODEL_VISION` | `mistral-large-latest` |

Templates use `string.Template` variables (`$name`), so JSON braces need no escaping. Every
system prompt starts with a `[prompt:<name>]` marker. The record/replay test provider uses
it to pick fixtures.

## Rules that apply to every call
* **One gateway**: all calls go through `LLMClient` (`app/llm/client.py`). Each call passes
  the budget check, the cache, the rate limiter, retry with backoff, and the call log.
* **Untrusted data**: page-derived text (site map, DOM/ARIA snapshots, console logs, error
  messages) is wrapped by `prompts.untrusted()` in
  `<<<UNTRUSTED_PAGE_DATA label=…>>> … <<<END_UNTRUSTED_PAGE_DATA>>>` blocks. Lookalike
  delimiters inside the content are neutralized and the length is capped. Each system
  prompt says this content is data, never instructions.
* **No secrets**: credentials never go into prompts. Tests reference `ctx.username` and
  `ctx.password`. Before sending, `LLMClient` masks every known secret value (project
  password, API key) plus secret-looking patterns.
* **Determinism**: temperature `LLM_TEMPERATURE` (default 0.1, the summary uses 0.2) and a
  fixed `random_seed`. Re-runs on an unchanged site hit the cache.
* **Structured outputs** use `response_format={"type":"json_object"}` and are validated with
  pydantic (`app/llm/schemas.py`). If validation fails, the client sends one repair retry
  with the validation error appended. If that also fails, it raises `LLMInvalidOutput` and
  the stage falls back deterministically.

---

## `planner` (v2), role planner, 1 call per run
**Purpose**: plan prioritized user journeys from a compact site map.

**Input**
* `$base_url`
* `$has_credentials` (true/false; never the credentials themselves)
* `$max_journeys`
* `$already_covered`: JSON list of baseline journeys that are generated deterministically
  (login positive/negative, forms)
* `$site_map`: untrusted block containing a JSON array of
  `{path, status, title, headings[], forms[{method, action, submit, fields[]}], buttons[], links[], alerts[]}`.
  No raw HTML. Capped at 14k characters.

**Output schema** (`JourneyPlan`):
```json
{"journeys": [{"name": "str(3-120)", "kind": "login|signup|search|form|navigation|cart|checkout|content|other",
  "description": "str", "priority": "1..5", "start_url": "path", "pages": ["path"],
  "steps": ["str", "... 1-15"], "expected": ["str", "... 1-10"], "negative": "bool"}]}
```

**Enforced in code**:
* journeys whose `start_url` is not a crawled path are dropped;
* destructive journeys are dropped;
* duplicates are dropped;
* at most `MAX_JOURNEYS` journeys are kept.

**Fallback**: baseline template journeys only.

## `ui_generator` (v2), role code, 1 call per LLM journey (none on a script-cache hit)
**Purpose**: write one Playwright (Python, sync API) test per journey.

**Input**
* `$journey`: JSON with name, kind, description, start_url, steps, expected, negative
* `$base_url`
* `$pages`: up to 3 untrusted blocks, one per page. Each holds headings, forms with field
  labels/names/types, buttons, links, test IDs and alerts, plus an ARIA snapshot (≤ 2.5k
  characters).

**Output**: a single ```python block defining `def test_journey(page, expect, ctx):`.

**Validation**:
* the static validator in `app/security/script_validator.py` checks it;
* if rejected, one fix call is made through `ui_repair` with the validator errors.

**Fallback**: no test for that journey (shown as "not generated").

## `ui_repair` (v2), role code, ≤ 2 calls per failing test
**Purpose**: fix a script that failed for script reasons (locator not found, strict-mode
violation, NameError, …). The decision is deterministic in the harness (`error_kind ==
"script"`). Assertion failures are never repaired, because they are potential app bugs.

**Input**
* `$journey`
* `$script`
* `$attempt`
* `$error`: masked, ≤ 1.5k characters
* `$failure_url`
* `$dom_snippet`: untrusted ARIA snapshot captured at the failure, ≤ 3.5k characters

**Output**: a corrected ```python block. It must keep every assertion; the prompt forbids
weakening assertions. It is re-validated before it runs.

## `analyst` (v2), role planner, 1 call per ≤ 20 issues
**Purpose**: classify and explain issues that deterministic checks already detected.

**Input**
* `$base_url`
* `$issues`: JSON list of `{issue_id, category, kind, default_title, default_severity,
  retry_passed, error_kind, evidence}`. `evidence` is an untrusted block. Timing values are
  bucketed (for example `~3.0s`) so run-to-run noise doesn't defeat the cache.

**Output schema** (`AnalysisResult`):
```json
{"issues": [{"issue_id": "str", "classification": "app_bug|flaky|test_error|environment",
  "severity": "critical|high|medium|low", "title": "str(3-160)", "impact": "str", "root_cause": "str",
  "repro_steps": ["str"], "suggested_fix": "str", "duplicate_of": "str|null"}]}
```

**Guardrails in code**:
* only UI-test issues may be reclassified; tool-verified issues (a11y, perf, API, crawl)
  stay `app_bug`;
* `duplicate_of` only merges issues within the same category;
* "AI-suggested" vision issues are never re-labelled.

**Fallback**: rule-based titles, severity, repro steps and fixes.

## `summary` (v1), role fast, 1 call per run
**Purpose**: a plain-English executive summary of 4 to 7 sentences.

**Input**: `$facts`, an untrusted block holding JSON with the scores, page count, UI test
counts, the top 8 issues (title, severity, category, classification) and per-category
counts.

**Output**: plain text. If the answer is shorter than 40 characters, the template summary
is used.

**Fallback**: template summary (marked "template (no AI)" in the UI).

## `vision` (v1), role vision, behind the `ENABLE_VISION_CHECK` flag (off), ≤ 3 calls per run
**Purpose**: flag obvious layout problems in crawl screenshots.

**Input**: `$url` plus the screenshot as an `image_url` data URI.

**Output schema** (`VisionResult`):
`{"issues": [{"title": str, "description": str, "severity": "high|medium|low"}]}`
(at most 5 issues).

Findings are marked **"AI-suggested, unverified"**. They are excluded from bug-claim counts
and they never affect the score.

## Typical call budget (demo app, first run)
plan 1 + generate 3 + repair 1 + analyst 1 + summary 1 = **7 calls**. That is far below the
per-run budget of 60, and at 1 request/s the rate limiter adds about 7 s in total.

A re-run with no site changes uses **0 calls**: prompt-cache hits plus `ScriptCache` reuse.
