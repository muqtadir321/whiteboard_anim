"""Text and metadata from an HTML page (standard library only; page content is data, never run).

Publication date, author and publisher come from <meta> tags, <time> and JSON-LD; paragraphs
from <p> elements outside navigation, asides and footers.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "nav", "footer", "aside", "form", "button", "svg", "figcaption", "template", "iframe"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_MARKERS = ("access denied", "just a moment", "attention required", "verify you are human", "are you a robot", "captcha",
                 "subscribe to continue", "subscribe to read", "please enable javascript", "403 forbidden", "sign in to continue")
DATE_META = ("article:published_time", "og:published_time", "datepublished", "date", "pubdate", "publishdate", "dc.date", "dc.date.issued",
             "citation_publication_date", "sailthru.date", "parsely-pub-date")


@dataclass
class Page:
    title: str = ""
    publisher: str = ""
    author: str = ""
    published_at: str = ""
    canonical: str = ""
    description: str = ""
    paragraphs: list[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.h1 = ""
        self.canonical = ""
        self.times: list[str] = []
        self.ld: list[str] = []
        self.paras: list[str] = []
        self.date_texts: list[str] = []  # text of elements whose class says date/time/published
        self._stack: list[str] = []
        self._skip = 0
        self._buf: list[str] | None = None
        self._grab: str | None = None  # title | h1 | ld

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
            return
        if tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = a.get("href", "")
            return
        if tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        if tag in VOID:
            return
        self._stack.append(tag)
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._grab, self._buf = "ld", []
            return
        if tag in SKIP:
            self._skip += 1
        elif tag in ("title", "h1") and not self._skip:
            self._grab, self._buf = tag, []
        elif re.search(r"date|time|publish", a.get("class", ""), re.I) and self._buf is None and not self._skip:
            self._grab, self._buf = "date", []  # before <p>: a dated paragraph is often a <p class="...time">
        elif tag in ("p", "li", "blockquote") and not self._skip and self._buf is None:
            self._grab, self._buf = "p", []

    def handle_endtag(self, tag):
        if tag in VOID or tag not in self._stack:
            return
        while self._stack:
            top = self._stack.pop()
            if top in SKIP and not (top == "script" and self._grab == "ld"):
                self._skip = max(0, self._skip - 1)
            if top == tag:
                break
        if self._buf is not None and (tag == self._grab or (self._grab == "p" and tag in ("p", "li", "blockquote")) or
                                      (self._grab == "date" and tag in ("p", "li", "div", "span", "time")) or
                                      (self._grab == "ld" and tag == "script")):
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if self._grab == "title" and not self.title:
                self.title = text
            elif self._grab == "h1" and not self.h1:
                self.h1 = text
            elif self._grab == "ld":
                self.ld.append("".join(self._buf))
            elif self._grab == "p" and len(text) >= 40:
                self.paras.append(text)
            elif self._grab == "date" and text:
                self.date_texts.append(text)
            self._grab, self._buf = None, None

    def handle_data(self, data):
        if self._buf is not None and (not self._skip or self._grab == "ld"):
            self._buf.append(data)


def _ld_fields(blobs: list[str]) -> dict:
    out: dict = {}

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for k in ("datePublished", "headline"):
                if k in o and k not in out and isinstance(o[k], str):
                    out[k] = o[k]
            au = o.get("author")
            if au and "author" not in out:
                names = [x.get("name", "") if isinstance(x, dict) else str(x) for x in (au if isinstance(au, list) else [au])]
                out["author"] = ", ".join(n for n in names if n)
            pub = o.get("publisher")
            if isinstance(pub, dict) and pub.get("name") and "publisher" not in out:
                out["publisher"] = pub["name"]
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for b in blobs:
        try:
            walk(json.loads(b))
        except (ValueError, TypeError):
            continue
    return out


def _parse_date(text: str) -> str:
    """'January 25, 2012' / '25 January 2012' / '2012-01-25' -> ISO date, else ''."""
    import datetime as dt

    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%B %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_html(text: str, max_paragraphs: int = 80) -> Page:
    p = _Parser()
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass  # malformed markup: keep what was parsed
    ld = _ld_fields(p.ld)
    m = p.meta
    date = (ld.get("datePublished") or next((m[k] for k in DATE_META if m.get(k)), "") or (p.times[0] if p.times else "")
            or next((d for d in (_parse_date(t) for t in p.date_texts) if d), ""))
    seen, paras = set(), []
    for t in p.paras:
        if t not in seen:
            seen.add(t)
            paras.append(html.unescape(t))
    page = Page(title=m.get("og:title") or ld.get("headline") or p.h1 or p.title, publisher=m.get("og:site_name") or ld.get("publisher", ""),
                author=m.get("author") or ld.get("author", ""), published_at=date.strip(), canonical=p.canonical,
                description=m.get("og:description") or m.get("description", ""), paragraphs=paras[:max_paragraphs])
    low = (page.title + " " + " ".join(paras[:3]) + " " + p.title).lower()
    page.blocked = len(page.text) < 300 and any(k in low for k in BLOCK_MARKERS)
    return page


def sentences(text: str) -> list[str]:
    """Sentence split for matching (keeps decimals and initials such as U.S. together)."""
    parts = re.split(r"(?<=[.!?؟۔])\s+(?=[\"'“(]?[A-Z0-9؀-ۿ])", text)
    out = []
    for s in parts:
        s = s.strip()
        if out and re.search(r"\b(?:[A-Z]\.){1,3}$|\b(?:Mr|Ms|Dr|Inc|Co|Corp|vs|No|St)\.$", out[-1]):
            out[-1] += " " + s
        elif s:
            out.append(s)
    return out
