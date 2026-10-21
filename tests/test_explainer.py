"""Explainer style: cue tags, evidence matching, layout, rendering, draft checks, end to end."""

import datetime as dt
import json
import math
import subprocess

import cairo
import pytest

from engine import cli
from engine.audio_timing import TimedSentence
from engine.config import load_config
from engine.explainer import draft, fred
from engine.explainer.charts import ChartPainter
from engine.explainer.cues import extract_cues, has_unresolved
from engine.explainer.evidence import Article, Evidence, merge_lines, parse_date
from engine.explainer.layout import ExplainerLayout, is_doodle, time_cues
from engine.explainer.render import ExplainerRenderer
from engine.explainer.sources import parse_news
from engine.scene_planner import Segment, plan_scenes
from engine.script_analyzer import analyze_script, normalize_script, normalize_script_with_cues

from .conftest import ROOT, tone

PAGE_TEXT = ("People can be locked in to their homes. This paper finds that for every percentage point that market rates exceed "
             "the origination rate, the probability of sale is decreased by 18.1%. This lock-in led to a 57% reduction in home sales "
             "and prevented 1.33 million sales between 2022 and 2023. ") * 2


def make_topic(root):
    """A small topic folder: article screenshot with word boxes, headline cards, a chart and a logo."""
    ev = root / "evidence"
    ev.mkdir(parents=True)
    w, h = 1200, 1400
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c = cairo.Context(s)
    c.set_source_rgb(1, 1, 1)
    c.paint()
    c.select_font_face("Sans")
    c.set_font_size(30)
    c.set_source_rgb(0.1, 0.1, 0.1)
    words, x, y = [], 40, 120
    for word in PAGE_TEXT.split():
        adv = c.text_extents(word + " ").x_advance
        if x + adv > w - 40:
            x, y = 40, y + 46
        c.move_to(x, y)
        c.show_text(word)
        words.append([word, x, y - 28, x + c.text_extents(word).x_advance, y + 8])
        x += adv
    s.write_to_png(str(ev / "article_1.png"))
    cards = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 300)
    cc = cairo.Context(cards)
    cc.set_source_rgb(0.13, 0.14, 0.16)
    cc.rectangle(10, 10, 700, 120)
    cc.fill()
    cards.write_to_png(str(ev / "news_cards.png"))
    logo = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 120)
    lc = cairo.Context(logo)
    lc.set_source_rgb(0.1, 0.3, 0.7)
    lc.rectangle(0, 0, 400, 120)
    lc.fill()
    logo.write_to_png(str(ev / "logo_acme.png"))
    d0 = dt.date(2019, 1, 3)
    points = [[str(d0 + dt.timedelta(weeks=k)), round(4 + 2 * math.sin(k / 50), 2)] for k in range(300)]
    manifest = {"news": {"image": "news_cards.png", "cards": [{"title": "Rates jump", "publisher": "AP", "url": "https://example.org/n"}]},
                "articles": [{"id": "1", "image": "article_1.png", "url": "https://example.gov/paper", "title": "Lock-in", "publisher": "FHFA", "words": words}],
                "charts": [{"id": "MORTGAGE30US", "title": "30-Year Mortgage Rate", "units": "%", "source": "Freddie Mac via FRED",
                            "url": "https://fred.stlouisfed.org/series/MORTGAGE30US", "points": points}],
                "logos": [{"name": "Acme Housing", "image": "logo_acme.png", "url": "https://example.org/logo", "license": "trademark"}]}
    (ev / "manifest.json").write_text(json.dumps(manifest))
    script = ('[news] Mortgage rates just hit seven percent.\n\n'
              '[chart MORTGAGE30US 2021] Buyers from 2021 locked in rates around three percent.\n\n'
              '[logo "Acme Housing"] The agency measured it.\n\n'
              '[article 1 "the probability of sale is decreased by 18.1%"] For every point, the chance of selling drops by 18.1%.\n')
    (root / "english.txt").write_text(script)
    return root


