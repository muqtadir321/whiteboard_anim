"""URL validation and canonical forms (one source id per document, whatever the tracking tags)."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import urllib.parse

TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|ref|ref_src|cmpid|ocid|smid|taid|_ga|guccounter|guce_.*|"
                      r"cid|src|sr_share|share|soc_src|soc_trk|at_medium|at_campaign|feature)$", re.I)


class UrlError(ValueError):
    pass


def validate_url(url: str) -> str:
    """http(s) only, no credentials, no local or private hosts (URLs come from users and web pages)."""
    url = (url or "").strip()
    if not url or any(c.isspace() or ord(c) < 32 for c in url):
        raise UrlError(f"not a URL: {url!r}")
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise UrlError(f"only http(s) URLs are allowed: {url!r}")
    if p.username or p.password:
        raise UrlError("URLs with credentials are not allowed")
    host = (p.hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UrlError(f"local host not allowed: {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise UrlError(f"private address not allowed: {url!r}")
    return url


def canonical_url(url: str) -> str:
    """Lower-case scheme/host, no default port, no fragment, no tracking parameters, sorted query."""
    p = urllib.parse.urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k))
    return urllib.parse.urlunsplit((scheme, host, path, urllib.parse.urlencode(query), ""))


def domain(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def short_url(url: str, limit: int = 42) -> str:
    """`domain/path` for an on-screen label."""
    p = urllib.parse.urlsplit(url)
    text = domain(url) + (p.path if p.path not in ("", "/") else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def source_id(url: str) -> str:
    """Stable id: the same canonical URL always gets the same id."""
    return "S-" + hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:10]
