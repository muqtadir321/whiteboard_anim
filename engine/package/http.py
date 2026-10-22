"""Polite HTTP for research: robots.txt, per-host rate limit, bounded retries, TTL cache with provenance.

Every request identifies itself (research.user_agent). Pages that robots.txt disallows are not
fetched; 401/403/451 are never retried; 429/5xx back off (Retry-After is honoured, capped).
Responses are cached under $WHITEBOARD_CACHE/research (or a given folder) with the URL, status,
content type and fetch time next to the body, and reused until the TTL runs out.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path

from .urls import validate_url

MAX_BYTES = 15_000_000
# hosts that publish their own limits (requests per second): GDELT asks for one request every 5 s
HOST_INTERVALS = {"api.gdeltproject.org": 5.5, "www.sec.gov": 0.17, "data.sec.gov": 0.17}


class FetchError(Exception):
    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{reason}: {url}")
        self.url, self.reason, self.status = url, reason, status


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str
    fetched_at: str  # ISO UTC
    from_cache: bool = False

    @property
    def text(self) -> str:
        charset = "utf-8"
        if "charset=" in self.content_type:
            charset = self.content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")


def default_cache_dir() -> Path:
    return Path(os.environ.get("WHITEBOARD_CACHE") or Path.home() / ".cache" / "whiteboard") / "research"


def urllib_opener(url: str, headers: dict, timeout: float) -> tuple[int, str, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), dict(resp.headers), resp.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, url, dict(exc.headers or {}), b""


class Fetcher:
    """`opener(url, headers, timeout) -> (status, final_url, headers, body)` is injectable for tests."""

    def __init__(self, user_agent: str, timeout: float = 20, retries: int = 2, backoff: float = 2.0, min_interval: float = 1.0,
                 cache_dir: Path | None = None, ttl_hours: float = 24, respect_robots: bool = True, opener=None, sleep=time.sleep,
                 max_retry_after: float = 30.0):
        self.ua = user_agent
        self.timeout, self.retries, self.backoff, self.min_interval = timeout, retries, backoff, min_interval
        self.cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
        self.ttl = ttl_hours * 3600
        self.respect_robots = respect_robots
        self.opener = opener or urllib_opener
        self.sleep = sleep
        self.max_retry_after = max_retry_after
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.log: list[dict] = []  # one row per network request (for research.json)

    @classmethod
    def from_config(cls, rc: dict, **kw) -> "Fetcher":
        ua = os.environ.get("RESEARCH_USER_AGENT") or rc["user_agent"]
        cache = Path(rc["cache_dir"]) if rc.get("cache_dir") else None
        return cls(ua, rc["timeout_seconds"], rc["retries"], rc["backoff_seconds"], rc["min_interval_seconds"], cache,
                   rc["cache_ttl_hours"], rc["respect_robots_txt"], **kw)

    # -- cache ------------------------------------------------------------------
    def _key(self, url: str, accept: str) -> Path:
        return self.cache_dir / hashlib.sha256(f"{url}\n{accept}".encode()).hexdigest()

    def _cached(self, url: str, accept: str) -> FetchResult | None:
        if self.ttl <= 0:
            return None
        key = self._key(url, accept)
        meta_p, body_p = key.with_suffix(".json"), key.with_suffix(".bin")
        if not meta_p.exists() or not body_p.exists():
            return None
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(meta["fetched_at"])).total_seconds()
        except Exception:
            return None
        if age > self.ttl:
            return None
        return FetchResult(url, meta["final_url"], meta["status"], body_p.read_bytes(), meta["content_type"], meta["fetched_at"], True)

    def _store(self, res: FetchResult, accept: str) -> None:
        if self.ttl <= 0:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._key(res.url, accept)
            key.with_suffix(".bin").write_bytes(res.body)
            key.with_suffix(".json").write_text(json.dumps({"url": res.url, "final_url": res.final_url, "status": res.status,
                                                            "content_type": res.content_type, "fetched_at": res.fetched_at,
                                                            "user_agent": self.ua}), encoding="utf-8")
        except OSError:
            pass  # a read-only cache never fails research

    # -- politeness -------------------------------------------------------------
    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = max(self.min_interval, HOST_INTERVALS.get(host, 0.0)) - (time.monotonic() - last)
            if gap > 0:
                self.sleep(gap)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            rp = None
            try:
                self._wait_turn(p.netloc)
                status, _, _, body = self.opener(host + "/robots.txt", {"User-Agent": self.ua}, self.timeout)
                if status == 200:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(body.decode("utf-8", "replace").splitlines())
                elif status in (401, 403):
                    rp = urllib.robotparser.RobotFileParser()
                    rp.disallow_all = True
            except Exception:
                rp = None  # robots.txt unreachable: treated as no restrictions (RFC 9309)
            self._robots[host] = rp
        rp = self._robots[host]
        return rp is None or rp.can_fetch(self.ua, url)

    # -- fetch ------------------------------------------------------------------
    def get(self, url: str, accept: str = "*/*", use_cache: bool = True) -> FetchResult:
        validate_url(url)
        if use_cache:
            hit = self._cached(url, accept)
            if hit:
                return hit
        if not self.allowed(url):
            raise FetchError(url, "blocked by robots.txt")
        host = urllib.parse.urlsplit(url).netloc
        headers = {"User-Agent": self.ua, "Accept": accept, "Accept-Language": "en;q=0.9,*;q=0.5"}
        attempt = 0
        while True:
            self._wait_turn(host)
            t0 = time.monotonic()
            try:
                status, final, rh, body = self.opener(url, headers, self.timeout)
            except Exception as exc:
                status, final, rh, body = None, url, {}, b""
                err = f"{type(exc).__name__}: {exc}"
            else:
                err = ""
            self.log.append({"url": url, "status": status, "error": err, "seconds": round(time.monotonic() - t0, 2), "attempt": attempt + 1})
            if status is not None and 200 <= status < 300:
                ctype = next((v for k, v in rh.items() if k.lower() == "content-type"), "")
                res = FetchResult(url, final, status, body, ctype, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
                self._store(res, accept)
                return res
            retryable = status is None or status == 429 or status >= 500
            if not retryable or attempt >= self.retries:
                raise FetchError(url, err or f"HTTP {status}", status)
            wait = self.backoff * (2 ** attempt)
            ra = next((v for k, v in rh.items() if k.lower() == "retry-after"), "")
            if ra.strip().isdigit():
                wait = max(wait, float(ra))
            self.sleep(min(wait, self.max_retry_after))
            attempt += 1
