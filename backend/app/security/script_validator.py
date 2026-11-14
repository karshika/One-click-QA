"""Static validator for LLM-generated Playwright test scripts.

The generated script must look like this:

    import re                                   # optional
    from playwright.sync_api import expect      # optional (expect is also injected)

    def test_journey(page, expect, ctx):
        page.goto(ctx.base_url + "/login")
        ...

Rules (allowlist-based, fail closed):
  * imports: only `re` and names from `playwright.sync_api` (expect, Page, Locator)
  * top level: imports, one `test_journey(page, expect, ctx)` function, optional helper
    functions, docstrings and simple constant assignments
  * no try/except (a test must not swallow assertion failures), no classes, no async,
    no global/nonlocal, no `with` except Playwright `page.expect_*` context managers
  * no access to dunder or private attributes (`_x`)
  * forbidden names: eval/exec/compile/open/__import__/getattr/os/sys/subprocess/...
  * every call must target an allowlisted builtin, a locally defined helper, `expect`,
    or an allowlisted method name (Playwright page/locator API, str methods, re methods)
  * absolute URL string literals must point to the target host
Runtime defense in depth lives in the harness (restricted builtins, network route guard,
subprocess timeout, temp working dir, scrubbed environment).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALLOWED_IMPORTS: dict[str, set[str] | None] = {
    "re": None,  # `import re`
    "playwright.sync_api": {"expect", "Page", "Locator", "BrowserContext"},
}

FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "hasattr", "input", "breakpoint", "help", "exit", "quit", "memoryview",
    "object", "type", "super", "classmethod", "staticmethod", "property", "os", "sys",
    "subprocess", "builtins", "__builtins__", "importlib", "socket", "shutil", "pathlib",
    "requests", "httpx", "urllib", "pickle", "marshal", "ctypes", "threading", "multiprocessing",
    "signal", "asyncio", "dir", "id", "bytearray",
}

ALLOWED_FUNCS = {
    "expect", "len", "range", "print", "any", "all", "min", "max", "int", "str", "float", "bool",
    "list", "dict", "set", "tuple", "enumerate", "zip", "sorted", "reversed", "isinstance", "abs",
    "round", "sum", "repr", "format",
}

PLAYWRIGHT_METHODS = {
    # navigation / page
    "goto", "reload", "go_back", "go_forward", "wait_for_load_state", "wait_for_url",
    "wait_for_timeout", "wait_for_selector", "title", "content", "set_viewport_size",
    "bring_to_front", "expect_navigation", "expect_response", "expect_request",
    "expect_console_message", "expect_popup",
    # locators
    "locator", "get_by_role", "get_by_text", "get_by_label", "get_by_placeholder",
    "get_by_alt_text", "get_by_title", "get_by_test_id", "frame_locator", "first", "last", "nth",
    "filter", "and_", "or_", "all", "count",
    # actions
    "click", "dblclick", "fill", "clear", "type", "press", "press_sequentially", "check",
    "uncheck", "set_checked", "select_option", "hover", "focus", "blur", "tap", "scroll_into_view_if_needed",
    "wait_for",
    # reads
    "inner_text", "inner_html", "text_content", "input_value", "get_attribute", "is_visible",
    "is_hidden", "is_enabled", "is_disabled", "is_checked", "is_editable", "all_inner_texts",
    "all_text_contents", "bounding_box", "value",
    # assertions (expect(...).xxx / expect(...).not_to_xxx)
    "to_be_visible", "to_be_hidden", "to_be_enabled", "to_be_disabled", "to_be_checked",
    "to_be_editable", "to_be_empty", "to_be_focused", "to_be_attached", "to_be_in_viewport",
    "to_have_url", "to_have_title", "to_have_text", "to_contain_text", "to_have_value",
    "to_have_values", "to_have_count", "to_have_attribute", "to_have_class", "to_have_id",
    "to_have_css", "to_have_accessible_name", "to_have_accessible_description", "to_have_role",
    "to_be_ok",
    "not_to_be_visible", "not_to_be_hidden", "not_to_be_enabled", "not_to_be_checked",
    "not_to_be_empty", "not_to_be_attached", "not_to_have_url", "not_to_have_title",
    "not_to_have_text", "not_to_contain_text", "not_to_have_value", "not_to_have_count",
    "not_to_have_attribute", "not_to_have_class", "not_to_be_disabled", "not_to_be_focused",
    "not_to_be_in_viewport",
}

STR_METHODS = {
    "lower", "upper", "swapcase", "capitalize", "title", "strip", "lstrip", "rstrip", "startswith",
    "endswith", "replace", "split", "join", "format", "find", "count", "isdigit", "zfill",
    "append", "extend", "get", "items", "keys", "values",
}

RE_METHODS = {"compile", "search", "match", "fullmatch", "escape", "IGNORECASE", "I", "findall", "sub"}

ALLOWED_METHODS = PLAYWRIGHT_METHODS | STR_METHODS | RE_METHODS

# Attributes that lead to network/file-system/JS-execution capabilities – blocked on access,
# not just on call (e.g. `page.context.request.get(...)`).
FORBIDDEN_ATTRS = {
    "context", "request", "browser", "evaluate", "evaluate_handle", "eval_on_selector",
    "eval_on_selector_all", "route", "unroute", "route_from_har", "add_init_script", "add_script_tag",
    "add_style_tag", "expose_function", "expose_binding", "set_extra_http_headers", "add_cookies",
    "cookies", "storage_state", "pdf", "screenshot", "video", "tracing", "set_input_files", "on", "once",
    "remove_listener", "wait_for_event", "frames", "main_frame", "workers", "service_workers", "opener",
    "set_content", "api_request", "clock", "close", "dispatch_event",
}

CTX_ATTRS = {"base_url", "username", "password", "unique_email", "unique_text"}

ENTRYPOINT = "test_journey"


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return "; ".join(self.errors)


class _Checker(ast.NodeVisitor):
    def __init__(self, target_host: str | None) -> None:
        self.errors: list[str] = []
        self.target_host = (target_host or "").lower()
        self.local_funcs: set[str] = set()

    def err(self, node: ast.AST, msg: str) -> None:
        self.errors.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    # --- structure -------------------------------------------------------------------
    def check_module(self, tree: ast.Module) -> None:
        entry = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.local_funcs.add(node.name)
                if node.name == ENTRYPOINT:
                    entry = node
            elif isinstance(node, ast.Import | ast.ImportFrom):
                pass
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                pass  # docstring
            elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
                if not _is_literal(node.value):
                    self.err(node, "top-level assignments must be literal constants")
            else:
                self.err(node, f"statement '{type(node).__name__}' not allowed at module level")
        if entry is None:
            self.errors.append(f"script must define `def {ENTRYPOINT}(page, expect, ctx):`")
        else:
            args = [a.arg for a in entry.args.args]
            if args != ["page", "expect", "ctx"]:
                self.err(entry, f"{ENTRYPOINT} must take exactly (page, expect, ctx), got {args}")
            if entry.decorator_list:
                self.err(entry, "decorators are not allowed")
        self.visit(tree)

    # --- visitors --------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name not in ALLOWED_IMPORTS or ALLOWED_IMPORTS[a.name] is not None:
                self.err(node, f"import of '{a.name}' is not allowed")
            if a.asname:
                self.err(node, "import aliases are not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        allowed = ALLOWED_IMPORTS.get(node.module or "")
        if node.level or allowed is None:
            self.err(node, f"import from '{node.module}' is not allowed")
            return
        for a in node.names:
            if a.name not in allowed:
                self.err(node, f"importing '{a.name}' from '{node.module}' is not allowed")

    def _forbid(self, node: ast.AST, what: str) -> None:
        self.err(node, f"{what} is not allowed in test scripts")

    def visit_Try(self, node: ast.Try) -> None:
        self._forbid(node, "try/except (tests must not swallow failures)")

    visit_TryStar = visit_Try  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._forbid(node, "class definitions")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._forbid(node, "async functions")

    def visit_Await(self, node: ast.Await) -> None:
        self._forbid(node, "await")

    def visit_Global(self, node: ast.Global) -> None:
        self._forbid(node, "global")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._forbid(node, "nonlocal")

    def visit_Delete(self, node: ast.Delete) -> None:
        self._forbid(node, "del")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._forbid(node, "lambda")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._forbid(node, "async with")

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            call = item.context_expr
            ok = (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr.startswith("expect_")
                and call.func.attr in PLAYWRIGHT_METHODS
            )
            if not ok:
                self.err(node, "`with` is only allowed for Playwright page.expect_* helpers")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.decorator_list:
            self.err(node, "decorators are not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
            self.err(node, f"use of name '{node.id}' is forbidden")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            self.err(node, f"access to private/dunder attribute '{node.attr}' is forbidden")
        if node.attr in FORBIDDEN_ATTRS:
            self.err(node, f"access to '{node.attr}' is forbidden")
        if isinstance(node.value, ast.Name) and node.value.id == "ctx" and node.attr not in CTX_ATTRS:
            self.err(node, f"ctx has no attribute '{node.attr}' (allowed: {sorted(CTX_ATTRS)})")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Name):
            if f.id not in ALLOWED_FUNCS and f.id not in self.local_funcs:
                self.err(node, f"call to '{f.id}' is not allowed")
        elif isinstance(f, ast.Attribute):
            is_ctx_helper = isinstance(f.value, ast.Name) and f.value.id == "ctx" and f.attr in CTX_ATTRS
            if f.attr not in ALLOWED_METHODS and not is_ctx_helper:
                self.err(node, f"method '{f.attr}' is not in the allowlist")
            if f.attr in {"evaluate", "evaluate_handle", "route", "add_init_script", "expose_function"}:
                self.err(node, f"method '{f.attr}' is forbidden")
        else:
            self.err(node, "dynamic call targets are not allowed")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value.lower().startswith(("http://", "https://", "//")):
            host = (urlsplit(node.value if not node.value.startswith("//") else "http:" + node.value).hostname
                    or "").lower()
            if self.target_host and host and host != self.target_host:
                self.err(node, f"URL literal points outside the target host: {node.value[:80]}")
        if isinstance(node.value, str) and node.value.lower().startswith(("file:", "javascript:", "data:")):
            self.err(node, "file:/javascript:/data: URLs are forbidden")


def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def validate_script(source: str, *, base_url: str | None = None, max_len: int = 20_000) -> ValidationResult:
    if len(source) > max_len:
        return ValidationResult(False, [f"script too long ({len(source)} > {max_len} chars)"])
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ValidationResult(False, [f"syntax error: {exc.msg} (line {exc.lineno})"])
    host = urlsplit(base_url).hostname if base_url else None
    checker = _Checker(host)
    checker.check_module(tree)
    return ValidationResult(not checker.errors, checker.errors)


def extract_code(text: str) -> str:
    """Pull python code out of an LLM answer (handles ```python fences)."""
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        candidates = [p for i, p in enumerate(parts) if i % 2 == 1]
        best = max(candidates, key=len) if candidates else t
        if best.startswith(("python\n", "py\n", "python3\n")):
            best = best.split("\n", 1)[1]
        return best.strip() + "\n"
    return t + "\n"