# -- cue tags -------------------------------------------------------------------------
def test_extract_cues_word_indices_and_args():
    text, cues = extract_cues('[curious] [news] Rates hit 7%. [article 2 “the rate rose”] It rose. [chart FEDFUNDS 2021-03] [logo "Fannie Mae"] Ok.')
    words = text.split()
    assert words == ["Rates", "hit", "7%.", "It", "rose.", "Ok."]
    kinds = [(c.kind, c.word) for c in cues]
    assert kinds == [("news", 0), ("article", 3), ("chart", 5), ("logo", 5)]
    assert cues[1].ref == "2" and cues[1].quote == "the rate rose"
    assert cues[2].ref == "FEDFUNDS" and cues[2].date == "2021-03"
    assert cues[3].ref == "Fannie Mae"


def test_cue_tags_never_reach_narration():
    raw = '[article 1 "decreased by 18.1%"] The chance drops. [icon house] Homes.'
    assert normalize_script(raw) == "The chance drops. Homes."
    text, cues = normalize_script_with_cues(raw)
    assert [c.kind for c in cues] == ["article", "icon"]
    assert text.split()[cues[1].word] == "Homes."


def test_verify_placeholders_are_found():
    assert has_unresolved("Rates are {VERIFY: current rate} today.") == ["{VERIFY: current rate}"]
    assert has_unresolved("Rates are 7% today.") == []


# -- evidence -------------------------------------------------------------------------
def test_find_quote_exact_fuzzy_and_missing():
    words = [[w, i * 10.0, 0.0, i * 10.0 + 8, 10.0] for i, w in enumerate("the probability of sale is decreased by 18.1%. This".split())]
    art = Article("1", None, words=[tuple(w) for w in words])
    assert art.find_quote("probability of sale is decreased by 18.1%") == [(10.0, 0.0, 78.0, 10.0)]
    assert art.find_quote("probability of a sale decreased by 18.1%")  # small wording differences still match
    assert art.find_quote("home prices fell sharply in Ohio") == []


def test_merge_lines_one_rect_per_line():
    rects = merge_lines([("a", 0, 0, 10, 10), ("b", 12, 0, 20, 10), ("c", 0, 14, 8, 24)])
    assert rects == [(0, 0, 20, 10), (0, 14, 8, 24)]


def test_parse_date_forms():
    assert parse_date("2021") == dt.date(2021, 7, 1)
    assert parse_date("2021-03") == dt.date(2021, 3, 15)
    assert parse_date("2021-03-04") == dt.date(2021, 3, 4)
    assert parse_date("soon") is None


def test_load_manifest(tmp_path):
    ev = Evidence.load(make_topic(tmp_path) / "evidence")
    assert set(ev.articles) == {"1"} and "MORTGAGE30US" in ev.charts and ev.news_image
    assert ev.logo("acme housing")["name"] == "Acme Housing"
    assert ev.logo("Acme Housng") is not None  # close spelling
    assert {s["kind"] for s in ev.sources()} == {"news", "article", "chart", "logo"}


# -- layout and rendering ---------------------------------------------------------------
def build_layout(topic, cfg):
    text, cues = normalize_script_with_cues((topic / "english.txt").read_text())
    an = analyze_script(text, "en")
    t, sents = 0.0, []
    for s in an:
        ws = []
        for w in s.text.split():
            ws.append({"word": w, "start": t, "end": t + 0.33})
            t += 0.36
        sents.append(TimedSentence(s.text, ws[0]["start"], ws[-1]["end"], ws))
        t += 0.3
    plan = plan_scenes([Segment(ts.text, ts.start, ts.end, i, ts.words) for i, ts in enumerate(sents)], "en", cfg, "english")
    r = ExplainerRenderer(cfg, cfg["video"]["width"], cfg["video"]["height"], "en")
    lay = ExplainerLayout(cfg, "en", Evidence.load(topic / "evidence"), r.scale)
    return r, lay.build(plan, time_cues(cues, sents, [s.paragraph for s in an], 0.0), 0.0, t + 0.5)


@pytest.fixture
def ex_cfg():
    return load_config(overrides={"animation": {"style": "explainer"}, "video": {"quality": "fast"}}, repo_root=ROOT)


