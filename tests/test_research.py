import datetime as dt
import json

from engine.research import cli
from engine.research import sources as S
from engine.research.analysis import is_finance, rank_topics
from engine.research.briefs import brief_markdown, seo_titles, slug, title_case

NOW = dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc)

TRENDS = b"""<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>gold price</title><ht:approx_traffic>50,000+</ht:approx_traffic><pubDate>Sun, 19 Oct 2026 08:00:00 -0700</pubDate>
<ht:news_item><ht:news_item_title>Gold price hits record high as dollar weakens</ht:news_item_title>
<ht:news_item_url>https://example.com/gold</ht:news_item_url></ht:news_item></item>
<item><title>football score</title><ht:approx_traffic>200K+</ht:approx_traffic><pubDate>Sun, 19 Oct 2026 08:00:00 -0700</pubDate></item>
</channel></rss>"""

YT = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
xmlns:media="http://search.yahoo.com/mrss/"><entry><yt:videoId>abc123</yt:videoId><title>Why the gold price keeps rising</title>
<published>2026-10-18T12:00:00+00:00</published><media:group><media:description>gold</media:description>
<media:community><media:statistics views="240000"/></media:community></media:group></entry>
<entry><yt:videoId>old1</yt:videoId><title>Gold price in 2020</title><published>2020-01-01T00:00:00+00:00</published>
<media:group><media:community><media:statistics views="9000000"/></media:community></media:group></entry></feed>"""

NEWS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Gold price rally: what it means for savers - Reuters</title><link>https://news.example/1</link>
<pubDate>Sat, 18 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Dubai salary guide: cost of living rises</title><link>https://news.example/2</link>
<pubDate>Sat, 18 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""

REDDIT = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Should I buy gold now that the gold price is so high?</title>
<link href="https://reddit.example/1"/><updated>2026-10-17T10:00:00+00:00</updated></entry></feed>"""


def signals():
    out = S.parse_google_trends(TRENDS, "US") + S.parse_youtube_feed(YT, "@chan")
    out += S.parse_rss_items(NEWS, "google_news", "US") + S.parse_rss_items(REDDIT, "reddit")
    return out


def test_parsers():
    tr = S.parse_google_trends(TRENDS, "US")
    assert tr[0].title == "gold price" and tr[0].traffic == 50000 and tr[1].traffic == 200000
    assert tr[0].extra["news"] == ["Gold price hits record high as dollar weakens"]
    yt = S.parse_youtube_feed(YT, "@chan")
    assert yt[0].views == 240000 and yt[0].url.endswith("abc123") and yt[0].age_hours(NOW) == 24
    news = S.parse_rss_items(NEWS, "google_news", "US")
    assert len(news) == 2 and news[0].url == "https://news.example/1"
    red = S.parse_rss_items(REDDIT, "reddit")
    assert red[0].url == "https://reddit.example/1" and red[0].published
    assert S.parse_autocomplete(b'["gold",["gold price","gold price today"]]') == ["gold price", "gold price today"]
    assert S.resolve_channel_id('x "channelId":"UC1234567890123456789012" y') == "UC1234567890123456789012"


def test_ranking_prefers_cross_source_finance_topic():
    auto = {"gold price": ["gold price today", "gold price prediction 2026", "why gold price is rising"]}
    ideas = rank_topics(signals(), auto, ["gold price", "dubai salary"], top_n=5, now=NOW)
    assert ideas and ideas[0].topic == "gold price"
    top = ideas[0]
    assert {"youtube", "trends", "news", "social", "search"} <= set(top.sources)
    assert top.potential == "High" and top.keywords
    # the five-year-old video must not count as momentum
    assert all("old1" not in e["url"] for e in top.evidence)
    assert not any("football" in i.topic for i in ideas)
    assert not is_finance("football score", [])


def test_brief_has_seo_and_outline_without_invented_numbers():
    idea = rank_topics(signals(), {}, ["gold price"], now=NOW)[0]
    md = brief_markdown(1, idea, 2026, {"english": "[curious] draft"})
    assert "## SEO titles" in md and "## Script outline (English)" in md and "Roman Urdu" in md
    assert "{VERIFY" in md and "[curious]" in md and "DRAFT script" in md
    assert "https://example.com/gold" in md or "https://www.youtube.com/watch?v=abc123" in md
    assert title_case("uae salary") == "UAE Salary" and slug("Gold Price!") == "gold-price"
    assert "2026" in seo_titles("gold price", 2026)["english"][0]


def test_cli_offline(tmp_path, monkeypatch):
    def fake_fetch(url, timeout, accept="*/*", retries=2):
        if "trends.google" in url:
            return TRENDS
        if "news.google" in url:
            return NEWS
        if "reddit" in url:
            return REDDIT
        if "suggestqueries" in url:
            return b'["q",["gold price today"]]'
        if "feeds/videos.xml" in url:
            return YT
        if "youtube.com/@" in url:
            return b'"channelId":"UC1234567890123456789012"'
        raise RuntimeError("blocked")

    monkeypatch.setattr(S, "fetch", fake_fetch)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    cfg = tmp_path / "c.yaml"
    cfg.write_text("niche: finance\nseed_keywords: [gold price]\nregions: [US]\nyoutube_channels: ['@chan']\nreddit_subreddits: [x]\n")
    out = tmp_path / "out"
    assert cli.main(["--config", str(cfg), "--out", str(out)]) == 0
    ideas = json.loads((out / "ideas.json").read_text())
    assert ideas[0]["topic"] == "gold price"
    assert (out / ideas[0]["brief"]).exists()
    report = (out / "report.md").read_text()
    assert "Gold Price" in report and "youtube_api" in report and "TikTok" in report
    assert "Gold Price" in summary.read_text()


def test_cli_fails_when_every_source_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("seed_keywords: [gold]\nregions: [US]\n")
    assert cli.main(["--config", str(cfg), "--out", str(tmp_path / "o")]) == 1
    assert json.loads((tmp_path / "o" / "sources.json").read_text())[0]["failed"] == 1


def test_phrases_keep_inner_small_words_and_drop_filler():
    from engine.research.analysis import phrases
    p = phrases("Social Security announces 3.6% cost-of-living adjustment", set())
    assert "cost of living" in p and "social security" in p and not any(x.startswith("6") for x in p)
    assert "much money" not in phrases("I Regret Saving Too Much Money", set())
