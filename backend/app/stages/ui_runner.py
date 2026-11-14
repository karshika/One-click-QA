"""Stage 4 – Run UI tests in the sandbox, detect flakiness, self-repair broken scripts.

Per test:
  attempt → passed?                                  → passed
          → failed (assertion)  → retry once → pass  → flaky
                                             → fail  → failed   (app-bug candidate)
          → failed (script)     → LLM repair (≤ 2, each re-validated) → loop
                                  no repair possible → retry once → still script error → error
          → failed (environment/timeout)             → retry once → error
Pass/fail is decided ONLY by the sandboxed Playwright run (assertions / exceptions).
The final passing script is stored in ScriptCache so re-runs need zero LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import select

from app.core.context import RunContext, TestCaseData
from app.db.models import ScriptCache, utcnow
from app.db.session import session_scope
from app.llm import prompts
from app.llm.provider import LLMError
from app.security.script_validator import extract_code, validate_script
from app.security.secrets import mask_text
from app.stages.ui_generator import journey_json, script_hash

HARNESS = Path(__file__).parent / "harness" / "harness.py"
MAX_REPAIRS = 2
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "PATH",
             "PLAYWRIGHT_BROWSERS_PATH", "LANG", "PROGRAMDATA", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
             "PROCESSOR_ARCHITECTURE")


@dataclass
class RunOutcome:
    status: str  # passed|failed|flaky|error
    error: str | None = None
    error_kind: str | None = None
    duration_ms: int = 0
    attempts: int = 0
    trace_path: str | None = None
    screenshot_path: str | None = None
    video_path: str | None = None
    final_script: str = ""
    log: list[str] = field(default_factory=list)


def sandbox_env(tmpdir: str) -> dict[str, str]:
    """Scrubbed environment: no API keys, no app secrets, temp dirs pointed at the sandbox dir."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    env.update({"TEMP": tmpdir, "TMP": tmpdir, "TMPDIR": tmpdir, "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8"})
    return env


def _posix_limits() -> None:  # pragma: no cover - linux only
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
        resource.setrlimit(resource.RLIMIT_FSIZE, (300 * 1024 * 1024, 300 * 1024 * 1024))
        os.setsid()


async def run_in_sandbox(script: str, *, ctx: RunContext, out_dir: Path) -> dict[str, Any]:
    spec = {
        "script": script, "base_url": ctx.base_url, "out_dir": str(out_dir),
        "username": ctx.credentials.username if ctx.credentials else "",
        "password": ctx.credentials.password if ctx.credentials else "",
        "extra_domains": ctx.extra_domains, "storage_state": None,
    }
    with tempfile.TemporaryDirectory(prefix="ocqa-sbx-") as tmp:
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _posix_limits
        else:
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(HARNESS), cwd=tmp, env=sandbox_env(tmp),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **kwargs)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(json.dumps(spec).encode()),
                                                    timeout=ctx.test_timeout_s)
        except TimeoutError:
            await _kill_tree(proc)
            return {"status": "failed", "error_kind": "timeout",
                    "error": f"Test exceeded the {ctx.test_timeout_s}s sandbox timeout and was killed"}
    text = stdout.decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("OCQA_RESULT:"):
            return dict(json.loads(line[len("OCQA_RESULT:"):]))
    err = stderr.decode("utf-8", "replace")[-1500:]
    return {"status": "failed", "error_kind": "environment", "error": f"Sandbox crashed: {err or text[-500:]}"}


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec("taskkill", "/F", "/T", "/PID", str(proc.pid),
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()


async def repair(ctx: RunContext, tc: TestCaseData, script: str, res: dict[str, Any], n: int) -> str | None:
    prompt = prompts.render(
        "ui_repair", journey=journey_json(tc.journey), script=script, attempt=str(n),
        error=mask_text(str(res.get("error") or "")[:1500], ctx.secrets),
        failure_url=ctx.path_of(res.get("url") or ctx.base_url),
        dom_snippet=prompts.untrusted("failure_dom", mask_text(res.get("dom_snippet") or "", ctx.secrets),
                                      max_chars=3500))
    try:
        out = await ctx.llm.text("run", prompt, max_tokens=2000)
    except LLMError as exc:
        ctx.degraded["run"] = f"repair unavailable: {type(exc).__name__}"
        return None
    return extract_code(out.text)


async def run_case(ctx: RunContext, tc: TestCaseData, index: int) -> RunOutcome:
    base_dir = ctx.artifacts_dir / "tests" / f"t{index:02d}"
    script = tc.script
    out = RunOutcome(status="error", final_script=script)
    if not tc.valid:
        out.error, out.error_kind = f"Script rejected by validator: {tc.validation_error}", "validation"
        return out
    repairs = 0
    attempt = 0
    retried_same = False
    while True:
        if ctx.should_cancel():
            out.status, out.error = "error", "cancelled"
            return out
        attempt += 1
        res = await run_in_sandbox(script, ctx=ctx, out_dir=base_dir / f"attempt-{attempt}")
        out.attempts = attempt
        out.duration_ms += int(res.get("duration_ms") or 0)
        kind = res.get("error_kind")
        tc.attempts.append({"attempt": attempt, "status": res.get("status"), "error_kind": kind,
                            "error": mask_text(str(res.get("error") or ""), ctx.secrets)[:600] or None,
                            "script_hash": script_hash(script)})
        if res.get("status") == "passed":
            out.status = "flaky" if retried_same else "passed"
            out.final_script = script
            if retried_same:
                out.error = "Failed on first attempt, passed on retry"
            return out
        out.error = mask_text(str(res.get("error") or "unknown error"), ctx.secrets)
        out.error_kind = kind
        out.trace_path, out.screenshot_path, out.video_path = (res.get("trace_path"), res.get("screenshot_path"),
                                                               res.get("video_path"))
        if kind == "script" and repairs < MAX_REPAIRS and ctx.llm.available and tc.journey.source == "llm":
            repairs += 1
            fixed = await repair(ctx, tc, script, res, repairs)
            if fixed:
                v = validate_script(fixed, base_url=ctx.base_url)
                tc.attempts.append({"attempt": attempt, "source": "repair", "repair": repairs,
                                    "valid": v.ok, "validation_error": v.message or None})
                if v.ok:
                    script = fixed
                    tc.source = "repaired"
                    retried_same = False
                    continue
        if not retried_same:
            retried_same = True  # one retry of the same script → flakiness detection
            continue
        out.status = "failed" if kind == "assertion" else "error"
        out.final_script = script
        return out


def cache_passing(ctx: RunContext, tc: TestCaseData, script: str) -> None:
    with session_scope() as s:
        row = s.exec(select(ScriptCache).where(ScriptCache.project_id == ctx.project_id,
                                               ScriptCache.journey_key == tc.journey.key)).first()
        if row is None:
            row = ScriptCache(project_id=ctx.project_id, journey_key=tc.journey.key)
        row.script, row.script_hash, row.updated_at = script, script_hash(script), utcnow()
        s.add(row)
        s.commit()


async def run_tests(ctx: RunContext) -> list[tuple[TestCaseData, RunOutcome]]:
    results = []
    for i, tc in enumerate(ctx.tests):
        if ctx.should_cancel():
            break
        ctx.log("run", f"Running test {i + 1}/{len(ctx.tests)}: {tc.journey.name}")
        outcome = await run_case(ctx, tc, i)
        tc.script = outcome.final_script or tc.script
        if outcome.status == "passed":
            cache_passing(ctx, tc, tc.script)
        results.append((tc, outcome))
    return results
