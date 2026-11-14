# Security and threat model

OneClickQA does three risky things. It points a browser at a URL a user typed in. It runs
code written by an LLM. It stores login credentials for the target app. This document lists
the threats and the controls that address each one.

## Assets
* Host machine / container: file system, network position (internal services, cloud
  metadata)
* Stored target-app credentials (`Project.auth_encrypted`)
* `MISTRAL_API_KEY` and `OCQA_SECRET_KEY`
* Integrity of verdicts: pass/fail results and the score

## Threats and controls

### T1: SSRF: using OneClickQA to reach internal services
* `app/security/ssrf.py::check_url` allows only `http`/`https`, rejects credentials embedded
  in URLs, resolves DNS, and blocks private, loopback, link-local (including
  `169.254.169.254`), CGNAT, multicast, reserved and unspecified addresses, plus
  IPv4-mapped IPv6. A host is blocked if **any** of its resolved addresses is forbidden.
* Applied to:
  * project `base_url` and `openapi_url` on create and update (HTTP 400);
  * every httpx request in the API checks, including **every redirect hop** (request event
    hook on a client with `follow_redirects`);
  * every browser request during crawl, a11y and perf (Playwright `context.route` guard;
    redirects produce new requests that the guard re-checks);
  * generated tests (strict host allowlist in the harness).
* `ALLOW_LOCAL_TARGETS=true` disables the private-IP check for local development and the
  docker-compose demo. It is off by default.
* Navigation is same-origin by default. Other domains need `EXTRA_ALLOWED_DOMAINS` or the
  project setting `extra_domains`.
* **Residual risk**: DNS rebinding between the check and the browser's own resolution. To
  reduce it, run the backend in a network namespace or container without access to
  sensitive internal networks (the docker-compose network contains only the demo app).

### T2: Malicious or hijacked LLM-generated test code
Defense in depth, all enforced in code:
1. **Static validation** (`app/security/script_validator.py`), allowlist-based and fail-closed:
   * only `import re` and `from playwright.sync_api import expect/Page/Locator`;
   * a single `test_journey(page, expect, ctx)` entry point;
   * no `try/except` (a test may not swallow failures), classes, lambdas, async,
     global/nonlocal, decorators, dunder or private attributes, `eval`, `exec`, `open`,
     `__import__`, `getattr`, `os`, `sys` or `subprocess`;
   * blocked attributes: `page.context`, `.request`, `.evaluate`, `.route`, `.screenshot`,
     `set_input_files`, `add_init_script`, …;
   * every call must target an allowlisted Playwright, `str` or `re` method or builtin;
   * absolute URL literals must point at the target host;
   * `file:`, `javascript:` and `data:` URLs are rejected.
2. **Subprocess sandbox** (`app/stages/harness/harness.py`, run by `ui_runner.py`):
   * runs as `python -I` in a fresh temp working directory with a scrubbed environment (no
     `MISTRAL_API_KEY`, no `OCQA_SECRET_KEY`, `TEMP` pointed at the sandbox dir);
   * restricted `__builtins__`, an import hook allowing only `re` and `playwright.sync_api`,
     and `__build_class__` disabled;
   * wall-clock timeout (`TEST_TIMEOUT_S`) followed by a process-tree kill;
   * on Linux, `RLIMIT_CPU`, `RLIMIT_FSIZE` and a new session;
   * credentials arrive on **stdin**, never in argv or on disk.
3. **Network**: the harness browser context aborts every request whose host is not the target
   host or an allowlisted extra domain.
4. **Verdict integrity**: pass/fail is the harness's exception status. The LLM never decides
   it. Repair prompts forbid weakening assertions, and repairs only happen for
   deterministic `script` errors, never for assertion failures.

Tests: `tests/test_security.py` checks that dangerous scripts are rejected.
`tests/test_ui_pipeline.py::test_sandbox_blocks_escape_attempts` checks that the runtime
blocks dynamically built foreign URLs, `open()` and `import os` even without the validator.

* **Residual risk**: the restricted-builtins sandbox is not a security boundary against a
  determined attacker who controls the code, since Python introspection escapes exist.
  That is why the static validator blocks introspection primitives (dunder access, `type`,
  `getattr`, …), and why production deployments should run the backend container as an
  unprivileged user (the Dockerfile does) without sensitive mounts.

### T3: Prompt injection from page content
* Page-derived text is wrapped in delimited untrusted-data blocks; delimiter lookalikes are
  neutralized; the system prompts say "data, never instructions".
* Scope is enforced in code rather than by the prompt:
  * journeys must start on crawled same-site paths (`planner._sanitize`), so the
    "Exfiltrate data" journey in the test fixtures is dropped;
  * destructive journeys are dropped;
  * scripts are validated and network-limited;
  * analyst classifications of tool-verified issues are locked;
  * duplicates can only merge within a category.
* The LLM has no tools: it cannot trigger actions, only return text and JSON that code
  validates.

### T4: Credential and secret leakage
* Target-app credentials are encrypted with Fernet (`OCQA_SECRET_KEY`; if unset, a key is
  generated once into `$OCQA_DATA_DIR/.secret_key`). The API never returns the password,
  only `has_credentials` and `username`.
* Credentials are never sent to the LLM. Tests use `ctx.username` / `ctx.password`.
  `mask_text` removes known secret values plus bearer, password, token and cookie patterns
  from:
  * prompts;
  * stored page data;
  * error messages and API check rows;
  * findings;
  * summaries and reports.
* The Mistral API key is held as `SecretStr`, is excluded from `/settings`, is redacted from
  provider error text, and is never written to the `LLMCall` table.
* Browser session state captured after login (`storage_state.json`) lives in the run's
  artifact folder only while the run executes. It is deleted at the end of the run, and the
  artifact endpoint refuses to serve the `auth/` folder.

### T5: Hammering or damaging the target
* Page limit (`max_pages`, default 15, max 50), depth limit, `REQUEST_DELAY_MS` between page
  visits and API requests, at most 80 API-check requests per run, and one active run per
  project.
* The crawler never follows or clicks destructive links or buttons (logout, delete,
  remove, pay, purchase, checkout, unsubscribe, …) and never submits forms other than
  login.
* The API checks run in **safe mode**: only GET and HEAD unless a project explicitly sets
  `allowed_api_methods`.
* The UI shows "Only test sites you own or have permission to test", and creating a project
  requires a consent checkbox.

### T6: Data sent to a third party (Mistral free tier)
* The free tier may use API data for training. The UI shows a privacy notice, and every new
  project requires consent ("only staging/demo apps without real customer data").
* Prompts are compact summaries (no raw HTML), truncated and masked.

### T7: Path traversal on artifact downloads
* `/artifacts/{run_id}/{path}` resolves the path and requires it to stay inside that run's
  folder. It refuses `auth/`. Tested in `tests/test_pipeline.py::test_project_validation`.

## Reporting
This is a local, self-hosted tool. Report issues privately to the maintainers.
