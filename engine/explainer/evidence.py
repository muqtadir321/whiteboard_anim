"""Evidence collected for a topic (input/topics/<name>/evidence/manifest.json).

    {"news":     {"image": "news_cards.png", "cards": [{"box": [x0,y0,x1,y1], "title", "publisher", "url", "published"}]},
     "articles": [{"id": "1", "image": "article_1.png", "url", "title", "publisher",
                   "words": [["text", x0, y0, x1, y1], ...]}],          # boxes in image pixels
     "charts":   [{"id": "MORTGAGE30US", "title", "units": "%", "source", "url", "points": [["2021-01-07", 2.65], ...]}],
     "logos":    [{"name": "Fannie Mae", "image": "logo_fannie_mae.png", "url", "license"}]}

Everything here is read offline at render time; preparation writes it (engine.explainer.prepare).
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "manifest.json"


def norm_token(t: str) -> str:
    t = t.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"^[^\w$%]+|[^\w%]+$", "", t)


@dataclass
class Article:
    id: str
    image: Path
    url: str = ""
    title: str = ""
    publisher: str = ""
    words: list[tuple[str, float, float, float, float]] = field(default_factory=list)
    source_id: str = ""  # id in the research package's source ledger
    capture: str = "screenshot"  # screenshot | source_card (drawn from verified metadata)
    published: str = ""

    def find_quote(self, quote: str, min_ratio: float = 0.72) -> list[tuple[float, float, float, float]]:
        """Line rectangles (image pixels) covering the quoted words, [] if not on the page."""
        q = [w for w in (norm_token(x) for x in quote.split()) if w]
        page = [norm_token(w[0]) for w in self.words]
        if not q or not page:
            return []
        n = len(q)
        best, best_i, best_n = 0.0, -1, n
        for i in range(len(page)):
            for m in (n, n - 1, n + 1):
                if m <= 0 or i + m > len(page):
                    continue
                r = difflib.SequenceMatcher(None, q, page[i:i + m], autojunk=False).ratio()
                if r > best:
                    best, best_i, best_n = r, i, m
            if best >= 1.0:
                break
        if best < min_ratio:
            return []
        return merge_lines(self.words[best_i:best_i + best_n])


def merge_lines(words) -> list[tuple[float, float, float, float]]:
    """Merge word boxes into one rectangle per text line."""
    lines: list[list[float]] = []
    for _, x0, y0, x1, y1 in words:
        cy = (y0 + y1) / 2
        if lines and abs(cy - (lines[-1][1] + lines[-1][3]) / 2) < (y1 - y0) * 0.6 and x0 >= lines[-1][0] - 2:
            ln = lines[-1]
            ln[0], ln[1], ln[2], ln[3] = min(ln[0], x0), min(ln[1], y0), max(ln[2], x1), max(ln[3], y1)
        else:
            lines.append([x0, y0, x1, y1])
    return [tuple(ln) for ln in lines]


@dataclass
class Chart:
    id: str
    title: str
    units: str
    source: str
    url: str
    points: list[tuple[dt.date, float]]
    source_id: str = ""

    def nearest(self, date: str) -> tuple[dt.date, float] | None:
        d = parse_date(date)
        if d is None or not self.points:
            return None
        return min(self.points, key=lambda p: abs((p[0] - d).days))


def parse_date(s: str) -> dt.date | None:
    """`2021-01-07`, `2021-01` (mid-month) or `2021` (mid-year)."""
    s = (s or "").strip()
    for fmt, fix in (("%Y-%m-%d", lambda d: d), ("%Y-%m", lambda d: d.replace(day=15)), ("%Y", lambda d: d.replace(month=7, day=1))):
        try:
            return fix(dt.datetime.strptime(s, fmt).date())
        except ValueError:
            continue
    return None


def _file(root: Path, rel: str) -> Path:
    """Evidence image path with "../" folded away (research packages keep images in research/screenshots;
    unfolded paths can pass Windows' 260-character limit)."""
    return Path(os.path.normpath(root / rel))


def _sid(url: str, skip: str = "") -> str:
    """Topic folders made before the source ledger: the same stable id the ledger would give the URL."""
    from ..package.urls import UrlError, source_id, validate_url

    if not url or (skip and skip in url):
        return ""  # aggregator redirect links do not identify the publisher's document
    try:
        return source_id(validate_url(url))
    except UrlError:
        return ""


@dataclass
class Evidence:
    root: Path
    news_image: Path | None = None
    news_cards: list[dict] = field(default_factory=list)
    articles: dict[str, Article] = field(default_factory=dict)
    charts: dict[str, Chart] = field(default_factory=dict)
    logos: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "Evidence":
        root = Path(root)
        path = root / MANIFEST
        ev = cls(root)
        if not path.exists():
            return ev
        data = json.loads(path.read_text(encoding="utf-8"))
        news = data.get("news") or {}
        if news.get("image") and _file(root, news["image"]).exists():
            ev.news_image = _file(root, news["image"])
            ev.news_cards = [{**c, "source_id": c.get("source_id") or _sid(c.get("url", ""), "news.google.com")} for c in news.get("cards", [])]
        for a in data.get("articles", []):
            if _file(root, a["image"]).exists():
                ev.articles[str(a["id"])] = Article(str(a["id"]), _file(root, a["image"]), a.get("url", ""), a.get("title", ""), a.get("publisher", ""),
                                                     [tuple(w) for w in a.get("words", [])], a.get("source_id") or _sid(a.get("url", "")),
                                                     a.get("capture", "screenshot"),
                                                     a.get("published", ""))
        for c in data.get("charts", []):
            pts = [(d, float(v)) for d, v in ((parse_date(p[0]), p[1]) for p in c.get("points", [])) if d is not None and v is not None]
            if pts:
                ev.charts[c["id"].upper()] = Chart(c["id"].upper(), c.get("title", c["id"]), c.get("units", ""), c.get("source", ""), c.get("url", ""), pts,
                                                   c.get("source_id") or _sid(c.get("url", "")))
        for lg in data.get("logos", []):
            if _file(root, lg["image"]).exists():
                ev.logos[lg["name"].lower()] = {**lg, "path": _file(root, lg["image"])}
        return ev

    def logo(self, name: str) -> dict | None:
        key = name.lower().strip()
        if key in self.logos:
            return self.logos[key]
        close = difflib.get_close_matches(key, list(self.logos), n=1, cutoff=0.75)
        return self.logos[close[0]] if close else None

    def ledger(self):
        """A source ledger built from this evidence (for folders without research/sources.json)."""
        from ..package.ledger import Ledger, make_source

        led = Ledger()
        for c in self.news_cards:
            if c.get("source_id"):
                led.add(make_source(c["url"], c.get("title", ""), c.get("publisher", ""), "", source_type="news", retrieval_method="rss"))
        for a in self.articles.values():
            if a.source_id:
                led.add(make_source(a.url, a.title, a.publisher, "", source_type="news", retrieval_method="playwright"))
        for ch in self.charts.values():
            if ch.source_id:
                led.add(make_source(ch.url, ch.title, ch.source, "", source_type="dataset", retrieval_method="api"))
        return led

    def sources(self) -> list[dict]:
        out = [{"kind": "news", "title": c.get("title", ""), "publisher": c.get("publisher", ""), "url": c.get("url", "")} for c in self.news_cards]
        out += [{"kind": "article", "title": a.title, "publisher": a.publisher, "url": a.url} for a in self.articles.values()]
        out += [{"kind": "chart", "title": c.title, "publisher": c.source, "url": c.url} for c in self.charts.values()]
        out += [{"kind": "logo", "title": lg["name"], "publisher": lg.get("license", ""), "url": lg.get("url", "")} for lg in self.logos.values()]
        return out