def test_layout_stages_follow_cues(tmp_path, ex_cfg):
    _, lay = build_layout(make_topic(tmp_path), ex_cfg)
    assert [s.kind for s in lay.stages] == ["news", "chart", "logos", "article"]
    assert all(a.t_out <= b.t_in + 1e-6 for a, b in zip(lay.stages, lay.stages[1:]))  # one stage at a time
    assert lay.highlights == {"requested": 1, "matched": 1}
    assert not lay.warnings
    first = [w.text for w in lay.scenes[0].words]
    assert first == ["7%"]  # no subtitles: only the spoken amount, written as its figure
    assert all(is_doodle(sp.name) for sc in lay.scenes for sp in sc.sprites)


def test_highlight_and_number_colours_are_painted(tmp_path, ex_cfg):
    r, lay = build_layout(make_topic(tmp_path), ex_cfg)
    frame = cairo.ImageSurface(cairo.FORMAT_RGB24, r.width, r.height)
    scene = lay.scenes[-1]  # "...drops by 18.1%" over the highlighted article
    r.paint_frame(cairo.Context(frame), scene, lay.stages, scene.end - 0.05)
    buf = frame.get_data()
    stride = frame.get_stride()

    def count(pred):
        n = 0
        for y in range(0, r.height, 3):
            for x in range(0, r.width, 3):
                b, g, rr = buf[y * stride + 4 * x], buf[y * stride + 4 * x + 1], buf[y * stride + 4 * x + 2]
                n += pred(rr, g, b)
        return n

    assert count(lambda rr, g, b: rr > 200 and g > 180 and b < 120) > 50  # yellow highlighter over the quote
    assert count(lambda rr, g, b: rr > 170 and g < 80 and b < 80) > 20  # big red number on the board


def test_chart_marker_moves_with_date(tmp_path):
    ev = Evidence.load(make_topic(tmp_path) / "evidence")
    p = ChartPainter(ev.charts["MORTGAGE30US"], 0.0, [(1.0, "2019-06"), (3.0, "2022")])
    plot = p.plot_rect((0, 600, 1080, 1800))
    x1, _ = p.xy(plot, *p.markers[0][1:])
    x2, _ = p.xy(plot, *p.markers[1][1:])
    assert plot[0] < x1 < x2 < plot[2]
    assert not p.add_marker(4.0, "not a date")


def test_explainer_style_without_topic_still_renders_words(ex_cfg):
    scene = {"scene_id": "scene_001", "narration_text": "Prices rose by fifty percent.", "start_seconds": 0, "end_seconds": 2,
             "visual_actions": [], "quantities": [{"kind": "percent", "display": "50%", "span": [15, 28], "source": "fifty percent", "value": 50}]}
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    tokens, flags, _ = lay.board_tokens(scene, [(0, 0.4)] * 5)
    assert tokens == ["Prices", "rose", "by", "50%."] and flags == [False, False, False, True]


# -- preparation helpers ------------------------------------------------------------------
NEWS_XML = b"""<rss><channel>
<item><title>Rates hit 7% - AP News</title><link>https://news.google.com/rss/articles/a</link>
<pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><source url="https://apnews.com">AP News</source></item>
<item><title>Paper on lock-in - FHFA</title><link>https://news.google.com/rss/articles/b</link>
<pubDate>Sun, 11 Oct 2026 10:00:00 GMT</pubDate><source url="https://www.fhfa.gov">FHFA</source></item>
<item><title>Paywalled view - WSJ</title><link>https://news.google.com/rss/articles/c</link>
<source url="https://www.wsj.com">WSJ</source></item>
</channel></rss>"""


def test_parse_news_strips_publisher_and_ranks():
    items = parse_news(NEWS_XML)
    assert [n.title for n in items] == ["Rates hit 7%", "Paper on lock-in", "Paywalled view"]
    assert [n.rank() for n in items] == [1, 0, 3]
    assert items[1].domain == "fhfa.gov"


def test_fred_csv_and_series_choice():
    pts = fred.parse_csv(b"observation_date,MORTGAGE30US\n2021-01-07,2.65\n2021-01-14,.\n2021-01-21,2.77\n")
    assert pts == [("2021-01-07", 2.65), ("2021-01-21", 2.77)]
    assert fred.pick_series("Mortgage rates and the housing market")[0] == "MORTGAGE30US"
    assert fred.pick_series("a story about cats") == []


