"""Sandbox harness for ONE generated Playwright test. Runs as a separate process:

    python -I harness.py < spec.json

* the spec (incl. credentials) arrives on stdin, never on disk or argv
* the script was statically validated already; here it additionally runs with restricted
  builtins and an import hook that only allows `re` and `playwright.sync_api`
* the browser context has a strict route guard: only the target host (and allowlisted extra
  domains) can be contacted
* trace + screenshot + video are kept on failure, deleted on success
* prints exactly one line `OCQA_RESULT:{json}` on stdout
"""

from __future__ import annotations

import builtins
import json
import re
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import expect, sync_playwright

SAFE_BUILTIN_NAMES = [
    "len", "range", "print", "any", "all", "min", "max", "int", "str", "float", "bool", "list", "dict",
    "set", "tuple", "enumerate", "zip", "sorted", "reversed", "isinstance", "abs", "round", "sum", "repr",
    "format", "True", "False", "None", "AssertionError", "Exception", "ValueError",
]
ALLOWED_MODULES = {"re", "playwright.sync_api"}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    if level != 0 or name not in ALLOWED_MODULES:
        raise ImportError(f"import of '{name}' is blocked in the sandbox")
    return builtins.__import__(name, globals, locals, fromlist, level)


def safe_builtins() -> dict:
    b = {n: getattr(builtins, n) for n in SAFE_BUILTIN_NAMES if hasattr(builtins, n)}
    b["__import__"] = _safe_import
    b["__build_class__"] = None
    return b


class Ctx:
    """The only object (besides page/expect) the generated test can touch."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    def unique_email(self) -> str:
        return f"ocqa+{uuid.uuid4().hex[:10]}@example.com"

    def unique_text(self, prefix: str = "ocqa") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"


def classify(exc: BaseException) -> str:
    """Deterministic error kind. assertion → potential app bug; script → test itself wrong."""
    msg = str(exc)
    if isinstance(exc, AssertionError):
        return "assertion"
    if re.search(r"net::ERR_(CONNECTION_REFUSED|NAME_NOT_RESOLVED|CONNECTION_RESET|INTERNET_DISCONNECTED|"
                 r"ADDRESS_UNREACHABLE|TIMED_OUT)", msg):
        return "environment"
    if "ERR_BLOCKED_BY_CLIENT" in msg:
        return "script"  # tried to leave the target host
    if isinstance(exc, PWTimeout | PWError | NameError | AttributeError | TypeError | SyntaxError |
                  ImportError | KeyError | IndexError | ValueError):
        return "script"
    return "script"


def main() -> int:
    spec = json.loads(sys.stdin.read())
    out = Path(spec["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    base = spec["base_url"]
    allowed_hosts = {urlsplit(base).hostname or ""} | set(spec.get("extra_domains", []))
    result: dict = {"status": "failed", "error": None, "error_kind": None, "duration_ms": 0, "url": None,
                    "trace_path": None, "screenshot_path": None, "video_path": None, "dom_snippet": "",
                    "blocked": []}
    t0 = time.perf_counter()
    try:
        code = compile(spec["script"], "<generated_test>", "exec")
    except SyntaxError as exc:
        result.update(error=f"SyntaxError: {exc}", error_kind="script")
        print("OCQA_RESULT:" + json.dumps(result))
        return 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        video_dir = out / "video"
        context = browser.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=str(video_dir),
                                      storage_state=spec.get("storage_state"))

        def guard(route):  # strict: target host only
            url = route.request.url
            if url.startswith(("data:", "blob:", "about:")):
                return route.continue_()
            host = (urlsplit(url).hostname or "").lower()
            if host in allowed_hosts or any(host.endswith("." + d) for d in spec.get("extra_domains", [])):
                return route.continue_()
            result["blocked"].append(url[:200])
            return route.abort("blockedbyclient")

        context.route("**/*", guard)
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        page.set_default_timeout(int(spec.get("action_timeout_ms", 7000)))
        page.set_default_navigation_timeout(int(spec.get("nav_timeout_ms", 20000)))
        expect.set_options(timeout=int(spec.get("expect_timeout_ms", 5000)))
        ns = {"__builtins__": safe_builtins(), "__name__": "generated_test"}
        failed = False
        try:
            exec(code, ns)  # noqa: S102 - validated script, restricted builtins
            fn = ns.get("test_journey")
            if not callable(fn):
                raise NameError("test_journey is not defined")
            fn(page, expect, Ctx(base, spec.get("username", ""), spec.get("password", "")))
            result["status"] = "passed"
        except BaseException as exc:  # noqa: BLE001 - report every failure
            failed = True
            tb = traceback.extract_tb(exc.__traceback__)
            line = next((f.lineno for f in reversed(tb) if f.filename == "<generated_test>"), None)
            msg = f"{type(exc).__name__}: {exc}"
            msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
            result.update(error=(msg[:2500] + (f" (script line {line})" if line else "")),
                          error_kind=classify(exc))
            try:
                result["url"] = page.url
                shot = out / "failure.png"
                page.screenshot(path=str(shot), full_page=False)
                result["screenshot_path"] = str(shot)
                result["dom_snippet"] = page.locator("body").aria_snapshot(timeout=3000)[:3500]
            except Exception:  # noqa: BLE001
                pass
        finally:
            trace = out / "trace.zip"
            try:
                context.tracing.stop(path=str(trace) if failed else None)
            except Exception:  # noqa: BLE001
                pass
            video = page.video
            context.close()
            browser.close()
            if failed:
                result["trace_path"] = str(trace) if trace.exists() else None
                try:
                    vp = video.path() if video else None
                    result["video_path"] = str(vp) if vp and Path(vp).exists() else None
                except Exception:  # noqa: BLE001
                    pass
            else:
                shutil.rmtree(video_dir, ignore_errors=True)
    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    print("OCQA_RESULT:" + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
