"""Where research looks for documents. Each provider fails on its own; coverage is recorded.

  user_urls        URLs you give (always first)
  official_feeds   RSS of government/central-bank publishers (research.official_feeds), filtered by topic
  gdelt            GDELT DOC 2.0 article search (free, no key; commercial use allowed with citation)
  bing_news_rss    OFF by default: its feed terms allow personal, non-commercial use only (restricted)
  google_news_rss  OFF by default: news.google.com/robots.txt disallows /rss for crawlers

Every request goes through http.Fetcher, so robots.txt, rate limits and the cache apply.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .http import Fetcher
from .urls import UrlError, domain, validate_url

PROVIDERS = {
    "official_feeds": {"commercial_status": "approved", "terms": "public sector publishers; check each feed's terms"},
    "gdelt": {"commercial_status": "approved", "terms": "https://www.gdeltproject.org/about.html#termsofuse (cite The GDELT Project)"},
    "bing_news_rss": {"commercial_status": "restricted", "terms": "feed copyright notice: personal, non-commercial use only"},
    "google_news_rss": {"commercial_status": "restricted", "terms": "robots.txt disallows /rss for crawlers"},
}


@dataclass
class Found:
    url: str
    title: str
    publisher: str
    published_at: str  # ISO date-time or ""
    provider: str
    snippet: str = ""
    source_type: str = "news"
    resolved: bool = True  # False: an aggregator link that needs a browser to reach the publisher


STOP = {"the", "and", "for", "with", "why", "how", "what", "that", "this", "from", "are", "was", "got", "get", "its", "your", "you",
        "about", "will", "into", "over", "after", "is", "a", "an", "of", "to", "in", "on", "at", "by"}


def topic_words(topic: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9$%.'-]+", topic) if w.lower() not in STOP and len(w) > 2]


def relevance(topic: str, text: str) -> float:
    words = {w.lower()[:5] for w in topic_words(topic)}
    low = text.lower()
    return sum(1 for w in words if w in low) / max(1, len(words))


def _iso(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def parse_rss(xml: bytes, provider: str, source_type: str = "news") -> list[Found]:
    root = ET.fromstring(xml)
    out = []
    for item in root.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        get = {c.tag.split("}")[-1].lower(): c for c in item}
        title = re.sub(r"\s+", " ", (get["title"].text or "") if "title" in get else "").strip()
        link_el = get.get("link")
        link = (link_el.text or link_el.get("href", "")).strip() if link_el is not None else ""
        src = get.get("source")
        publisher = (src.text or "").strip() if src is not None else ""
        date = _iso((get["pubdate"].text if "pubdate" in get else get["updated"].text if "updated" in get else "") or "")
        desc = re.sub(r"<[^>]+>", " ", (get["description"].text or "") if "description" in get else "")
        if title and link:
            out.append(Found(link, title, publisher, date, provider, re.sub(r"\s+", " ", desc).strip()[:300], source_type))
    return out


def official_feeds(f: Fetcher, topic: str, feeds: list[str], limit: int, errors: list[str] | None = None) -> list[Found]:
    """Each feed on its own: one blocked or broken feed does not hide the others (errors are collected)."""
    out = []
    for url in feeds:
        try:
            items = parse_rss(f.get(url, "application/rss+xml,application/xml,text/xml").body, "official_feeds", "official")
        except Exception as exc:
            if errors is None:
                raise
            errors.append(f"{url}: {getattr(exc, 'reason', exc)}")
            continue
        for it in items:
            if relevance(topic, it.title + " " + it.snippet) >= 0.5:
                it.publisher = it.publisher or domain(url)
                out.append(it)
    return out[:limit]


GDELT_COUNTRY = {"US": "unitedstates", "GB": "unitedkingdom", "UK": "unitedkingdom", "PK": "pakistan", "IN": "india",
                 "AE": "unitedarabemirates", "CA": "canada", "AU": "australia", "SA": "saudiarabia"}


def gdelt(f: Fetcher, topic: str, limit: int, days: int = 30, region: str = "global") -> list[Found]:
    q = " ".join(topic_words(topic)[:6]) + " sourcelang:english"
    country = GDELT_COUNTRY.get((region or "").upper())
    if country:  # research locale: publishers from that country
        q += f" sourcecountry:{country}"
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?" +
           urllib.parse.urlencode({"query": q, "mode": "artlist", "maxrecords": min(75, max(5, limit)), "format": "json",
                                   "timespan": f"{days}d", "sort": "hybridrel"}))
    body = f.get(url, "application/json").body
    try:
        data = json.loads(body)
    except ValueError as exc:  # GDELT answers rate limits and bad queries in plain text
        raise RuntimeError("GDELT: " + body[:160].decode("utf-8", "replace")) from exc
    return [Found(a["url"], a.get("title", ""), a.get("domain", ""), _iso(a.get("seendate", "")), "gdelt")
            for a in data.get("articles", []) if a.get("url")][:limit]


def bing_news(f: Fetcher, topic: str, limit: int) -> list[Found]:
    url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": topic, "format": "rss", "setlang": "en-US"})
    items = parse_rss(f.get(url, "application/rss+xml").body, "bing_news_rss")
    for it in items:  # apiclick links carry the publisher URL; no need to visit Bing again
        real = urllib.parse.parse_qs(urllib.parse.urlsplit(it.url).query).get("url", [""])[0]
        if real:
            it.url = real
            it.publisher = it.publisher or domain(real)
    return items[:limit]


def google_news(f: Fetcher, topic: str, limit: int) -> list[Found]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f"{topic} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    items = parse_rss(f.get(url, "application/rss+xml").body, "google_news_rss")
    for it in items:
        it.resolved = False  # news.google.com redirect pages need a browser
        if it.publisher and it.title.endswith(" - " + it.publisher):
            it.title = it.title[: -len(it.publisher) - 3]
    return items[:limit]


def clean_user_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for u in urls:
        u = u.strip()
        if not u:
            continue
        try:
            ok.append(validate_url(u))
        except UrlError as exc:
            bad.append(str(exc))
    return list(dict.fromkeys(ok)), bad
