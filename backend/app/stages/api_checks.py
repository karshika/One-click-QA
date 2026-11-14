"""Stage 5 – API checks (deterministic; httpx + jsonschema, every request SSRF-guarded).

With an OpenAPI spec (3.0/3.1):
  * valid request (examples/defaults/type-based values) must not return 5xx and should return a
    documented status; 2xx responses must match the declared content type and JSON schema
  * auth-required operations must return 401/403 without credentials
  * boundary/negative inputs for path/query params (0, -1, overflow, non-numeric, empty, long,
    special chars) must not return 5xx
  * latency warning above 1 s
Without a spec: replay GET endpoints observed during the crawl, check status/latency/content
type, schema consistency across calls of the same endpoint, and malformed-input handling.

Safe mode (default): only GET/HEAD; other methods are listed as skipped unless allowed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx
import yaml
from jsonschema import Draft202012Validator

from app.core.context import RunContext
from app.security.ssrf import SSRFError, is_allowed_host, safe_http_client

MAX_REQUESTS = 80
SLOW_MS = 1000.0


@dataclass
class ApiCheck:
    method: str
    url: str
    path: str
    check: str
    status: str  # passed|failed|warning|skipped
    status_code: int | None = None
    latency_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- schema helpers
def resolve_refs(node: Any, spec: dict[str, Any], depth: int = 0) -> Any:
    if depth > 25:
        return {}
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            target: Any = spec
            for part in node["$ref"][2:].split("/"):
                target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
            merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve_refs(merged, spec, depth + 1)
        return {k: resolve_refs(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(v, spec, depth + 1) for v in node]
    return node


def oas30_to_jsonschema(schema: Any) -> Any:
    """OpenAPI 3.0 `nullable` → JSON Schema type list (3.1 schemas pass through unchanged)."""
    if isinstance(schema, dict):
        out = {k: oas30_to_jsonschema(v) for k, v in schema.items() if k not in ("nullable", "example", "xml")}
        if schema.get("nullable") and "type" in out:
            t = out["type"]
            out["type"] = (t if isinstance(t, list) else [t]) + ["null"]
        return out
    if isinstance(schema, list):
        return [oas30_to_jsonschema(v) for v in schema]
    return schema


def schema_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    v = Draft202012Validator(oas30_to_jsonschema(schema))
    errs = []
    for e in sorted(v.iter_errors(instance), key=lambda e: list(e.absolute_path))[:5]:
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        errs.append(f"{loc}: {e.message[:200]}")
    return errs


def sample_value(schema: dict[str, Any], name: str = "") -> Any:
    if "example" in schema:
        return schema["example"]
    if schema.get("examples"):
        ex = schema["examples"]
        return ex[0] if isinstance(ex, list) else next(iter(ex.values()), None)
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if t == "number":
        return float(max(1, schema.get("minimum", 1)))
    if t == "boolean":
        return True
    if "anyOf" in schema or "oneOf" in schema:
        return sample_value((schema.get("anyOf") or schema.get("oneOf"))[0], name)
    fmt = schema.get("format")
    if fmt == "email":
        return "qa@example.com"
    if fmt == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if fmt in ("date", "date-time"):
        return "2030-01-15" if fmt == "date" else "2030-01-15T10:00:00Z"
    return "test"


def boundary_values(schema: dict[str, Any]) -> list[tuple[str, Any]]:
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t in ("integer", "number"):
        return [("zero", 0), ("negative", -1), ("overflow", 2**31), ("non_numeric", "abc")]
    if t == "boolean":
        return [("invalid_bool", "maybe")]
    return [("empty", ""), ("long", "a" * 1000), ("special_chars", "'\"<>%;--")]


def json_shape(v: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "…"
    if isinstance(v, dict):
        return {k: json_shape(x, depth + 1) for k, x in sorted(v.items())[:30]}
    if isinstance(v, list):
        return [json_shape(v[0], depth + 1)] if v else []
    return type(v).__name__


# ----------------------------------------------------------------------------- runner
class _Http:
    def __init__(self, ctx: RunContext, client: httpx.AsyncClient) -> None:
        self.ctx = ctx
        self.client = client
        self.count = 0

    async def send(self, method: str, url: str, **kw: Any) -> tuple[httpx.Response | None, float, str | None]:
        if self.count >= MAX_REQUESTS:
            return None, 0.0, "request budget for API checks exhausted"
        if not is_allowed_host(url, self.ctx.base_url, self.ctx.extra_domains):
            return None, 0.0, "endpoint host is outside the target"
        self.count += 1
        await asyncio.sleep(self.ctx.request_delay_ms / 2000)
        t0 = time.perf_counter()
        try:
            r = await self.client.request(method, url, **kw)
            return r, round((time.perf_counter() - t0) * 1000, 1), None
        except SSRFError as exc:
            return None, 0.0, f"blocked by SSRF guard: {exc}"
        except httpx.HTTPError as exc:
            return None, round((time.perf_counter() - t0) * 1000, 1), f"{type(exc).__name__}: {exc}"[:300]


def _json(r: httpx.Response) -> tuple[Any, bool]:
    try:
        return r.json(), True
    except (json.JSONDecodeError, ValueError):
        return None, False


async def load_spec(ctx: RunContext, http: _Http) -> tuple[dict[str, Any] | None, str | None]:
    assert ctx.openapi_url
    url = urljoin(ctx.base_url, ctx.openapi_url)
    r, _, err = await http.send("GET", url)
    if r is None:
        return None, err
    if r.status_code >= 400:
        return None, f"OpenAPI spec returned HTTP {r.status_code}"
    try:
        spec = r.json() if "json" in r.headers.get("content-type", "") or r.text.lstrip().startswith("{") \
            else yaml.safe_load(r.text)
    except (ValueError, yaml.YAMLError) as exc:
        return None, f"Could not parse OpenAPI spec: {exc}"
    if not isinstance(spec, dict) or "paths" not in spec:
        return None, "Document is not an OpenAPI spec (no 'paths')"
    return spec, None


def server_base(ctx: RunContext, spec: dict[str, Any]) -> str:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return urljoin(urljoin(ctx.base_url, ctx.openapi_url or "/"), servers[0]["url"]).rstrip("/")
    return ctx.base_url.rstrip("/")


async def check_openapi(ctx: RunContext, http: _Http, spec: dict[str, Any]) -> list[ApiCheck]:
    out: list[ApiCheck] = []
    base = server_base(ctx, spec)
    global_security = spec.get("security")
    allowed = {m.upper() for m in ctx.allowed_api_methods}
    for path, item in list(spec.get("paths", {}).items())[:40]:
        item = resolve_refs(item, spec)
        shared_params = item.get("parameters", [])
        for method, op in item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            m = method.upper()
            if m not in allowed:
                out.append(ApiCheck(m, base + path, path, "safe_mode", "skipped",
                                    message=f"{m} skipped in safe mode (only {', '.join(sorted(allowed))})"))
                continue
            params = shared_params + op.get("parameters", [])
            path_params = {p["name"]: p for p in params if p.get("in") == "path"}
            query_params = {p["name"]: p for p in params if p.get("in") == "query" and p.get("required")}
            values = {n: sample_value(p.get("schema", {}), n) for n, p in {**path_params, **query_params}.items()}

            def build(vals: dict[str, Any], path: str = path, path_params: dict = path_params,
                      query_params: dict = query_params) -> str:
                p = path
                for n in path_params:
                    p = p.replace("{" + n + "}", str(vals[n]))
                q = {n: vals[n] for n in query_params}
                return base + p + (("?" + urlencode(q)) if q else "")

            security = op.get("security", global_security)
            secured = bool(security) and any(bool(s) for s in security)
            responses = op.get("responses", {})
            url = build(values)
            r, ms, err = await http.send(m, url)
            if r is None:
                out.append(ApiCheck(m, url, path, "reachable", "failed", message=err or "no response"))
                continue
            if secured:
                ok = r.status_code in (401, 403)
                out.append(ApiCheck(m, url, path, "auth_required", "passed" if ok else "failed", r.status_code, ms,
                                    "Requires authentication (401/403 without credentials)" if ok else
                                    f"Auth-protected endpoint returned {r.status_code} without credentials"))
                continue
            out.extend(_check_response(m, url, path, r, ms, responses))
            # boundary / negative inputs, one parameter at a time
            for n, p in {**path_params, **query_params}.items():
                for label, val in boundary_values(p.get("schema", {})):
                    bad = dict(values)
                    bad[n] = val
                    burl = build(bad)
                    br, bms, berr = await http.send(m, burl)
                    if br is None:
                        continue
                    if br.status_code >= 500:
                        out.append(ApiCheck(m, burl, path, "boundary", "failed", br.status_code, bms,
                                            f"Server error {br.status_code} for {label} value of '{n}' ({val!r:.40})",
                                            {"param": n, "case": label, "body": br.text[:300]}))
                    else:
                        out.append(ApiCheck(m, burl, path, "boundary", "passed", br.status_code, bms,
                                            f"{label} value of '{n}' handled ({br.status_code})",
                                            {"param": n, "case": label}))
    return out


def _check_response(m: str, url: str, path: str, r: httpx.Response, ms: float,
                    responses: dict[str, Any]) -> list[ApiCheck]:
    out: list[ApiCheck] = []
    code = r.status_code
    if code >= 500:
        out.append(ApiCheck(m, url, path, "server_error", "failed", code, ms,
                            f"Valid request returned server error {code}", {"body": r.text[:300]}))
        return out
    documented = str(code) in responses or f"{str(code)[0]}XX" in responses or "default" in responses
    out.append(ApiCheck(m, url, path, "status", "passed" if documented else "warning", code, ms,
                        f"Returned {code}" + ("" if documented else " (status code not documented in the spec)")))
    if ms > SLOW_MS:
        out.append(ApiCheck(m, url, path, "latency", "warning", code, ms, f"Slow response: {ms:.0f} ms"))
    spec_resp = responses.get(str(code)) or responses.get(f"{str(code)[0]}XX") or {}
    content = spec_resp.get("content") or {}
    if 200 <= code < 300 and content and m != "HEAD":
        ctype = r.headers.get("content-type", "").split(";")[0].strip()
        declared = list(content.keys())
        ok_type = any(ctype == d or (d.endswith("/*") and ctype.startswith(d[:-1])) or d == "*/*" for d in declared)
        out.append(ApiCheck(m, url, path, "content_type", "passed" if ok_type else "failed", code, ms,
                            f"Content-Type {ctype or '(none)'}" + ("" if ok_type else f", expected {declared}")))
        schema = (content.get(ctype) or content.get("application/json") or {}).get("schema")
        if schema and "json" in ctype:
            body, ok_json = _json(r)
            if not ok_json:
                out.append(ApiCheck(m, url, path, "schema", "failed", code, ms, "Response body is not valid JSON"))
            else:
                errs = schema_errors(body, schema)
                out.append(ApiCheck(m, url, path, "schema", "failed" if errs else "passed", code, ms,
                                    ("Response violates the documented schema: " + "; ".join(errs)) if errs
                                    else "Response matches the documented schema",
                                    {"errors": errs, "sample": r.text[:400]} if errs else {}))
    return out


NUMERIC_SEG = re.compile(r"^\d+$")


def template_of(url: str) -> str:
    p = urlsplit(url)
    segs = ["{id}" if NUMERIC_SEG.match(s) else s for s in p.path.split("/")]
    return "/".join(segs) or "/"


async def check_observed(ctx: RunContext, http: _Http) -> list[ApiCheck]:
    out: list[ApiCheck] = []
    calls: dict[str, dict[str, Any]] = {}
    for p in ctx.pages:
        for c in p.api_calls:
            if c.get("method") in ("GET", "HEAD") and is_allowed_host(c["url"], ctx.base_url, ctx.extra_domains):
                calls.setdefault(c["url"], c)
    by_template: dict[str, list[str]] = {}
    for url in list(calls)[:25]:
        by_template.setdefault(template_of(url), []).append(url)
    for tpl, urls in by_template.items():
        shapes = []
        for url in urls[:5]:
            r, ms, err = await http.send("GET", url)
            if r is None:
                out.append(ApiCheck("GET", url, tpl, "reachable", "failed", message=err or "no response"))
                continue
            if r.status_code >= 500:
                out.append(ApiCheck("GET", url, tpl, "server_error", "failed", r.status_code, ms,
                                    f"Observed endpoint returned {r.status_code}", {"body": r.text[:300]}))
                continue
            out.append(ApiCheck("GET", url, tpl, "status", "passed" if r.status_code < 400 else "warning",
                                r.status_code, ms, f"Returned {r.status_code}"))
            if ms > SLOW_MS:
                out.append(ApiCheck("GET", url, tpl, "latency", "warning", r.status_code, ms, f"Slow: {ms:.0f} ms"))
            body, ok = _json(r)
            if ok:
                shapes.append(json_shape(body))
        if len(shapes) > 1:
            consistent = all(s == shapes[0] or (isinstance(s, list) and not s) for s in shapes)
            out.append(ApiCheck("GET", urls[0], tpl, "consistency", "passed" if consistent else "warning",
                                message="Response shape consistent across calls" if consistent else
                                "Response shape differs between calls of the same endpoint",
                                details={} if consistent else {"shapes": shapes[:3]}))
        # malformed input on numeric path segments
        sample = urls[0]
        parts = urlsplit(sample)
        segs = parts.path.split("/")
        idx = next((i for i, s in enumerate(segs) if NUMERIC_SEG.match(s)), None)
        if idx is not None:
            for label, val in [("zero", "0"), ("negative", "-1"), ("non_numeric", "abc")]:
                new_segs = list(segs)
                new_segs[idx] = val
                burl = urlunsplit((parts.scheme, parts.netloc, "/".join(new_segs), parts.query, ""))
                br, bms, _ = await http.send("GET", burl)
                if br is None:
                    continue
                failed = br.status_code >= 500
                out.append(ApiCheck("GET", burl, tpl, "boundary", "failed" if failed else "passed", br.status_code,
                                    bms, (f"Server error {br.status_code} for {label} id" if failed else
                                          f"{label} id handled ({br.status_code})"),
                                    {"case": label, "body": br.text[:300]} if failed else {"case": label}))
    return out


async def run_api_checks(ctx: RunContext) -> tuple[list[ApiCheck], str]:
    """Returns (checks, mode) where mode is 'openapi' | 'observed' | 'none'."""
    async with safe_http_client(allow_local=ctx.allow_local, timeout=15.0,
                                headers={"User-Agent": "OneClickQA/0.1 (automated QA)"}) as client:
        http = _Http(ctx, client)
        if ctx.openapi_url:
            spec, err = await load_spec(ctx, http)
            if spec is not None:
                return await check_openapi(ctx, http, spec), "openapi"
            checks = [ApiCheck("GET", ctx.openapi_url, ctx.openapi_url, "spec", "failed",
                               message=err or "spec unavailable")]
            return checks + await check_observed(ctx, http), "observed"
        checks = await check_observed(ctx, http)
        return checks, ("observed" if checks else "none")