def test_draft_validation(tmp_path):
    ev = Evidence.load(make_topic(tmp_path) / "evidence")
    known = draft.evidence_numbers([PAGE_TEXT]) | {7.0}
    script = ('[news] Rates hit 7%. [article 1 "the probability of sale is decreased by 18.1%"] Sales fell 18.1%. '
              '[article 9 "x"] Prices rose 42%. [article 1 "words that are not there at all"] Ok. [chart NOPE 2020] Done.')
    out, notes = draft.validate(script, ev.articles, set(ev.charts), {"acme housing"}, known)
    assert "[article 9" not in out and "[chart NOPE" not in out
    assert '[article 1 "the probability of sale is decreased by 18.1%"]' in out
    assert "[article 1] Ok." in out  # quote not on the page: keep the screenshot, drop the highlight
    assert "{VERIFY: 42%}" in out and "{VERIFY: 18.1%}" not in out and "{VERIFY: 7%}" not in out
    assert len(notes) == 4


def test_extractive_draft_has_tags_and_placeholder():
    text = draft.extractive_draft([{"title": "Rates jump"}], [{"id": "1", "paragraphs": [PAGE_TEXT]}], [])
    _, cues = extract_cues(text)
    assert {c.kind for c in cues} == {"news", "article"}
    assert has_unresolved(text)


# -- end to end -----------------------------------------------------------------------------
def test_topic_folder_renders_explainer_video(tmp_path):
    repo = tmp_path
    topic = make_topic(repo / "input" / "topics" / "demo")
    (topic / "audio").mkdir()
    tone(topic / "audio" / "english.wav", seconds=14.0)
    # an unrelated default voice-over must not be picked up for the topic
    (repo / "input" / "audio").mkdir(parents=True)
    tone(repo / "input" / "audio" / "roman_urdu.wav", seconds=2.0)
    code = cli.main(["run", "--repo-root", str(repo), "--topic-dir", "input/topics/demo", "--quality", "fast", "--output", str(repo / "out")])
    assert code == 0
    out = repo / "out" / "english"
    assert (out / "video.mp4").exists() and (out / "thumbnail.png").exists()
    sources = (out / "sources.txt").read_text()
    assert "https://example.gov/paper" in sources and "fred.stlouisfed.org" in sources
    q = json.loads((out / "quality_report.json").read_text())
    assert q["passed"]
    assert {c["name"]: c["passed"] for c in q["checks"]}["evidence_has_source"]
    assert not (repo / "out" / "roman_urdu" / "video.mp4").exists()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out / "video.mp4")],
                               capture_output=True, text=True).stdout)
    assert dur > 13


def test_verify_placeholder_blocks_rendering(tmp_path):
    repo = tmp_path
    topic = make_topic(repo / "input" / "topics" / "demo")
    (topic / "english.txt").write_text("[news] Rates are {VERIFY: today's rate} now.\n")
    (topic / "audio").mkdir()
    tone(topic / "audio" / "english.wav", seconds=3.0)
    code = cli.main(["run", "--repo-root", str(repo), "--topic-dir", "input/topics/demo", "--quality", "fast", "--output", str(repo / "out")])
    assert code != 0
    report = json.loads((repo / "out" / "reports" / "generation_report.json").read_text())
    assert "VERIFY" in json.dumps(report)


def _browser():
    try:
        from engine.explainer.capture import Browser

        return Browser()
    except Exception as exc:  # playwright or a browser not installed
        pytest.skip(f"headless browser unavailable: {exc}")


def test_capture_article_word_boxes(tmp_path):
    body = "".join(f"<p>{PAGE_TEXT} Paragraph {i} adds more context about housing and rates for readers.</p>" for i in range(6))
    page = tmp_path / "a.html"
    page.write_text(f"<html><head><title>Lock-in</title></head><body><article><h1>The Lock-In Effect</h1>{body}</article></body></html>")
    b = _browser()
    try:
        cap = b.article(page.as_uri(), tmp_path / "a.png")
        cards = b.news_cards([{"title": "Rates jump", "publisher": "AP News", "age": "2 hours ago"}], tmp_path / "cards.png")
    finally:
        b.close()
    assert cap is not None and (tmp_path / "a.png").exists()
    art = Article("1", tmp_path / "a.png", words=[tuple(w) for w in cap.words])
    rects = art.find_quote("the probability of sale is decreased by 18.1%")
    assert rects
    img = cairo.ImageSurface.create_from_png(str(tmp_path / "a.png"))
    assert all(0 <= r[0] < r[2] <= img.get_width() and 0 <= r[1] < r[3] <= img.get_height() for r in rects)
    assert cards[0]["box"][2] > cards[0]["box"][0]


