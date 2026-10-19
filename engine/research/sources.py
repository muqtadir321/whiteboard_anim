"""Signal collectors for viral-topic research.

Every collector returns a list of Signal objects and never raises: failures are
recorded in a SourceStatus so the report can say exactly which sources worked.
Only public feeds and documented APIs are used; nothing logs in or bypasses limits.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (X11; Linux x86_64) whiteboard-engine-research/1.0 (+https://github.com)"


@dataclass
class Signal:
    source: str  # google_trends | google_news | youtube | youtube_api | reddit | autocomplete
    title: str
    url: str = ""
    region: str = ""
    published: dt.datetime | None = None
    views: float = 0.0
    traffic: float = 0.0  # Google Trends approximate searches
    score: float = 0.0  # reddit score etc.
    extra: dict = field(default_factory=dict)

    def age_hours(self, now: dt.datetime) -> float | None:
        if not self.published:
            return None
        return max(0.5, (now - self.published).total_seconds() / 3600.0)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "published"}
        d["published"] = self.published.isoformat() if self.published else None
        return d


@dataclass
class SourceStatus:
    name: str
    ok: int = 0
    failed: int = 0
    signals: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__, errors=self.errors[:5])


def fetch(url: str, timeout: float, accept: str = "*/*", retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(5_000_000)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (400, 401, 403, 404):
                break  # not retryable
        except Exception as exc:  # network hiccup
            last = exc
        time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def _text(el, path: str, ns: dict | None = None) -> str:
    x = el.find(path, ns or {})
    return (x.text or "").strip() if x is not None and x.text else ""


def _clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _date(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _traffic(s: str) -> float:
    m = re.match(r"([\d,.]+)\s*([KkMm]?)", s or "")
    if not m:
        return 0.0
    v = float(m.group(1).replace(",", ""))
    return v * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)


# ---------------------------------------------------------------------------
# Parsers (pure functions, unit-tested with fixtures)
# ---------------------------------------------------------------------------

def parse_google_trends(xml_bytes: bytes, region: str) -> list[Signal]:
    root = ET.fromstring(xml_bytes)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for item in root.iter("item"):
        title = _clean(_text(item, "title"))
        traffic = _traffic(_text(item, "ht:approx_traffic", ns))
        news = [_clean(_text(n, "ht:news_item_title", ns)) for n in item.findall("ht:news_item", ns)]
        link = _text(item.find("ht:news_item", ns), "ht:news_item_url", ns) if item.find("ht:news_item", ns) is not None else ""
        out.append(Signal("google_trends", title, link, region, _date(_text(item, "pubDate")), traffic=traffic, extra={"news": news}))
    return out


def parse_rss_items(xml_bytes: bytes, source: str, region: str = "") -> list[Signal]:
    """Generic RSS 2.0 / Atom parser (Google News, Reddit, news sites)."""
    root = ET.fromstring(xml_bytes)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in list(root.iter("item")) + list(root.iter(f"{atom}entry")):
        title = _clean(_text(item, "title") or _text(item, f"{atom}title"))
        link = _text(item, "link")
        if not link:
            le = item.find(f"{atom}link")
            link = le.get("href", "") if le is not None else ""
        pub = _date(_text(item, "pubDate") or _text(item, f"{atom}updated") or _text(item, f"{atom}published"))
        if title:
            out.append(Signal(source, title, link, region, pub))
    return out


def parse_youtube_feed(xml_bytes: bytes, channel: str) -> list[Signal]:
    ns = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall("a:entry", ns):
        title = _clean(_text(e, "a:title", ns))
        vid = _text(e, "yt:videoId", ns)
        stats = e.find("media:group/media:community/media:statistics", ns)
        views = float(stats.get("views", 0)) if stats is not None else 0.0
        desc = _clean(_text(e, "media:group/media:description", ns))[:400]
        out.append(Signal("youtube", title, f"https://www.youtube.com/watch?v={vid}", "", _date(_text(e, "a:published", ns)),
                          views=views, extra={"channel": channel, "description": desc}))
    return out


def parse_autocomplete(payload: bytes) -> list[str]:
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
        return [s if isinstance(s, str) else s[0] for s in data[1]]
    return []


def resolve_channel_id(page_html: str) -> str | None:
    for pat in (r'"channelId":"(UC[\w-]{22})"', r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                r'"externalId":"(UC[\w-]{22})"', r'channel/(UC[\w-]{22})'):
        m = re.search(pat, page_html)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_google_trends(regions: list[str], timeout: float, st: SourceStatus) -> list[Signal]:
    out = []
    for r in regions:
        try:
            out += parse_google_trends(fetch(f"https://trends.google.com/trending/rss?geo={r}", timeout), r)
            st.ok += 1
        except Exception as exc:
            st.failed += 1
            st.errors.append(f"{r}: {exc}")
    st.signals = len(out)
    return out


NEWS_LOCALE = {"US": ("en-US", "US:en"), "GB": ("en-GB", "GB:en"), "PK": ("en-PK", "PK:en"), "IN": ("en-IN", "IN:en"), "AE": ("en-AE", "AE:en")}


def collect_google_news(seeds: list[str], regions: list[str], timeout: float, st: SourceStatus, max_queries: int = 40) -> list[Signal]:
    out = []
    n = 0
    for r in regions:
        hl, ceid = NEWS_LOCALE.get(r, ("en-US", "US:en"))
        for seed in seeds:
            if n >= max_queries:
                break
            q = urllib.parse.quote(f"{seed} when:7d")
            url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={r}&ceid={ceid}"
            try:
                sig = parse_rss_items(fetch(url, timeout), "google_news", r)
                for s in sig:
                    s.extra["seed"] = seed
                out += sig[:20]
                st.ok += 1
            except Exception as exc:
                st.failed += 1
                st.errors.append(f"{r}/{seed}: {exc}")
            n += 1
    st.signals = len(out)
    return out


def collect_youtube_channels(handles: list[str], timeout: float, st: SourceStatus, api_key: str = "") -> list[Signal]:
    out = []
    for h in handles:
        try:
            cid = None
            if api_key:
                data = json.loads(fetch("https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(
                    {"part": "id", "forHandle": h, "key": api_key}), timeout))
                items = data.get("items") or []
                cid = items[0]["id"] if items else None
            if not cid:
                cid = resolve_channel_id(fetch(f"https://www.youtube.com/{h}", timeout).decode("utf-8", errors="replace"))
            if not cid:
                raise RuntimeError("channel id not found")
            out += parse_youtube_feed(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", timeout), h)
            st.ok += 1
        except Exception as exc:
            st.failed += 1
            st.errors.append(f"{h}: {exc}")
    st.signals = len(out)
    return out


def collect_youtube_api(seeds: list[str], regions: list[str], api_key: str, lookback_days: int, timeout: float, st: SourceStatus,
                        max_queries: int = 25) -> list[Signal]:
    """Official YouTube Data API v3 (free quota; needs the YOUTUBE_API_KEY secret)."""
    if not api_key:
        st.errors.append("YOUTUBE_API_KEY secret not set (optional)")
        return []
    out = []
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for r in regions[:3]:
        for seed in seeds:
            if n >= max_queries:
                break
            n += 1
            try:
                q = {"part": "snippet", "q": seed, "type": "video", "order": "viewCount", "publishedAfter": after,
                     "regionCode": r, "maxResults": 15, "key": api_key}
                data = json.loads(fetch("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(q), timeout))
                ids = [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
                if not ids:
                    st.ok += 1
                    continue
                stats = json.loads(fetch("https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
                    {"part": "statistics,snippet,contentDetails", "id": ",".join(ids), "key": api_key}), timeout))
                for v in stats.get("items", []):
                    sn, stt = v["snippet"], v.get("statistics", {})
                    out.append(Signal("youtube_api", _clean(sn["title"]), f"https://www.youtube.com/watch?v={v['id']}", r,
                                      _date(sn.get("publishedAt", "")), views=float(stt.get("viewCount", 0)),
                                      score=float(stt.get("likeCount", 0)), extra={"channel": sn.get("channelTitle"), "seed": seed,
                                                                                     "comments": float(stt.get("commentCount", 0)),
                                                                                     "duration": v.get("contentDetails", {}).get("duration")}))
                st.ok += 1
            except Exception as exc:
                st.failed += 1
                st.errors.append(f"{r}/{seed}: {exc}")
    st.signals = len(out)
    return out


def collect_autocomplete(seeds: list[str], timeout: float, st: SourceStatus) -> dict[str, list[str]]:
    """YouTube search suggestions = what people are typing (SEO keywords)."""
    out: dict[str, list[str]] = {}
    for seed in seeds:
        for q in (seed, f"{seed} 2026", f"how {seed}", f"why {seed}"):
            try:
                url = "https://suggestqueries.google.com/complete/search?" + urllib.parse.urlencode({"client": "firefox", "ds": "yt", "q": q})
                sugg = parse_autocomplete(fetch(url, timeout))
                out.setdefault(seed, [])
                out[seed] += [s for s in sugg if s not in out[seed]]
                st.ok += 1
            except Exception as exc:
                st.failed += 1
                st.errors.append(f"{q}: {exc}")
    st.signals = sum(len(v) for v in out.values())
    return out


def collect_reddit(subs: list[str], timeout: float, st: SourceStatus) -> list[Signal]:
    out = []
    for sub in subs:
        try:
            sig = parse_rss_items(fetch(f"https://www.reddit.com/r/{sub}/top/.rss?t=week", timeout), "reddit")
            for s in sig:
                s.extra["subreddit"] = sub
            out += sig[:25]
            st.ok += 1
        except Exception as exc:
            st.failed += 1
            st.errors.append(f"r/{sub}: {exc}")
    st.signals = len(out)
    return out
