"""SSRF guard.

Every URL OneClickQA fetches (crawl targets, OpenAPI specs, API checks, redirects) passes
through `check_url`. Private, loopback, link-local, CGNAT, multicast, reserved and
unspecified addresses are blocked unless ALLOW_LOCAL_TARGETS=true (local development /
docker-compose demo). Redirects are re-checked hop by hop by the callers (see
`safe_http_client` and the Playwright route guard in the crawler).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

ALLOWED_SCHEMES = {"http", "https"}

_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("fc00::/7"),
]

Resolver = Callable[[str], list[str]]


class SSRFError(ValueError):
    """Raised when a URL targets a forbidden destination."""


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(i[4][0]) for i in infos})


@lru_cache(maxsize=512)
def _cached_resolve(host: str) -> tuple[str, ...]:
    return tuple(_default_resolver(host))


def is_forbidden_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


def check_url(url: str, *, allow_local: bool, resolver: Resolver | None = None) -> str:
    """Validate a URL for outbound access. Returns the normalized URL or raises SSRFError."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFError(f"Scheme '{parts.scheme}' is not allowed (only http/https)")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFError("URL has no host")
    if parts.username or parts.password:
        raise SSRFError("Credentials in URLs are not allowed; use the project login settings")
    if allow_local:
        return url.strip()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise SSRFError(f"Host '{host}' is local (set ALLOW_LOCAL_TARGETS=true for local development)")
    try:
        ipaddress.ip_address(host)
        addrs: list[str] = [host]
    except ValueError:
        try:
            addrs = list(resolver(host)) if resolver else list(_cached_resolve(host))
        except (socket.gaierror, OSError) as exc:
            raise SSRFError(f"Cannot resolve host '{host}': {exc}") from exc
    if not addrs:
        raise SSRFError(f"Host '{host}' did not resolve")
    for a in addrs:
        if is_forbidden_ip(a):
            raise SSRFError(
                f"Host '{host}' resolves to a private/loopback/link-local address ({a}); "
                "set ALLOW_LOCAL_TARGETS=true for local development"
            )
    return url.strip()


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme.lower()}://{(p.netloc or '').lower()}"


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_allowed_host(url: str, base_url: str, extra_domains: list[str]) -> bool:
    """Same-origin by default; plus an explicit allowlist of extra domains (and subdomains)."""
    h = host_of(url)
    if h == host_of(base_url):
        return True
    return any(h == d or h.endswith("." + d) for d in extra_domains)


def safe_http_client(*, allow_local: bool, timeout: float = 15.0, **kwargs: object) -> httpx.AsyncClient:
    """httpx client that re-validates every request – including each redirect hop."""

    async def _guard(request: httpx.Request) -> None:
        check_url(str(request.url), allow_local=allow_local)

    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [_guard]},
        **kwargs,  # type: ignore[arg-type]
    )