def test_relevance_needs_most_topic_words():
    from engine.explainer.prepare import relevant
    from engine.explainer.sources import NewsItem

    def item(title):
        return NewsItem(title, "https://news.google.com/x", "Pub", "https://pub.example", None)

    topic = "Digital Arrest Scams 2.0: New Tactics Fraudsters Are Using in 2026"
    items = [item("Rs 51 lakh gone in digital arrest scam: how fraudsters targeted a retiree"),
             item("Miami man arrested on fraud charges"), item("DoJ charges 28 over alleged voter fraud")]
    assert [n.title for n in relevant(topic, items)] == [items[0].title]
    ai = [item("AI stock bubble fears grow"), item("Will the AI bubble pop?"), item("Stocks are in a late-stage bubble"), item("AI chips sell out")]
    assert [n.title for n in relevant("Is the AI Stock Bubble About to Pop?", ai)] == [n.title for n in ai[:3]]


def test_large_amounts_keep_their_scale_word():
    from engine.numbers import extract_quantities

    def shown(text, lang="en"):
        return [q.display for q in extract_quantities(text, lang)]

    assert shown("spending will hit one trillion dollars") == ["$1 trillion"]
    assert shown("valuations of $2 trillion") == ["$2 trillion"]
    assert shown("1.33 million sales") == ["1.33 million"]
    assert shown("log 4,057.7 crore rupay gawa chuke", "roman_urdu") == ["Rs 4,057.7 crore"]
    assert shown("51 lakh rupay bhej diye", "roman_urdu") == ["Rs 51 lakh"]
    assert shown("fifty thousand dollars") == ["$50,000"]
    q = extract_quantities("Rs 10.73 crore", "en")[0]
    assert q.value == 107_300_000 and q.kind == "currency"


def test_wide_amount_is_shrunk_to_fit_the_board(ex_cfg):
    from engine.illustration_composer import DESIGN_W, SAFE_MARGIN

    text = "India mein log 4,057.7 crore rupay gawa chuke hain."
    from engine.numbers import extract_quantities

    qs = [q.to_dict() for q in extract_quantities(text, "roman_urdu")]
    scene = {"scene_id": "scene_001", "narration_text": text, "start_seconds": 0, "end_seconds": 3, "visual_actions": [], "quantities": qs}
    words, _ = ExplainerLayout(ex_cfg, "roman_urdu", None, 1.0).layout_words(scene, [(0.0, 0.3)] * len(text.split()))
    big = next(w for w in words if "crore" in w.text)
    assert big.text == "Rs 4,057.7 crore"
    assert all(SAFE_MARGIN <= w.bbox[0] and w.bbox[2] <= DESIGN_W - SAFE_MARGIN for w in words)


def test_spoken_currency_word_is_not_repeated(ex_cfg):
    from engine.numbers import extract_quantities

    for text, lang, want in [("Spending will hit one trillion dollars.", "en", "$1 trillion."),
                             ("Log 4,057.7 crore rupay gawa chuke.", "roman_urdu", "Rs 4,057.7 crore")]:
        scene = {"narration_text": text, "quantities": [q.to_dict() for q in extract_quantities(text, lang)]}
        tokens, flags, _ = ExplainerLayout.board_tokens(scene, [(0.0, 0.3)] * len(text.split()))
        assert want in tokens and not any(t.lower().startswith(("dollar", "rupay")) for t in tokens), tokens


def test_board_shows_only_key_amounts(ex_cfg):
    from engine.numbers import extract_quantities

    text = "Darr ke maare usne 51 lakh rupay fraudsters ke account mein bhej diye."
    scene = {"scene_id": "scene_001", "narration_text": text, "start_seconds": 0, "end_seconds": 3, "visual_actions": [],
             "quantities": [q.to_dict() for q in extract_quantities(text, "roman_urdu")]}
    words, _ = ExplainerLayout(ex_cfg, "roman_urdu", None, 1.0).layout_words(scene, [(0.0, 0.3)] * len(text.split()))
    assert [w.text for w in words] == ["Rs 51 lakh"]  # no subtitles, only the amount
    scene["quantities"], scene["narration_text"] = [], "Call kaatein aur verify karein."
    assert ExplainerLayout(ex_cfg, "roman_urdu", None, 1.0).layout_words(scene, [(0.0, 0.3)] * 4)[0] == []


