"""Offline fixtures for the research package: a fake web (robots.txt, pages, feeds, APIs) and scripts."""

from __future__ import annotations

import datetime as dt
import json
import math

from engine.package.http import Fetcher

ARTICLE_1 = """<html><head><title>Mortgage rates climb - Example News</title>
<meta property="og:site_name" content="Example News"><meta property="article:published_time" content="2026-10-15T10:00:00Z">
<meta name="author" content="A. Reporter"><link rel="canonical" href="https://news.example.com/rates-climb?utm_source=x"></head>
<body><nav><p>Home | Markets | Housing | Subscribe to our newsletter today</p></nav><article><h1>Mortgage rates climb to 6.8%</h1>
<p>The average 30-year mortgage rate rose to 6.8% this week, the highest level since July, according to Freddie Mac data.</p>
<p>Economists said the rise reflects higher Treasury yields. Home sales fell 4.5% in September from the month before.</p>
<p>Buyers who locked in rates around 3% in 2021 are reluctant to sell, which keeps the number of listings low across the country.</p>
</article><footer><p>Copyright Example News. All rights reserved worldwide and everywhere.</p></footer></body></html>"""

ARTICLE_2 = """<html><head><title>Housing report</title><meta property="og:site_name" content="Housing Agency"></head>
<body><main><h1>Lock-in effect report</h1>
<p>This paper finds that for every percentage point that market rates exceed the origination rate, the probability of sale is decreased by 18.1%.</p>
<p>The lock-in effect prevented 1.33 million sales between 2022 and 2023 according to the agency's estimates for the period.</p>
</main></body></html>"""

BLOCKED = "<html><head><title>Just a moment...</title></head><body><p>Verify you are human.</p></body></html>"

FEED = """<?xml version="1.0"?><rss><channel><title>Agency press</title>
<item><title>Agency publishes mortgage rates and housing lock-in report</title><link>https://agency.example.gov/report</link>
<pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><description>New data on mortgage rates.</description></item>
<item><title>Unrelated fishing permits</title><link>https://agency.example.gov/fish</link></item>
</channel></rss>"""


def gdelt_json() -> str:
    arts = [{"url": "https://news.example.com/rates-climb", "title": "Mortgage rates climb to 6.8% as buyers wait", "seendate": "20261015T100000Z",
             "domain": "news.example.com"},
            {"url": "https://blocked.example.net/rates", "title": "Mortgage rates and housing: what buyers should know", "seendate": "20261014T100000Z",
             "domain": "blocked.example.net"}]
    return json.dumps({"articles": arts})


def fred_csv() -> str:
    d0 = dt.date(2019, 1, 3)
    rows = [f"{d0 + dt.timedelta(weeks=k)},{round(5 + 1.8 * math.sin(k / 40), 2)}" for k in range(380)]
    return "observation_date,MORTGAGE30US\n" + "\n".join(rows) + "\n"


class FakeWeb:
    """opener(url, headers, timeout) for http.Fetcher; records every request."""

    def __init__(self, extra: dict | None = None):
        self.pages = {
            "https://news.example.com/robots.txt": (200, "User-agent: *\nDisallow: /private\n"),
            "https://news.example.com/rates-climb": (200, ARTICLE_1),
            "https://agency.example.gov/robots.txt": (404, ""),
            "https://agency.example.gov/report": (200, ARTICLE_2),
            "https://agency.example.gov/feed.xml": (200, FEED),
            "https://blocked.example.net/robots.txt": (200, "User-agent: *\nDisallow: /\n"),
            "https://paywall.example.org/robots.txt": (404, ""),
            "https://paywall.example.org/story": (200, BLOCKED),
            "https://api.gdeltproject.org/robots.txt": (404, ""),
            "https://fred.stlouisfed.org/robots.txt": (404, ""),
        }
        self.pages.update(extra or {})
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        assert headers.get("User-Agent"), "every request identifies itself"
        if url.startswith("https://api.gdeltproject.org/api/v2/doc/doc"):
            return 200, url, {"Content-Type": "application/json"}, gdelt_json().encode()
        if url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv"):
            return 200, url, {"Content-Type": "text/csv"}, fred_csv().encode()
        status, body = self.pages.get(url, (404, ""))
        return status, url, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")


def fake_fetcher(tmp_path, web: FakeWeb | None = None) -> Fetcher:
    return Fetcher("test-agent/1.0 (+https://example.invalid)", timeout=5, retries=1, backoff=0, min_interval=0, cache_dir=tmp_path / "http-cache",
                   ttl_hours=0, opener=web or FakeWeb(), sleep=lambda s: None)


def research_cfg(cfg: dict) -> dict:
    cfg = json.loads(json.dumps(cfg))
    cfg["research"]["official_feeds"] = ["https://agency.example.gov/feed.xml"]
    cfg["research"]["max_articles"] = 2
    cfg["research"]["finance"]["sec_edgar"] = False
    cfg["animation"]["explainer"]["semantic"] = False
    return cfg


ENGLISH = ("Mortgage rates just climbed to 6.8 percent this week.\n\n"
           "Buyers who locked in rates around 3% in 2021 do not want to sell.\n\n"
           "For every point, the probability of sale is decreased by 18.1%.\n\n"
           "Prices in Atlantis rose 42% last year.\n\n"
           "Will rates fall next year?\n")
URDU = ("مارگیج ریٹ اس ہفتے 6.8 فیصد تک پہنچ گیا۔\n\n"
        "جن لوگوں نے 2021 میں 3% پر قرض لیا وہ گھر نہیں بیچنا چاہتے۔\n\n"
        "ہر ایک پوائنٹ پر گھر بکنے کا امکان 18.1% کم ہو جاتا ہے۔\n\n"
        "اٹلانٹس میں قیمتیں پچھلے سال 42% بڑھیں۔\n\n"
        "کیا اگلے سال ریٹ کم ہوں گے؟\n")
ROMAN_URDU = ("Mortgage rate is hafte 6.8 fisad tak pohanch gaya.\n\n"
              "Jin logon ne 2021 mein 3% par qarz liya woh ghar nahi bechna chahte.\n")
