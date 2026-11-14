"""Can a tool be shown inside DadsOps? Probe its response headers for framing rules.

A browser refuses to render a page in an iframe when the page sends
`X-Frame-Options: DENY` / `SAMEORIGIN` (DadsOps and the tool are different origins:
same host, different port) or a Content-Security-Policy `frame-ancestors` directive
that does not list the DadsOps origin. As in browsers, an enforced `frame-ancestors`
directive wins over X-Frame-Options. Report-only policies are ignored.
"""

from __future__ import annotations

import http.client
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5
DEFAULT_PORTS = {"http": 80, "https": 443}
HOST_SOURCE_RE = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>\*|(?:\*\.)?[a-z0-9.-]+|\[[0-9a-f:.]+\])"
    r"(?::(?P<port>\*|[0-9]+))?"
    r"(?P<path>/.*)?$"
)


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int

    @classmethod
    def parse(cls, url: str) -> Origin:
        parts = urlsplit(url)
        scheme = parts.scheme.lower() or "http"
        port = parts.port or DEFAULT_PORTS.get(scheme, 0)
        return cls(scheme, (parts.hostname or "").lower(), port)

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class FrameCheck:
    """allowed, or the header line that blocks framing, or why the tool could not be probed."""

    allowed: bool
    blocked_by: str | None = None
    error: str | None = None


def check_framing(url: str, embedder: str, timeout: float = 3.0) -> FrameCheck:
    """GET url (following same-origin redirects) and decide whether embedder may frame it."""
    try:
        headers = fetch_headers(url, timeout)
    except (OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        return FrameCheck(allowed=False, error=f"cannot reach {url}: {reason}")
    blocked_by = frame_blocker(headers, Origin.parse(embedder), Origin.parse(url))
    return FrameCheck(allowed=blocked_by is None, blocked_by=blocked_by)


def fetch_headers(url: str, timeout: float) -> list[tuple[str, str]]:
    target = Origin.parse(url)
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
        try:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn.request("GET", path)
            response = conn.getresponse()
            headers = response.getheaders()
            location = response.getheader("location")
            status = response.status
        finally:
            conn.close()
        if status not in REDIRECTS or not location:
            return headers
        next_url = urljoin(url, location)
        if Origin.parse(next_url) != target:
            return headers  # leaves the tool: these are the tool's own last headers
        url = next_url
    return headers


def frame_blocker(headers: list[tuple[str, str]], embedder: Origin, tool: Origin) -> str | None:
    """The header (as "Name: value") that stops embedder from framing tool, or None."""
    policies = [value for name, value in headers if name.lower() == "content-security-policy"]
    has_frame_ancestors = False
    for header in policies:
        for policy in header.split(","):
            sources = frame_ancestors(policy)
            if sources is None:
                continue
            has_frame_ancestors = True
            if not any(source_matches(s, embedder, tool) for s in sources):
                return f"Content-Security-Policy: frame-ancestors {' '.join(sources)}".rstrip()
    if has_frame_ancestors:
        return None  # frame-ancestors present and satisfied: X-Frame-Options is ignored

    options = [value for name, value in headers if name.lower() == "x-frame-options"]
    values = {v.strip().upper() for header in options for v in header.split(",")}
    if "DENY" in values:
        return "X-Frame-Options: DENY"
    if "SAMEORIGIN" in values and embedder != tool:
        return "X-Frame-Options: SAMEORIGIN"
    return None


def frame_ancestors(policy: str) -> list[str] | None:
    """The source list of the first frame-ancestors directive in one policy, or None."""
    for directive in policy.split(";"):
        tokens = directive.split()
        if tokens and tokens[0].lower() == "frame-ancestors":
            return tokens[1:]
    return None


def source_matches(source: str, embedder: Origin, tool: Origin) -> bool:
    source = source.lower()
    if source == "*":
        return embedder.scheme in ("http", "https")
    if source == "'self'":
        return embedder == tool
    if source.startswith("'"):
        return False  # 'none' and every other keyword match nothing
    if re.fullmatch(r"[a-z][a-z0-9+.-]*:", source):
        return scheme_matches(source[:-1], embedder.scheme)
    match = HOST_SOURCE_RE.match(source)
    if match is None:
        return False
    scheme = match["scheme"] or tool.scheme
    if not scheme_matches(scheme, embedder.scheme):
        return False
    host = match["host"].strip("[]")
    if host.startswith("*."):
        if not embedder.host.endswith(host[1:]):
            return False
    elif host != "*" and host != embedder.host:
        return False
    port = match["port"]
    if port == "*":
        return True
    wanted = int(port) if port else DEFAULT_PORTS.get(scheme)
    return wanted == embedder.port


def scheme_matches(source: str, actual: str) -> bool:
    return source == actual or (source == "http" and actual == "https")
