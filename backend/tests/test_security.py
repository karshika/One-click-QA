import pytest

from app.security.script_validator import extract_code, validate_script
from app.security.secrets import MASK, decrypt_json, encrypt_json, mask_obj, mask_text
from app.security.ssrf import SSRFError, check_url, is_allowed_host, is_forbidden_ip

# ---------------------------------------------------------------- SSRF


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1",
    "fe80::1", "fd00::1", "100.64.0.1", "::ffff:127.0.0.1", "224.0.0.1",
])
def test_forbidden_ips(ip: str) -> None:
    assert is_forbidden_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert not is_forbidden_ip(ip)


def test_check_url_blocks_private_and_resolved_private() -> None:
    with pytest.raises(SSRFError):
        check_url("http://127.0.0.1:8000/", allow_local=False)
    with pytest.raises(SSRFError):
        check_url("http://localhost/", allow_local=False)
    with pytest.raises(SSRFError):  # DNS name resolving to metadata IP
        check_url("http://evil.example/", allow_local=False, resolver=lambda h: ["169.254.169.254"])
    with pytest.raises(SSRFError):  # any private address among results → blocked
        check_url("http://mixed.example/", allow_local=False, resolver=lambda h: ["8.8.8.8", "10.0.0.1"])
    assert check_url("https://ok.example/x", allow_local=False, resolver=lambda h: ["93.184.216.34"])


def test_check_url_schemes_and_credentials() -> None:
    for bad in ["file:///etc/passwd", "gopher://x", "ftp://x.com", "javascript:alert(1)"]:
        with pytest.raises(SSRFError):
            check_url(bad, allow_local=True)
    with pytest.raises(SSRFError):
        check_url("http://user:pw@example.com/", allow_local=True)


def test_allow_local_flag() -> None:
    assert check_url("http://localhost:8001/", allow_local=True)


def test_same_origin_and_allowlist() -> None:
    base = "http://app.example.com/"
    assert is_allowed_host("http://app.example.com/a", base, [])
    assert not is_allowed_host("http://evil.com/a", base, [])
    assert is_allowed_host("https://cdn.assets.io/x.js", base, ["assets.io"])
    assert not is_allowed_host("https://notassets.io/x.js", base, ["assets.io"])


# ---------------------------------------------------------------- script validator

GOOD = '''
import re
from playwright.sync_api import expect

def test_journey(page, expect, ctx):
    page.goto(ctx.base_url + "/login")
    page.get_by_label("Email").fill(ctx.username)
    page.get_by_label("Password").fill(ctx.password.swapcase())
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text(re.compile("invalid", re.IGNORECASE))
    expect(page).to_have_url(re.compile(r".*/login"))
'''


def test_validator_accepts_good_script() -> None:
    r = validate_script(GOOD, base_url="http://localhost:8001")
    assert r.ok, r.errors


@pytest.mark.parametrize("snippet,reason", [
    ("import os", "import"),
    ("import subprocess", "import"),
    ("from pathlib import Path", "import"),
    ("import re as r", "alias"),
    ("__import__('os').system('x')", "forbidden"),
    ("eval('1+1')", "forbidden"),
    ("exec('x=1')", "forbidden"),
    ("open('/etc/passwd').read()", "forbidden"),
    ("getattr(page, 'goto')('x')", "forbidden"),
    ("page.__class__.__bases__", "dunder"),
    ("page._impl_obj", "private"),
    ("page.evaluate('fetch(`http://evil`)')", "forbidden"),
    ("page.route('**', lambda r: r)", "forbidden"),
    ("page.context.request.get('/x')", "access to 'context'"),
    ("page.request.post('/x')", "access to 'request'"),
    ("page.screenshot(path='x.png')", "access to 'screenshot'"),
    ("page.foo_bar()", "allowlist"),
    ("page.goto('http://evil.com/steal')", "outside the target host"),
    ("page.goto('file:///etc/passwd')", "file:"),
    ("ctx.secret_key", "ctx has no attribute"),
    ("(lambda: 1)()", "lambda"),
])
def test_validator_rejects_dangerous_code(snippet: str, reason: str) -> None:
    src = f"def test_journey(page, expect, ctx):\n    {snippet}\n"
    r = validate_script(src, base_url="http://localhost:8001")
    assert not r.ok, snippet
    assert reason.lower() in r.message.lower(), r.message


def test_validator_rejects_try_except_and_structure() -> None:
    src = ("def test_journey(page, expect, ctx):\n    try:\n        page.goto('/')\n"
           "    except Exception:\n        pass\n")
    assert "try/except" in validate_script(src).message
    assert not validate_script("x = 1\n").ok  # no entrypoint
    assert not validate_script("def test_journey(page):\n    pass\n").ok  # wrong signature
    assert "syntax error" in validate_script("def (:\n").message
    assert not validate_script("class X: pass\ndef test_journey(page, expect, ctx):\n    pass\n").ok
    assert not validate_script("import os\n" * 3000).ok  # too long / forbidden


def test_validator_rejects_toplevel_side_effects() -> None:
    src = "print('x')\ndef test_journey(page, expect, ctx):\n    pass\n"
    # print() at module level is an Expr with a Call, not a docstring
    assert not validate_script(src).ok


def test_extract_code_from_fences() -> None:
    assert extract_code("Here:\n```python\ndef test_journey(page, expect, ctx):\n    pass\n```\n").startswith("def ")


# ---------------------------------------------------------------- secrets


def test_encrypt_roundtrip() -> None:
    token = encrypt_json({"username": "a", "password": "b"})
    assert token and "b" not in token[:5]
    assert decrypt_json(token) == {"username": "a", "password": "b"}
    assert decrypt_json("garbage") == {}


def test_masking() -> None:
    assert "s3cret" not in mask_text("password=s3cret&x=1")
    assert "abc.def" not in mask_text("Authorization: Bearer abc.def")
    assert mask_text("my pw is Tr0ub4dor", ["Tr0ub4dor"]) == f"my pw is {MASK}"
    assert mask_obj({"password": "x", "nested": [{"token": "y"}], "ok": "fine"}) == {
        "password": MASK, "nested": [{"token": MASK}], "ok": "fine"}