def test_voice_text_for_urdu_and_roman_urdu():
    from engine.numbers import extract_quantities
    from engine.transliteration import roman_to_urdu, spell_numbers, urdu_number_words

    assert urdu_number_words(4057) == "چار ہزار ستاون" and urdu_number_words(2026) == "دو ہزار چھبیس"
    assert urdu_number_words(1_500_000) == "پندرہ لاکھ"
    assert spell_numbers("لوگ 4,057.7 کروڑ روپے") == "لوگ چار ہزار ستاون اعشاریہ سات کروڑ روپے"
    assert roman_to_urdu("Police video call par arrest nahi karti.") == "پولیس ویڈیو کال پر اریسٹ نہیں کرتی۔"
    assert roman_to_urdu("Pune mein", {"pune": "پُونے"}) == "پُونے میں"
    assert [q.display for q in extract_quantities("اس نے 51 لاکھ روپے بھیج دیے", "ur")] == ["Rs 51 lakh"]


def test_one_click_drops_unverified_sentences():
    script = ('[news] Rates hit 7%. Banks charge {VERIFY: 42%} more.\n\n'
              '[article 1 "x"] Only {VERIFY: 9 million} people moved.\n\nPrices stay high. ہے {VERIFY: ۵} ۔ Done.\n')
    out, removed = draft.drop_unverified(script)
    assert removed == 3 and "VERIFY" not in out
    assert out == "[news] Rates hit 7%.\n\nPrices stay high. Done.\n"


def test_amounts_people_say():
    from engine.numbers import extract_quantities

    def shown(text, lang="en"):
        return [q.display for q in extract_quantities(text, lang)]

    assert shown("about one and a half million home sellers") == ["1.5 million"]
    assert shown("Almost four out of ten homeowners") == ["4 out of 10"]
    assert shown("تقریباً پچانوے روپے کا", "ur") == ["Rs 95"]
    assert shown("ایک ڈالر تقریباً دو سو ستتر روپے کا ہے", "ur")[-1] == "Rs 277"


def test_auto_tag_keeps_words_and_matches_numbers(tmp_path):
    ev = Evidence.load(make_topic(tmp_path) / "evidence")
    script = "[curious] Rates hit seven percent.\n\n[measured] For every point, sales drop by 18.1%. Prices stay high.\n"
    out, notes = draft.auto_tag(script, "en", ev.articles, {"1": [PAGE_TEXT]}, [], news=True)
    assert out.startswith("[curious] [news] Rates hit seven percent.")
    assert '[article 1 "' in out and "18.1%" in out.split("[article 1")[1]
    assert extract_cues(out)[0].split() == extract_cues(script)[0].split()  # narration unchanged
    assert draft.auto_tag(out, "en", ev.articles, {}, [], True)[0] == out  # already tagged: left alone


def test_initials_do_not_end_a_sentence():
    sents = [s.text for s in analyze_script("There were 1.5 million sellers in the U.S., but fewer buyers. Next one.", "en")]
    assert sents == ["There were 1.5 million sellers in the U.S., but fewer buyers.", "Next one."]


def test_scene_without_doodles_still_gets_one(ex_cfg):
    scene = {"scene_id": "scene_001", "narration_text": "According to Redfin, the gap is the biggest yet.", "start_seconds": 0, "end_seconds": 3,
             "visual_actions": [{"id": "a1", "action": "draw", "object": "people_group", "position": "center", "draw_order": 1}], "quantities": []}
    sprites = ExplainerLayout(ex_cfg, "en", None, 1.0).scene_icons(scene, [(0.0, 0.3)] * 8, 0.0, 3.0, [], 300.0, [])
    assert [sp.name for sp in sprites] == ["person"]
    scene["visual_actions"] = []
    assert ExplainerLayout(ex_cfg, "en", None, 1.0).scene_icons(scene, [(0.0, 0.3)] * 8, 0.0, 3.0, [], 300.0, [])
