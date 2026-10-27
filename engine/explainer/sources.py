"""Find articles about a topic (Google News RSS, free and public).

Results are ranked so the screenshots are likely to work and to convince: official and
data sources first, then publishers that render without a paywall; paywalled sites last.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..research.sources import NEWS_LOCALE, _clean, _date, fetch

OFFICIAL = (".gov", ".mil", "federalreserve", "imf.org", "worldbank.org", "oecd.org", "bis.org", "nar.realtor", "urban.org",
            "brookings.edu", "pewresearch.org", "sbp.org.pk", "pbs.gov.pk", "uaecb", "ecb.europa.eu", "bankofengland", ".edu",
            # neutral consumer and regulator sources (US/UK insurance, credit, investing)
            "naic.org", "iii.org", "finra.org", "fca.org.uk", "moneyhelper.org.uk", "consumerreports.org")
# "Cheapest X of 2026" / "Best X" lists are affiliate rankings: fine for prices, weak as proof of how something works
AFFILIATE_TITLE = re.compile(r"\b(cheapest|best|top \d+)\b.*\b20\d\d\b|\b(cheapest|best) .* (companies|providers|cards|plans|deals)\b", re.I)
OPEN_PUBLISHERS = ("apnews.com", "cnbc.com", "cbsnews.com", "abcnews.go.com", "nbcnews.com", "npr.org", "axios.com", "usatoday.com",
                   "cnn.com", "foxbusiness.com", "yahoo.com", "investopedia.com", "theguardian.com", "bbc.com", "bbc.co.uk",
                   "dawn.com", "tribune.com.pk", "khaleejtimes.com", "gulfnews.com", "thenationalnews.com", "realtor.com",
                   "redfin.com", "zillow.com", "bankrate.com", "nerdwallet.com", "fortune.com", "time.com", "newsweek.com")
PAYWALLED = ("wsj.com", "nytimes.com", "bloomberg.com", "ft.com", "washingtonpost.com", "economist.com", "barrons.com",
             "businessinsider.com", "marketwatch.com", "theatlantic.com", "telegraph.co.uk", "thetimes.co.uk")


@dataclass
class NewsItem:
    title: str
    url: str  # Google News link (redirects to the publisher)
    publisher: str
    publisher_url: str
    published: dt.datetime | None

    @property
    def domain(self) -> str:
        return urllib.parse.urlparse(self.publisher_url).netloc.lower().removeprefix("www.")

    def rank(self) -> int:
        d = self.domain
        if any(k in d for k in OFFICIAL):
            return 0
        if AFFILIATE_TITLE.search(self.title):
            return 2
        if any(d.endswith(k) for k in OPEN_PUBLISHERS):
            return 1
        if any(d.endswith(k) for k in PAYWALLED):
            return 3
        return 2

    def age_text(self, now: dt.datetime) -> str:
        if not self.published:
            return ""
        h = (now - self.published).total_seconds() / 3600
        if h < 1:
            return "just now"
        if h < 24:
            return f"{int(h)} hour{'s' if int(h) != 1 else ''} ago"
        d = int(h // 24)
        return f"{d} day{'s' if d != 1 else ''} ago"

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "publisher": self.publisher, "publisher_url": self.publisher_url,
                "published": self.published.isoformat() if self.published else None}


def parse_news(xml_bytes: bytes) -> list[NewsItem]:
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title") or "")
        src = item.find("source")
        publisher = (src.text or "").strip() if src is not None else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        out.append(NewsItem(title, (item.findtext("link") or "").strip(), publisher,
                            src.get("url", "") if src is not None else "", _date(item.findtext("pubDate") or "")))
    return [n for n in out if n.title and n.url]


def search_news(topic: str, region: str = "US", timeout: float = 20, days: int = 30) -> list[NewsItem]:
    hl, ceid = NEWS_LOCALE.get(region, ("en-US", "US:en"))
    seen: set[str] = set()
    items: list[NewsItem] = []
    for q in (f"{topic} when:{days}d", f"{topic} report OR data OR study when:{days * 3}d"):
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}&gl={region}&ceid={ceid}"
        try:
            found = parse_news(fetch(url, timeout))
        except Exception:
            continue
        for n in found:
            key = re.sub(r"\W+", " ", n.title.lower()).strip()
            if key not in seen:
                seen.add(key)
                items.append(n)
    now = dt.datetime.now(dt.timezone.utc)
    return sorted(items, key=lambda n: (n.rank(), -(n.published or now - dt.timedelta(days=365)).timestamp()))
