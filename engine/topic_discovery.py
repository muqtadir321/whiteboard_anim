"""Optional automatic topic discovery from configured public RSS feeds.

Disabled by default. Builds a short script from the first feed item using a
deterministic template (title + summary sentences). No text is translated or
invented, so only languages the feed is written in are supported (English by
default).
"""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET

from .results import StageError, Status
from .retry import with_retries


def _fetch(url: str, timeout: float) -> bytes:
    if not url.startswith("https://"):
        raise StageError(f"Only https feeds are allowed: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "whiteboard-engine/1.0 (+RSS topic discovery)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("xml", "rss", "atom", "text")):
                raise StageError(f"Unexpected content type {ctype!r} from {url}")
            return resp.read(2_000_000)
    except StageError:
        raise
    except Exception as exc:
        raise StageError(f"Feed download failed: {exc}", Status.RETRYABLE_FAILURE) from exc


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.split("}")[-1]: (c.text or "") for c in it}
        title = html.unescape(re.sub(r"<[^>]+>", " ", fields.get("title", ""))).strip()
        desc = html.unescape(re.sub(r"<[^>]+>", " ", fields.get("description") or fields.get("summary") or "")).strip()
        if title:
            items.append({"title": re.sub(r"\s+", " ", title), "summary": re.sub(r"\s+", " ", desc)})
    return items


def script_from_item(item: dict) -> str:
    title = item["title"].rstrip(".!?")
    sentences = re.split(r"(?<=[.!?])\s+", item["summary"])
    body = " ".join(s for s in sentences[:3] if s)
    return f"Today's topic: {title}. {body}".strip()


def discover_topic(cfg: dict, log=print) -> str:
    a = cfg["automation"]
    errors = []
    for url in a["topic_feeds"]:
        try:
            data = with_retries(lambda: _fetch(url, a["download_timeout_seconds"]), a["retry_limit"], a["retry_base_seconds"], log)
            items = parse_feed(data)
            if items:
                return script_from_item(items[0])
            errors.append(f"{url}: no items")
        except (StageError, ET.ParseError) as exc:
            errors.append(f"{url}: {exc}")
    raise StageError("Automatic topic discovery failed: " + "; ".join(errors), Status.PERMANENT_FAILURE)
