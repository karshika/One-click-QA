"""ShopLite – a tiny demo shop with deliberately planted bugs for testing OneClickQA.

See ../EXPECTED_BUGS.md. Everything that is NOT a planted bug is meant to be clean
(accessible markup, fast pages, correct API behavior) so false positives stay measurable.
"""

from __future__ import annotations

import asyncio
import html
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel

app = FastAPI(
    title="ShopLite Demo API",
    version="1.0.0",
    description="Demo shop API used to test OneClickQA. Contains planted bugs.",
)

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "Demo-Pass-2024"
SESSIONS: dict[str, str] = {}

PRODUCTS = [
    {"id": 1, "name": "Canvas Tote Bag", "price": 18.0, "in_stock": True, "sold": 120},
    {"id": 2, "name": "Ceramic Mug", "price": 12.5, "in_stock": True, "sold": 340},
    {"id": 3, "name": "Desk Plant", "price": 24.0, "in_stock": False, "sold": 75},
    {"id": 4, "name": "Notebook Set", "price": 9.99, "in_stock": True, "sold": 410},
    {"id": 5, "name": "Wool Socks", "price": 14.0, "in_stock": True, "sold": 220},
]


# ----------------------------------------------------------------------------- models
class Product(BaseModel):
    id: int
    name: str
    price: float
    in_stock: bool


class ProductDetail(Product):
    popularity: float


class Stats(BaseModel):
    total_products: int
    total_orders: int
    revenue: float


class Order(BaseModel):
    id: int
    product_id: int
    quantity: int


# ----------------------------------------------------------------------------- layout
CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
color:#1a1a1a;background:#ffffff;line-height:1.5}
header{background:#1f3a5f;color:#ffffff;padding:12px 24px}
header a{color:#ffffff;margin-right:16px;text-decoration:underline}
header .brand{font-weight:700;font-size:20px;margin-right:32px;text-decoration:none}
main{max-width:860px;margin:0 auto;padding:24px}
footer{max-width:860px;margin:0 auto;padding:24px;color:#444444;font-size:14px}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #cccccc;padding:8px;text-align:left}
label{display:block;margin-top:12px;font-weight:600}
input,textarea{width:100%;max-width:420px;padding:8px;border:1px solid #666666;border-radius:4px;font:inherit}
.btn{margin-top:16px;background:#1f3a5f;color:#ffffff;border:0;padding:10px 18px;border-radius:4px;font:inherit;
cursor:pointer}
.error{color:#8a1111;background:#fdecec;padding:8px 12px;border-radius:4px}
.ok{color:#0b5d1e;background:#e6f4ea;padding:8px 12px;border-radius:4px}
.promo{background:#fff4cc;color:#3d3000;padding:24px;margin-bottom:16px;border-radius:6px}
.icon-btn{background:#1f3a5f;border:0;border-radius:50%;width:40px;height:40px;cursor:pointer}
.subscribe{margin-top:8px;background:#ffffff;color:#c4c4c4;border:1px solid #e0e0e0;padding:10px 18px;
border-radius:4px;font:inherit}
"""

NAV = """
<header>
  <a class="brand" href="/">ShopLite</a>
  <nav aria-label="Main">
    <a href="/">Home</a><a href="/products">Products</a><a href="/contact">Contact</a>
    <a href="/reports">Reports</a><a href="/old-pricing">Pricing</a><a href="/login">Sign in</a>
  </nav>
</header>
"""


def page(title: str, body: str, extra_head: str = "", status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · ShopLite</title>
<link rel="icon" href="/favicon.ico">
<style>{CSS}</style>
{extra_head}
</head>
<body>
{NAV}
<main id="main">
{body}
</main>
<footer>ShopLite demo shop – for testing only.</footer>
</body>
</html>"""
    return HTMLResponse(doc, status_code=status)


# ----------------------------------------------------------------------------- pages
@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" fill="#1f3a5f"/></svg>'
    return Response(svg, media_type="image/svg+xml")


@app.get("/static/team.svg", include_in_schema=False)
def team_image() -> Response:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120" viewBox="0 0 320 120">'
           '<rect width="320" height="120" fill="#dbe7f5"/><circle cx="80" cy="60" r="30" fill="#1f3a5f"/>'
           '<circle cx="160" cy="60" r="30" fill="#3d6ea8"/><circle cx="240" cy="60" r="30" fill="#1f3a5f"/></svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> HTMLResponse:
    body = """
<div id="banner-slot"></div>
<h1>Welcome to ShopLite</h1>
<p>Simple, well-made everyday goods. Browse our <a href="/products">products</a> or
<a href="/contact">get in touch</a>.</p>

<h2>Meet the team</h2>
<!-- BUG B2: image without alt text -->
<img src="/static/team.svg" width="320" height="120">

<h2>Newsletter</h2>
<p>Get a monthly email with new arrivals.</p>
<!-- BUG B4: low-contrast button text (#c4c4c4 on #ffffff) -->
<button type="button" class="subscribe">Subscribe</button>

<h2>Share</h2>
<!-- BUG B3: icon-only button with no accessible name -->
<button type="button" class="icon-btn"><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
<path d="M2 8h12M8 2v12" stroke="#ffffff" stroke-width="2"/></svg></button>

<h2>Why ShopLite?</h2>
<p>We ship within two business days and every product comes with a 30-day return policy.
Our packaging is fully recyclable and we work directly with small makers.</p>
<p>Questions? Our support team answers every message within one business day.</p>
"""
    # BUG B6: banner injected late without reserved space → layout shift (CLS)
    # BUG B9: uncaught JavaScript error on load (initCarousel is not defined)
    script = """
<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var b = document.createElement('div');
    b.className = 'promo';
    b.style.height = '220px';
    b.innerHTML = '<strong>Autumn sale!</strong> 20% off everything this week.';
    document.getElementById('banner-slot').appendChild(b);
  }, 700);
  initCarousel();
});
</script>"""
    return page("Home", body, extra_head=script)


@app.get("/products", response_class=HTMLResponse, include_in_schema=False)
def products_page() -> HTMLResponse:
    body = """
<h1>Products</h1>
<p>All products currently in our catalogue.</p>
<table aria-describedby="products-caption">
  <caption id="products-caption">Product catalogue</caption>
  <thead><tr><th scope="col">Name</th><th scope="col">Price</th><th scope="col">Availability</th></tr></thead>
  <tbody id="rows"><tr><td colspan="3">Loading…</td></tr></tbody>
</table>
<script>
fetch('/api/products').then(function (r) { return r.json(); }).then(function (items) {
  document.getElementById('rows').innerHTML = items.map(function (p) {
    return '<tr><td><a href="/products/' + p.id + '">' + p.name + '</a></td><td>$' + p.price.toFixed(2) +
      '</td><td>' + (p.in_stock ? 'In stock' : 'Sold out') + '</td></tr>';
  }).join('');
});
</script>
"""
    return page("Products", body)


@app.get("/products/{product_id}", response_class=HTMLResponse, include_in_schema=False)
def product_page(product_id: int) -> HTMLResponse:
    body = f"""
<h1 id="name">Product</h1>
<p id="details">Loading…</p>
<p><a href="/products">Back to products</a></p>
<script>
fetch('/api/products/{product_id}').then(function (r) {{ return r.json(); }}).then(function (p) {{
  document.getElementById('name').textContent = p.name;
  document.getElementById('details').textContent = '$' + p.price.toFixed(2) + (p.in_stock ? ' · In stock' : ' · Sold out');
}});
</script>
"""
    return page("Product", body)


@app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
async def reports_page() -> HTMLResponse:
    # BUG B5: artificially slow page (3 s server delay)
    await asyncio.sleep(3)
    body = """
<h1>Monthly report</h1>
<p>Orders and revenue for the current month.</p>
<table>
  <caption>Summary</caption>
  <thead><tr><th scope="col">Metric</th><th scope="col">Value</th></tr></thead>
  <tbody><tr><td>Orders</td><td>12</td></tr><tr><td>Revenue</td><td>$1,240.00</td></tr></tbody>
</table>
"""
    return page("Reports", body)


@app.get("/contact", response_class=HTMLResponse, include_in_schema=False)
def contact_page() -> HTMLResponse:
    body = """
<h1>Contact us</h1>
<form method="post" action="/contact">
  <label for="name">Name</label><input id="name" name="name" autocomplete="name" required>
  <label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required>
  <label for="message">Message</label><textarea id="message" name="message" rows="4" required></textarea>
  <button class="btn" type="submit">Send message</button>
</form>
"""
    return page("Contact", body)


@app.post("/contact", response_class=HTMLResponse, include_in_schema=False)
def contact_submit(name: str = Form(""), email: str = Form(""), message: str = Form("")) -> HTMLResponse:
    if not name.strip() or "@" not in email or not message.strip():
        return page("Contact", '<h1>Contact us</h1><p class="error" role="alert">Please fill in all fields.</p>'
                    '<p><a href="/contact">Try again</a></p>', status=400)
    return page("Thanks", f'<h1>Thank you, {html.escape(name)}!</h1>'
                '<p class="ok" role="status">Your message has been sent. We will reply within one business day.</p>')


def _login_form(error: str = "") -> str:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""
<h1>Sign in</h1>
{err}
<form method="post" action="/login">
  <label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <button class="btn" type="submit">Sign in</button>
</form>
"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page() -> HTMLResponse:
    return page("Sign in", _login_form())


@app.post("/login", include_in_schema=False)
def login_submit(email: str = Form(""), password: str = Form("")) -> Response:
    # BUG B1: password comparison is case-insensitive, so "dEMO-pASS-2024" also logs you in.
    if email.strip().lower() == DEMO_EMAIL and password.lower() == DEMO_PASSWORD.lower():
        token = secrets.token_urlsafe(16)
        SESSIONS[token] = email
        resp = RedirectResponse("/account", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax")
        return resp
    return page("Sign in", _login_form("Invalid email or password."))


def _user(request: Request) -> str | None:
    return SESSIONS.get(request.cookies.get("session", ""))


@app.get("/account", response_class=HTMLResponse, include_in_schema=False)
def account(request: Request) -> Response:
    user = _user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    body = f"""
<h1>Your account</h1>
<p class="ok" role="status">Signed in as {html.escape(user)}.</p>
<p>You have no open orders.</p>
<p><a href="/logout">Sign out</a></p>
"""
    return page("Account", body)


@app.get("/logout", include_in_schema=False)
def logout(request: Request) -> Response:
    SESSIONS.pop(request.cookies.get("session", ""), None)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.exception_handler(404)
async def not_found(request: Request, exc: Exception) -> Response:
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not Found")
        return JSONResponse({"detail": detail}, status_code=404)
    # BUG B10 lands here: the nav links to /old-pricing which no longer exists.
    return page("Not found", "<h1>Page not found</h1><p><a href=\"/\">Go home</a></p>", status=404)


# ----------------------------------------------------------------------------- API
@app.get("/api/health", tags=["meta"])
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/products", response_model=list[Product], tags=["products"])
def list_products() -> list[dict[str, Any]]:
    return [{k: v for k, v in p.items() if k != "sold"} for p in PRODUCTS]


@app.get("/api/products/{product_id}", response_model=ProductDetail, tags=["products"],
         responses={404: {"description": "Product not found"}})
def get_product(product_id: int) -> dict[str, Any]:
    # BUG B7: product_id is not validated; a "cache bucket" divides by it, so id=0 → 500.
    _bucket = 1000 // product_id
    p = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if p is None:
        raise HTTPException(404, "Product not found")
    return {**{k: v for k, v in p.items() if k != "sold"}, "popularity": round(p["sold"] / 10, 1)}


@app.get("/api/stats", tags=["stats"], responses={200: {"model": Stats, "description": "Shop statistics"}})
def get_stats() -> JSONResponse:
    # BUG B8: total_orders is returned as a string, violating the documented integer schema.
    return JSONResponse({"total_products": len(PRODUCTS), "total_orders": "12", "revenue": 1240.0})


bearer = HTTPBearer(auto_error=False)


@app.get("/api/orders", response_model=list[Order], tags=["orders"],
         responses={401: {"description": "Not authenticated"}})
def list_orders(request: Request, creds: Any = Depends(bearer)) -> list[dict[str, Any]]:
    user = _user(request) or (SESSIONS.get(creds.credentials) if creds else None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return [{"id": 1, "product_id": 2, "quantity": 1}]
