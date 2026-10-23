"""Research package: URLs, polite fetching, ledger, claims, finance, manifests, research -> render end to end (offline)."""

import csv
import io
import json
import shutil

import pytest

from engine import cli
from engine.config import load_config
from engine.explainer.evidence import Article
from engine.package import claims as C
from engine.package import consistency, finance, manifest
from engine.package.extract import parse_html
from engine.package.http import FetchError, Fetcher
from engine.package.job import JobError, create_job_dir, safe_join, verify_package, write_package_manifest
from engine.package.ledger import CSV_FIELDS, Ledger, make_source
from engine.package.research import Research, ResearchInputs, mark_syndicated, run_research, urdu_form
from engine.package.source_card import make_card
from engine.package.urls import UrlError, canonical_url, source_id, validate_url
from engine.results import Status

from .conftest import ROOT, ToneTTS, tone
from .package_fixtures import ARTICLE_1, ENGLISH, ROMAN_URDU, URDU, FakeWeb, fake_fetcher, research_cfg


# -- URLs, fetching, ledger ---------------------------------------------------------------
def test_canonical_url_drops_tracking_and_fragment():
    a = canonical_url("HTTPS://WWW.Example.com:443/a/b/?utm_source=x&id=2&fbclid=y#top")
    assert a == "https://www.example.com/a/b?id=2"
    assert source_id("https://www.example.com/a/b?id=2&utm_medium=z") == source_id(a)


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "http://localhost/x", "http://127.0.0.1/", "http://10.0.0.5/a",
                                 "https://user:pw@example.com/", "javascript:alert(1)", "http://example.com/a b"])
def test_validate_url_rejects_unsafe(bad):
    with pytest.raises(UrlError):
        validate_url(bad)


def test_fetcher_respects_robots_and_identifies_itself(tmp_path):
    web = FakeWeb()
    f = fake_fetcher(tmp_path, web)
    with pytest.raises(FetchError, match="robots"):
        f.get("https://blocked.example.net/rates")
    assert "https://blocked.example.net/rates" not in web.calls  # never requested


def test_fetcher_backs_off_on_429_and_never_retries_403(tmp_path):
    seen, sleeps = [], []

    def opener(url, headers, timeout):
        seen.append(url)
        if url.endswith("robots.txt"):
            return 404, url, {}, b""
        if "busy" in url:
            return (429, url, {"Retry-After": "7"}, b"") if seen.count(url) < 2 else (200, url, {"Content-Type": "text/plain"}, b"ok")
        return 403, url, {}, b""

    f = Fetcher("ua/1", retries=2, backoff=1, min_interval=0, cache_dir=tmp_path, ttl_hours=0, opener=opener, sleep=sleeps.append)
    assert f.get("https://a.example.com/busy").body == b"ok"
    assert 7 in sleeps  # Retry-After honoured
    with pytest.raises(FetchError) as e:
        f.get("https://a.example.com/forbidden")
    assert e.value.status == 403 and seen.count("https://a.example.com/forbidden") == 1


def test_fetcher_cache_has_provenance_and_ttl(tmp_path):
    web = FakeWeb()
    f = Fetcher("ua/1", min_interval=0, cache_dir=tmp_path, ttl_hours=1, opener=web, sleep=lambda s: None)
    first = f.get("https://news.example.com/rates-climb")
    n = len(web.calls)
    again = f.get("https://news.example.com/rates-climb")
    assert again.from_cache and not first.from_cache and len(web.calls) == n
    meta = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert meta["url"] == "https://news.example.com/rates-climb" and meta["fetched_at"] and meta["user_agent"] == "ua/1"


def test_ledger_merges_duplicates_and_writes_all_fields(tmp_path):
    led = Ledger()
    led.add(make_source("https://www.bls.gov/x?utm_source=a", "CPI", "BLS", "2026-10-01T00:00:00+00:00", source_type="official"))
    led.add(make_source("https://www.bls.gov/x", "", "", "2026-10-02T00:00:00+00:00", published_at="2026-09-30", claims_supported=["EN-001"]))
    led.add(make_source("https://www.ca.gov/y", "State page", "CA", "2026-10-01T00:00:00+00:00"))
    assert len(led.sources) == 2
    bls = led.get(source_id("https://www.bls.gov/x"))
    assert bls.published_at == "2026-09-30" and bls.claims_supported == ["EN-001"] and bls.commercial_status == "approved"
    assert led.get(source_id("https://www.ca.gov/y")).commercial_status == "unknown"  # state .gov is not federal public domain
    led.write(tmp_path)
    rows = list(csv.DictReader(io.StringIO((tmp_path / "sources.csv").read_text(encoding="utf-8"))))
    assert list(rows[0]) == CSV_FIELDS and len(CSV_FIELDS) == 18
    assert "https://www.bls.gov/x" in (tmp_path / "sources.txt").read_text(encoding="utf-8")
    assert Ledger.load(tmp_path).get(bls.source_id).title == "CPI"


def test_extract_reads_metadata_and_skips_navigation():
    page = parse_html(ARTICLE_1)
    assert page.title == "Mortgage rates climb to 6.8%" or page.title.startswith("Mortgage rates climb")
    assert page.publisher == "Example News" and page.author == "A. Reporter" and page.published_at.startswith("2026-10-15")
    assert page.canonical.startswith("https://news.example.com/rates-climb")
    assert not any("newsletter" in p or "Copyright" in p for p in page.paragraphs)
    assert parse_html("<html><head><title>Just a moment...</title></head><body><p>Verify you are human.</p></body></html>").blocked


# -- claims ------------------------------------------------------------------------------
DOCS = [C.Doc("S-a", "The average 30-year mortgage rate rose to 6.8% this week, according to Freddie Mac data. "
                     "Home sales fell 4.5% in September from the month before.", "news", "news.example.com", "2026-10-15"),
        C.Doc("S-b", "This paper finds that the probability of sale is decreased by 18.1% for every percentage point.", "official",
              "agency.example.gov", "2026-10-12")]


@pytest.mark.parametrize("text,kind", [("In May, prices rose 5%.", "factual"), ("Prices may fall next year.", "prediction"),
                                       ("Will rates fall?", "question"), ("I think now is the best time.", "opinion"),
                                       ("And that is the story.", "narrative"), ("Redfin says sellers outnumber buyers.", "factual")])
def test_claim_kinds(text, kind):
    assert C.classify(text) == kind


def test_claim_statuses_are_conservative():
    cl = C.extract_claims("Mortgage rates rose to 6.8% this week, Freddie Mac data show.\n\nThe chance of a sale drops by 18.1 percent.\n\n"
                          "Home sales fell 9.5% in September from the month before.\n\nPrices in Atlantis rose 42%.\n\nRates may fall soon.", "en", "EN")
    C.match_claims(cl, DOCS)
    st = {c.claim_id: c.status for c in cl}
    assert st["EN-001"] == "supported" and cl[0].source_ids == ["S-a"]
    assert st["EN-002"] in ("supported", "partially_supported")
    assert st["EN-003"] == "contradicted"  # same wording, a different figure
    assert st["EN-004"] == "not_found" and st["EN-005"] == "opinion"
    assert [c.paragraph for c in cl] == [0, 1, 2, 3, 4]


def test_small_numbers_alone_do_not_support_a_claim():
    cl = C.extract_claims("There are 3 reasons dogs bark.", "en", "EN")
    C.match_claims(cl, [C.Doc("S-x", "Profits rose for 3 years in a row at the bank.", "news", "x.com")])
    assert cl[0].status == "not_found"


def test_outdated_when_claim_says_now_but_source_is_old():
    cl = C.extract_claims("Mortgage rates are now 6.8%, Freddie Mac data show.", "en", "EN")
    old = [C.Doc("S-a", DOCS[0].text, "news", "news.example.com", "2019-01-01")]
    C.match_claims(cl, old, outdated_days=400)
    assert cl[0].status == "outdated"


def test_urdu_claim_numbers_match_english_source_only_partially():
    cl = C.extract_claims("مارگیج ریٹ اس ہفتے 6.8 فیصد تک پہنچ گیا۔", "ur", "UR")
    C.match_claims(cl, DOCS)
    assert cl[0].status == "partially_supported" and "across languages" in cl[0].notes[0]


def test_syndicated_copy_is_not_an_independent_source():
    text = " ".join(f"Sentence number {i} about mortgage rates rising to 6.8% this week across the country." for i in range(12))
    docs = [C.Doc("S-1", text, "news", "apnews.com", "2026-10-10"), C.Doc("S-2", text, "news", "localpaper.com", "2026-10-11")]
    led = Ledger()
    mark_syndicated(docs, led)
    assert docs[1].duplicate_of == "S-1"
    cl = C.extract_claims("Mortgage rates are rising to 6.8% this week across the country.", "en", "EN")
    C.match_claims(cl, docs)
    assert cl[0].status == "supported" and cl[0].independent_sources == 1


# -- finance -----------------------------------------------------------------------------
def test_financial_amounts_are_normalised():
    a = {x.raw: (x.value, x.unit) for x in finance.parse_amounts("$35.1 billion, $1.2bn, 94%, 2 percentage points, 25 basis points, 45 cents a share")}
    assert a["$35.1 billion"] == (35.1e9, "USD") and a["$1.2bn"] == (1.2e9, "USD") and a["94%"] == (94.0, "percent")
    assert a["2 percentage points"] == (2.0, "pp") and a["25 basis points"] == (0.25, "pp")
    assert a["45 cents"] == (0.45, "USD/shares")


@pytest.mark.parametrize("text,fy,fp,fiscal", [("in the third quarter of fiscal 2025", 2025, "Q3", True), ("Q2 2024 results", 2024, "Q2", False),
                                               ("for fiscal 2024", 2024, "FY", True), ("FY24", 2024, "FY", True), ("in 2023", 2023, "FY", False)])
def test_financial_periods(text, fy, fp, fiscal):
    p = finance.parse_period(text)
    assert (p.fy, p.fp, p.fiscal) == (fy, fp, fiscal)


def test_one_sentence_two_atomic_financial_claims():
    fcs = finance.parse_claims("EN-001", "Nvidia reported revenue of $35.1 billion in the third quarter of fiscal 2025, up 94% year over year.")
    assert [(f.claim_id, f.metric, f.amount["unit"], f.comparison) for f in fcs] == [("EN-001a", "revenue", "USD", None),
                                                                                       ("EN-001b", "revenue", "percent", "yoy")]
    assert fcs[0].basis == "unknown"  # "reported" says nothing about GAAP


def companyfacts():
    def row(start, end, val, fy, fp, form, accn, filed):
        return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "form": form, "accn": accn, "filed": filed}

    rev = [row("2023-01-01", "2023-12-31", 80e9, 2023, "FY", "10-K", "a23", "2024-02-01"),
           row("2024-01-01", "2024-03-31", 25e9, 2024, "Q1", "10-Q", "q1", "2024-05-01"),
           row("2024-04-01", "2024-06-30", 26e9, 2024, "Q2", "10-Q", "q2", "2024-08-01"),
           row("2024-01-01", "2024-06-30", 51e9, 2024, "Q2", "10-Q", "q2", "2024-08-01"),  # year to date: ignored
           row("2024-07-01", "2024-09-30", 27e9, 2024, "Q3", "10-Q", "q3", "2024-11-01"),
           row("2024-01-01", "2024-12-31", 100e9, 2024, "FY", "10-K", "a24", "2025-02-01"),
           row("2023-01-01", "2023-12-31", 80e9, 2024, "FY", "10-K", "a24", "2025-02-01"),  # prior year shown again
           row("2024-01-01", "2024-12-31", 98e9, 2024, "FY", "10-K/A", "a24b", "2025-04-01")]  # restated
    eps = [row("2024-01-01", "2024-12-31", 1.24, 2024, "FY", "10-K", "a24", "2025-02-01")]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}}}


def test_xbrl_periods_restatement_and_q4_derivation():
    s = finance.metric_series(companyfacts(), "revenue")
    assert s[(2024, "FY")].value == 98e9 and s[(2024, "FY")].restated_from == [{"filed": "2025-02-01", "value": 100e9}]
    assert (2024, "Q2") in s and s[(2024, "Q2")].value == 26e9  # quarter, not the half-year value
    q4 = s[(2024, "Q4")]
    assert q4.value == 98e9 - 78e9 and "Q1 + Q2 + Q3" in q4.derived


def verify(text):
    fc = finance.parse_claims("EN-001", text)[0]
    finance.verify_with_facts(fc, companyfacts(), "S-sec", "https://data.sec.gov/x", "Acme")
    finance.summarise(fc, None, 3)
    return fc


def test_financial_matches_and_conflicts():
    assert verify("Diluted EPS was $1.24 for fiscal 2024.").status == "exact_match"
    assert verify("Revenue was $98 billion in fiscal 2024.").status == "normalized_match"
    fc = verify("Revenue was $100 billion in fiscal 2024.")
    assert fc.status == "conflict" and any("originally reported" in f for f in fc.flags) and fc.proposed_correction
    g = verify("Revenue rose 22.5% year over year in fiscal 2024.")
    assert g.status == "exact_match" and "100 * (" in g.matches[0]["calculation"]  # (98-80)/80 = 22.5% exactly
    assert verify("Revenue rose 23% year over year in fiscal 2024.").status == "normalized_match"  # rounded figure


def test_non_gaap_and_missing_values_are_flagged():
    fc = verify("Adjusted revenue was $105 billion in fiscal 2024.")
    assert fc.status == "partial_match" and any(f.startswith("basis_differs") for f in fc.flags)
    miss = verify("Revenue was $30 billion in Q1 2022.")
    assert miss.status == "not_found" and "no Q1 FY2022" in miss.matches[0]["note"]
    assert any("distinct source" in x for x in miss.limitations)
    cap = finance.parse_claims("EN-9", "Its market cap is $3 trillion.")[0]
    finance.verify_with_facts(cap, companyfacts(), "S", "u", "Acme")
    assert any("market data" in x for x in cap.limitations)


def test_percent_vs_percentage_points_conflict():
    a = finance.parse_amounts("2 percentage points")[0]
    assert finance.compare_value(a, 2.0, "percent") == "conflict"


def test_issuer_release_table_in_millions():
    fc = finance.parse_claims("EN-1", "Revenue was $35.1 billion in Q3 fiscal 2025.")[0]
    finance.verify_in_text(fc, "Revenue | $ 35,082 | $ 18,120\nNet income | 19,309", "S-rel", "u", "Issuer release 8-K", "issuer_release")
    assert fc.matches and fc.matches[0]["table_scale"] == 1e6 and fc.matches[0]["status"] == "normalized_match"


def test_financial_reports_written(tmp_path):
    fc = verify("Revenue was $98 billion in fiscal 2024.")
    finance.write_reports(tmp_path, [fc])
    assert json.loads((tmp_path / "financial_claims.json").read_text(encoding="utf-8"))[0]["status"] == "normalized_match"
    assert "normalized_match" in (tmp_path / "financial_claims.csv").read_text(encoding="utf-8")
    assert "Financial verification report" in (tmp_path / "financial_verification_report.md").read_text(encoding="utf-8")


# -- cards, jobs, manifests, consistency ------------------------------------------------
def test_source_card_words_allow_highlighting(tmp_path):
    info = make_card(tmp_path / "c.png", "Housing Agency", "Lock-in effect report", "https://agency.example.gov/report", "2026-10-12",
                     "This paper finds that the probability of sale is decreased by 18.1% for every point.")
    art = Article("1", tmp_path / "c.png", words=[tuple(w) for w in info["words"]], capture="source_card")
    rects = art.find_quote("the probability of sale is decreased by 18.1%")
    assert rects and all(0 <= r[0] < r[2] <= info["width"] and 0 <= r[1] < r[3] <= info["height"] for r in rects)


def test_job_folders_are_never_reused_and_paths_are_safe(tmp_path):
    job = create_job_dir(tmp_path, "20261022-000000-x-ab12")
    (job / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(JobError):
        create_job_dir(tmp_path, "20261022-000000-x-ab12")
    for bad in ("../evil", "/etc/passwd", "C:/Windows", "a/../../b"):
        with pytest.raises(JobError):
            safe_join(job, bad)
    with pytest.raises(JobError):
        create_job_dir(tmp_path, "../oops")


def test_package_hashes_detect_tampering_and_voice_samples(tmp_path):
    job = create_job_dir(tmp_path, "20261022-000000-pkg-ab12")
    (job / "research").mkdir()
    (job / "research" / "claims.json").write_text("[]", encoding="utf-8")
    write_package_manifest(job, {"topic": "t"})
    assert verify_package(job)[1] == []
    (job / "research" / "claims.json").write_text("[1]", encoding="utf-8")
    assert any("hash mismatch" in p for p in verify_package(job)[1])
    (job / "voice").mkdir()
    (job / "voice" / "reference.wav").write_bytes(b"RIFF")
    with pytest.raises(JobError, match="voice samples"):
        write_package_manifest(job, {})


def minimal_manifest():
    ev = {"event_id": "scene_001.e001", "start_sec": 0.5, "end_sec": 1.5, "trigger_text": "Rates", "action": "show_evidence",
          "target_asset": "article:1", "parameters": {}, "source_id": "S-a"}
    scene = {"scene_id": "scene_001", "start_sec": 0.0, "end_sec": 2.0, "narration_text": "Rates rose.", "word_or_phrase_anchors": [],
             "claim_ids": [], "visual_type": "article_evidence_card", "assets": [], "source_ids": ["S-a"], "animation_events": [ev],
             "transition_in": "card_reveal", "transition_out": "end", "layout": {}, "timing_confidence": "low", "render_notes": []}
    return {"schema_version": "1.0", "job_id": "local-20261022", "language": "en", "topic": "t", "script_sha256": "", "audio_sha256": "",
            "video_settings": {"width": 720, "height": 1280, "fps": 24, "duration_seconds": 2.0},
            "sources": [{"source_id": "S-a", "label": "A"}], "claims": [], "scenes": [scene], "qa_expectations": {}}


def test_manifest_validation_rules(tmp_path):
    assert manifest.validate(minimal_manifest()) == []
    m = minimal_manifest()
    m["scenes"][0]["start_sec"] = -1
    assert manifest.validate(m)  # schema: negative time
    m = minimal_manifest()
    m["scenes"][0]["animation_events"][0]["end_sec"] = 3.0
    assert any("outside its scene" in p for p in manifest.validate(m))
    m = minimal_manifest()
    m["scenes"][0]["source_ids"], m["scenes"][0]["animation_events"][0]["source_id"] = [], None
    assert any("without a source id" in p for p in manifest.validate(m))
    m = minimal_manifest()
    m["language"] = "xx"
    assert manifest.validate(m)
    m = minimal_manifest()
    m["scenes"][0]["assets"] = [{"asset_id": "article:1", "kind": "screenshot", "path": "research/screenshots/missing.png"}]
    assert any("missing asset" in p for p in manifest.validate(m, tmp_path))


def test_consistency_report_finds_number_and_evidence_differences():
    en = "[news] Rates hit 6.8% this week.\n\nPrices rose 4.5% in 2025.\n"
    ur = "[news] ریٹ اس ہفتے 6.8 فیصد ہو گیا۔\n\nقیمتیں 2024 میں 5.4% بڑھیں۔\n"
    rep = consistency.report({"english": en, "urdu": ur}, {"english": "en", "urdu": "ur"}, {})
    assert rep["alignment"] == "paragraph" and not rep["consistent"]
    assert rep["number_mismatches"][0]["paragraph"] == 2 and rep["date_mismatches"][0]["only_english"] == [2025.0]
    rep2 = consistency.report({"english": en, "urdu": ur.replace("\n\n", " ")}, {"english": "en", "urdu": "ur"}, {})
    assert rep2["alignment"] == "global"


def test_urdu_form_detection_keeps_supplied_form():
    assert urdu_form(URDU, "auto")[0] == "urdu"
    assert urdu_form(ROMAN_URDU, "auto")[0] == "roman_urdu"
    assert urdu_form(ROMAN_URDU, "urdu")[0] == "urdu"  # explicit mode wins


# -- research -> render end to end (offline) --------------------------------------------
@pytest.fixture(scope="module")
def package(tmp_path_factory):
    root = tmp_path_factory.mktemp("pkg")
    cfg = research_cfg(load_config(repo_root=ROOT))
    web = FakeWeb()
    inp = ResearchInputs("mortgage rates housing lock-in", "both", {"english": ENGLISH, "urdu": URDU},
                         ["https://agency.example.gov/report", "https://paywall.example.org/story", "file:///etc/passwd"])
    job = run_research(inp, cfg, root / "out", log=lambda m: None, fetcher=fake_fetcher(root, web), sec=None,
                       browser_factory=lambda: None, embedder=None)
    return job, web


def test_research_package_contents(package):
    job, web = package
    res = job / "research"
    for f in ("research.json", "claims.json", "sources.json", "sources.csv", "sources.txt", "source_report.md", "topic/english.txt",
              "topic/urdu.txt", "topic/evidence/manifest.json", "scripts/english.original.txt", "charts/MORTGAGE30US.csv"):
        assert (res / f).exists(), f
    assert (job / "storyboard" / "consistency_report.json").exists() and (job / "package.json").exists()
    assert verify_package(job)[1] == []
    info = json.loads((res / "research.json").read_text(encoding="utf-8"))
    assert info["providers"]["bing_news_rss"]["status"] == "disabled" and info["providers"]["google_news_rss"]["status"] == "disabled"
    assert any("file:///etc/passwd" in w for w in info["warnings"])
    assert "https://blocked.example.net/rates" not in web.calls  # robots.txt respected
    assert (res / "scripts" / "urdu.original.txt").read_text(encoding="utf-8") == URDU  # kept byte for byte


def test_research_claims_flag_unsupported_and_keep_words(package):
    job, _ = package
    claims = {c["claim_id"]: c for c in json.loads((job / "research" / "claims.json").read_text(encoding="utf-8"))}
    assert claims["EN-001"]["status"] == "supported"
    assert claims["EN-004"]["status"] == "not_found"  # "Prices in Atlantis rose 42%"
    assert claims["EN-005"]["kind"] == "question"
    from engine.explainer.cues import extract_cues

    tagged = (job / "research" / "topic" / "english.txt").read_text(encoding="utf-8")
    assert extract_cues(tagged)[0].split() == ENGLISH.split()
    ledger = Ledger.load(job / "research")
    assert any("EN-001" in s.claims_supported for s in ledger.sources.values())


def test_screenshot_failure_becomes_labelled_source_card(package):
    job, _ = package
    man = json.loads((job / "research" / "topic" / "evidence" / "manifest.json").read_text(encoding="utf-8"))
    assert man["articles"] and all(a["capture"] == "source_card" and a["source_id"] for a in man["articles"])
    assert not any("Just a moment" in a["title"] for a in man["articles"])  # a bot check's title is never a headline


@pytest.fixture(scope="module")
def rendered(package, tmp_path_factory):
    from engine.package.render import RenderInputs, render_package

    pkg, _ = package
    root = tmp_path_factory.mktemp("render")
    tone(root / "en.wav", 16.0)
    ri = RenderInputs(pkg, "both", {"english": root / "en.wav"}, voice_modes={"urdu": "system_tts"}, quality="draft")
    job, results = render_package(ri, root / "out", log=lambda m: None, tts_chain=[], transcriber=None, aligner=None)
    return job, {r.key: r for r in results}


def test_render_outputs_layout_and_one_language_failure(rendered):
    job, results = rendered
    assert results["english"].status.ok
    assert results["urdu"].status == Status.PERMANENT_FAILURE  # no voice-over and no usable TTS: fails alone
    for f in ("video/explainer_en.mp4", "video/thumbnail_en.png", "storyboard/scene_manifest.en.json", "audio/alignment.en.json",
              "audio/en_audio_info.json", "qa/qa_report.json", "qa/qa_report.md", "qa/contact_sheet.jpg", "qa/warnings.json",
              "build_info.json", "sources.txt", "upload_metadata.md", "research/claims.json"):
        assert (job / f).exists(), f
    assert not (job / "video" / "explainer_ur.mp4").exists()
    assert not [p for p in job.rglob("*") if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac")]  # no audio leaks into outputs


def test_render_manifest_points_evidence_to_sources(rendered):
    job, _ = rendered
    m = json.loads((job / "storyboard" / "scene_manifest.en.json").read_text(encoding="utf-8"))
    assert manifest.validate(m, job) == []
    ev = [s for s in m["scenes"] if s["visual_type"] in manifest.FACTUAL_TYPES]
    assert ev and all(s["source_ids"] for s in ev)
    assert any(a["kind"] == "source_card" for s in m["scenes"] for a in s["assets"])
    assert any(s["claim_ids"] for s in m["scenes"])
    align = json.loads((job / "audio" / "alignment.en.json").read_text(encoding="utf-8"))
    assert align["phrase_fallback"] and align["timing_confidence"] == "low"  # estimated timing is labelled, not hidden
    q = json.loads((job / "qa" / "qa_report.json").read_text(encoding="utf-8"))["english"]["quality"]
    checks = {c["name"]: c for c in q["checks"]}
    assert checks["evidence_has_source_id"]["passed"] and checks["evidence_has_source_id"]["critical"]
    assert checks["scene_manifest_valid"]["passed"] and checks["no_black_frames"]["passed"]
    info = json.loads((job / "build_info.json").read_text(encoding="utf-8"))
    assert info["commercial_clearance"]["cleared"] is False and info["languages"]["urdu"]["status"] == "PERMANENT_FAILURE"


def test_render_refuses_tampered_package(package, tmp_path):
    from engine.package.render import RenderError, RenderInputs, render_package

    pkg, _ = package
    copy = tmp_path / "pkg"
    shutil.copytree(pkg, copy)
    (copy / "research" / "topic" / "english.txt").write_text("Changed words.\n", encoding="utf-8")
    with pytest.raises(RenderError, match="hash mismatch"):
        render_package(RenderInputs(copy, "english"), tmp_path / "out", log=lambda m: None)


def test_clone_voice_needs_consent_and_reference(package, tmp_path):
    from engine.package.render import RenderInputs, render_package

    pkg, _ = package
    tone(tmp_path / "me.wav", 3.0)
    ri = RenderInputs(pkg, "english", voice_modes={"english": "clone_voice"}, voice_reference=tmp_path / "me.wav", quality="draft")
    job, results = render_package(ri, tmp_path / "out", log=lambda m: None, tts_chain=[ToneTTS(load_config(repo_root=ROOT), ("en",))])
    assert results[0].status == Status.PERMANENT_FAILURE and "consent" in results[0].message
    assert not list(job.rglob("me.wav"))


def test_provided_audio_mode_without_audio_fails_clearly(package, tmp_path):
    from engine.package.render import RenderInputs, render_package

    pkg, _ = package
    _, results = render_package(RenderInputs(pkg, "english", voice_modes={"english": "provided_audio"}), tmp_path / "out", log=lambda m: None)
    assert results[0].status == Status.PERMANENT_FAILURE and "no audio" in results[0].message


def test_roman_urdu_package_renders_with_tts(tmp_path):
    from engine.package.render import RenderInputs, render_package

    cfg = research_cfg(load_config(repo_root=ROOT))
    pkg = Research(ResearchInputs("mortgage rates housing lock-in", "urdu", {"urdu": ROMAN_URDU}), cfg, tmp_path / "out", log=lambda m: None,
                   fetcher=fake_fetcher(tmp_path), sec=None, browser_factory=lambda: None, embedder=None).run()
    assert (pkg / "research" / "topic" / "roman_urdu.txt").exists() and not (pkg / "research" / "topic" / "urdu.txt").exists()
    job, results = render_package(RenderInputs(pkg, "urdu", quality="draft"), tmp_path / "out", log=lambda m: None,
                                  tts_chain=[ToneTTS(cfg, ("ur",))], transcriber=None, aligner=None)
    assert results[0].key == "roman_urdu" and results[0].status.ok, results[0].message
    assert (job / "video" / "explainer_ur.mp4").exists()


# -- CLI ---------------------------------------------------------------------------------
def test_cli_translation_needs_a_local_model_and_bad_packages_fail(tmp_path, capsys):
    (tmp_path / "en.txt").write_text("Rates rose to 6.8% this week.\n", encoding="utf-8")
    code = cli.main(["research", "--topic", "mortgage rates", "--english-script", str(tmp_path / "en.txt"), "--auto-translate", "true",
                     "--draft-model", "none", "--output", str(tmp_path / "out")])
    assert code == 2 and "no local model" in capsys.readouterr().out  # fails before any network request
    assert cli.main(["render", "--research-dir", str(tmp_path / "nope"), "--output", str(tmp_path)]) == 2
    assert "package.json not found" in capsys.readouterr().out


# -- regressions found in a live run ------------------------------------------------------
def test_one_blocked_official_feed_does_not_hide_the_others(tmp_path):
    from engine.package.providers import official_feeds

    web = FakeWeb({"https://stats.example.gov/robots.txt": (200, "User-agent: *\nDisallow: /feed\n")})
    errors = []
    items = official_feeds(fake_fetcher(tmp_path, web), "mortgage rates housing", ["https://stats.example.gov/feed.xml",
                                                                                     "https://agency.example.gov/feed.xml"], 10, errors)
    assert [i.url for i in items] == ["https://agency.example.gov/report"] and "robots" in errors[0]


def test_rate_limited_host_waits_at_least_its_published_interval(tmp_path):
    sleeps, n = [], []

    def opener(url, headers, timeout):
        if url.endswith("robots.txt"):
            return 404, url, {}, b""
        n.append(timeout)
        return (429, url, {}, b"") if len(n) == 1 else (200, url, {"Content-Type": "application/json"}, b"{}")

    f = Fetcher("ua/1", retries=1, backoff=0.5, min_interval=0, cache_dir=tmp_path, ttl_hours=0, opener=opener, sleep=sleeps.append)
    f.get("https://api.gdeltproject.org/api/v2/doc/doc?query=x")
    assert max(sleeps) >= 11 and n[0] >= 75  # GDELT: one request per 5 s, slow answers


def test_household_figures_are_not_company_finance():
    assert not finance.is_financial("Home sales are still low because owners locked in rates around 3% in 2021.")
    assert not finance.is_financial("Household debt hit $18 trillion.")
    assert finance.is_financial("Target posted a quarterly profit of $1 billion.")


# -- SRS completion: data gaps, research gates, readability, translation, privacy -------------
def test_missing_dataset_omits_chart_and_research_continues(tmp_path):
    class NoFred(FakeWeb):
        def __call__(self, url, headers, timeout):
            if url.startswith("https://fred.stlouisfed.org/graph/"):
                return 503, url, {}, b""
            return super().__call__(url, headers, timeout)

    cfg = research_cfg(load_config(repo_root=ROOT))
    job = run_research(ResearchInputs("mortgage rates housing lock-in", "english", {"english": ENGLISH}), cfg, tmp_path / "out", log=lambda m: None,
                       fetcher=fake_fetcher(tmp_path, NoFred()), sec=None, browser_factory=lambda: None, embedder=None)
    info = json.loads((job / "research" / "research.json").read_text(encoding="utf-8"))
    assert info["charts"] == [] and any("not downloaded" in w for w in info["warnings"])
    man = json.loads((job / "research" / "topic" / "evidence" / "manifest.json").read_text(encoding="utf-8"))
    assert man["charts"] == []  # no data, no chart: nothing invented


def test_research_gates_and_log(package):
    job, _ = package
    gates = json.loads((job / "research" / "qa.json").read_text(encoding="utf-8"))
    for name in ("claims_map_to_sources", "urls_canonical", "quotes_match_extracted_text", "chart_values_traceable",
                 "publication_date_vs_data_period", "unsupported_claims_flagged"):
        assert gates[name]["passed"], (name, gates[name])
    assert "EN-004" in gates["unsupported_claims_flagged"]["detail"]
    assert "research package ready" in (job / "research" / "research.log").read_text(encoding="utf-8")
    info = json.loads((job / "research" / "research.json").read_text(encoding="utf-8"))
    assert info["scripts"]["english"]["estimated_seconds"] > 5


def test_unreadable_screenshot_is_replaced_by_its_card(tmp_path, ex_cfg_pkg):
    from engine.explainer.layout import ExplainerLayout, Layout

    card = make_card(tmp_path / "card.png", "Pub", "Headline", "https://pub.example/x", excerpt="Rates rose to 6.8% this week across the country.")
    import cairo

    shot = cairo.ImageSurface(cairo.FORMAT_RGB24, 4000, 3000)  # a huge page: its 20 px words become ~5 px on screen
    shot.write_to_png(str(tmp_path / "shot.png"))
    art = Article("1", tmp_path / "shot.png", "https://pub.example/x", "Headline", "Pub", [("Rates", 10, 10, 60, 30)] * 5,
                  "S-x", "screenshot", "", tmp_path / "card.png", card["words"])
    lay = Layout([], [])
    shown = ExplainerLayout(ex_cfg_pkg, "en", None, 1.0).readable(art, lay)
    assert shown.capture == "source_card" and lay.text_px["article:1"] >= ex_cfg_pkg["animation"]["explainer"]["min_text_px"]


@pytest.fixture
def ex_cfg_pkg():
    return load_config(overrides={"animation": {"style": "explainer"}, "video": {"quality": "fast"}}, repo_root=ROOT)


def test_missing_language_translated_only_on_request(tmp_path, monkeypatch):
    from engine.explainer import draft
    from engine.package import research as R

    monkeypatch.setattr(R, "_model_available", lambda host, model: True)
    monkeypatch.setattr(draft, "ollama", lambda prompt, model, host, temperature=0.5, timeout=900: "مارگیج ریٹ اس ہفتے 6.8 فیصد ہو گیا۔")
    cfg = research_cfg(load_config(repo_root=ROOT))
    cfg["research"]["auto_translate_missing_language"] = True
    cfg["research"]["providers"] = {k: False for k in cfg["research"]["providers"]}
    cfg["research"]["official_charts"] = False
    job = run_research(ResearchInputs("mortgage rates", "both", {"english": "Mortgage rates rose to 6.8 percent this week.\n"}), cfg,
                       tmp_path / "out", log=lambda m: None, fetcher=fake_fetcher(tmp_path), sec=None, browser_factory=lambda: None,
                       embedder=None, draft_model="qwen2.5:7b")
    info = json.loads((job / "research" / "research.json").read_text(encoding="utf-8"))
    assert info["scripts"]["urdu"]["origin"] == "machine_translated" and info["scripts"]["urdu"]["numbers_match_source"]
    assert (job / "research" / "topic" / "urdu.txt").exists()


def test_pronunciations_only_change_what_the_voice_reads():
    from engine.voice_manager import _convert

    assert _convert("The SBP said so.", None, "en", {"SBP": "S B P"}) == "The S B P said so."
    assert _convert("SBPX stays.", None, "en", {"SBP": "S B P"}) == "SBPX stays."  # whole words only


def test_region_filters_gdelt_and_voice_files_stay_out_of_the_cache(tmp_path, monkeypatch):
    from engine.package.providers import gdelt
    from engine.tts.base import cache_dir, private_dir

    web = FakeWeb()
    gdelt(fake_fetcher(tmp_path, web), "rupee dollar rate", 5, region="PK")
    assert any("sourcecountry%3Apakistan" in u for u in web.calls)
    monkeypatch.setenv("WHITEBOARD_CACHE", str(tmp_path / "cache"))
    assert (tmp_path / "cache") in cache_dir("x").parents and (tmp_path / "cache") not in private_dir("omnivoice").parents


def test_visual_variety_flags_long_runs_of_one_type():
    m = minimal_manifest()
    sc = m["scenes"][0]
    m["scenes"] = [{**sc, "scene_id": f"scene_00{i}", "visual_type": "hand_drawn_illustration"} for i in range(1, 5)]
    assert manifest.repeated_types(m) == ["hand_drawn_illustration x4 (scene_001-scene_004)"]
    m["scenes"] = [{**sc, "scene_id": f"scene_00{i}", "visual_type": "data_chart"} for i in range(1, 5)]
    assert manifest.repeated_types(m) == []  # evidence may repeat


def test_render_log_and_overlap_checks(rendered):
    job, _ = rendered
    assert "voice mode" in (job / "qa" / "render.log").read_text(encoding="utf-8")
    checks = {c["name"] for c in json.loads((job / "qa" / "qa_report.json").read_text(encoding="utf-8"))["english"]["quality"]["checks"]}
    assert {"no_overlaps", "visual_variety", "evidence_text_readable", "no_long_frozen_runs", "audio_not_clipped"} <= checks


def test_package_pronunciations_reach_the_voice_only(package, tmp_path):
    from engine.package.job import write_package_manifest
    from engine.package.render import RenderInputs, render_package

    pkg, _ = package
    copy = tmp_path / "pkg"
    shutil.copytree(pkg, copy)
    (copy / "research" / "topic" / "pronunciations.yaml").write_text("Atlantis: at-LAN-tis\n", encoding="utf-8")
    old = json.loads((copy / "package.json").read_text(encoding="utf-8"))
    write_package_manifest(copy, {k: old[k] for k in ("topic", "language_mode", "label", "languages")})
    voice = ToneTTS(load_config(repo_root=ROOT), ("en",))
    job, results = render_package(RenderInputs(copy, "english", voice_modes={"english": "system_tts"}, quality="draft"), tmp_path / "out",
                                  log=lambda m: None, tts_chain=[voice], transcriber=None, aligner=None)
    assert results[0].status.ok, results[0].message
    spoken = " ".join(t for t, _ in voice.calls)
    assert "at-LAN-tis" in spoken and "Atlantis" not in spoken
    narration = " ".join(s["narration_text"] for s in json.loads((job / "storyboard" / "scene_manifest.en.json").read_text(encoding="utf-8"))["scenes"])
    assert "Atlantis" in narration and "at-LAN-tis" not in narration  # the board and manifest keep the script


def test_charts_tagged_at_the_number_and_at_named_years():
    from engine.explainer import draft
    from engine.explainer.cues import extract_cues

    pts = [[f"{y}-06-01", v] for y, v in ((2019, 4.0), (2020, 3.1), (2021, 2.96), (2022, 5.4), (2023, 6.8))] + \
          [[f"2026-{m:02d}-01", 7.3] for m in range(1, 10)]
    charts = [{"id": "MORTGAGE30US", "points": pts}]
    script = ("Mortgage rates in the US are back above 7 percent this month.\n\n"
              "Many owners locked in rates around 3% in 2021, so they do not want to sell.\n")
    out, _ = draft.auto_tag(script, "en", {}, {}, charts, True)
    assert "above [chart MORTGAGE30US 2026-09-01] 7 percent" in out  # after the headlines had their time
    assert "[chart MORTGAGE30US 2021] Many owners" in out
    assert extract_cues(out)[0].split() == script.split()


def test_year_context_lets_a_small_figure_count_partially():
    cl = C.extract_claims("Many owners locked in rates around 3% in 2021.", "en", "EN")
    C.match_claims(cl, [C.Doc("S-f", "30-Year Fixed Rate Mortgage Average was 2.96% on 2021-06-01.", "dataset", "fred.stlouisfed.org")])
    assert cl[0].status == "partially_supported" and cl[0].source_ids == ["S-f"]


def test_evidence_ranked_by_authority_then_directness():
    news = C.Doc("S-news", "Rates rose to 6.8% this week across the country, data show.", "news", "news.example.com", "2026-10-16")
    gov = C.Doc("S-gov", "Rates rose to 6.8% this week across the country.", "official", "agency.example.gov", "2026-10-10")
    cl = C.extract_claims("Rates rose to 6.8% this week across the country.", "en", "EN")
    C.match_claims(cl, [news, gov])
    assert cl[0].source_ids[0] == "S-gov" and cl[0].primary_source


def test_preliminary_and_stale_financial_figures_are_flagged():
    fc = finance.parse_claims("EN-1", "Revenue was $35.1 billion in Q3 fiscal 2025.")[0]
    finance.verify_in_text(fc, "Preliminary unaudited results.\nRevenue | $ 35,082 | $ 18,120", "S-rel", "u", "Issuer release 8-K", "issuer_release")
    assert any(f.startswith("preliminary") for f in fc.matches[0]["flags"])
    st = finance.parse_claims("EN-2", "Diluted EPS is now $1.24.")[0]
    finance.verify_with_facts(st, companyfacts(), "S", "u", "Acme")  # newest period ends 2024-12-31
    assert any(f.startswith("stale") for f in st.matches[0]["flags"])


def test_matched_release_row_becomes_a_labelled_evidence_card(tmp_path):
    cfg = research_cfg(load_config(repo_root=ROOT))
    r = Research(ResearchInputs("acme earnings", "english", {"english": "Acme revenue was $35.1 billion.\n"}), cfg, tmp_path / "out",
                 log=lambda m: None, fetcher=fake_fetcher(tmp_path), sec=None, browser_factory=lambda: None, embedder=None)
    r.ledger.add(make_source("https://www.sec.gov/Archives/x/ex99.htm", "Acme earnings release (8-K)", "Acme", "2026-10-01T00:00:00+00:00",
                             source_type="issuer_release"))
    fc = finance.parse_claims("EN-001", "Acme revenue was $35.1 billion.")[0]
    fc.company = "Acme"
    finance.verify_in_text(fc, "Revenue | $ 35,082 | $ 18,120", source_id("https://www.sec.gov/Archives/x/ex99.htm"),
                           "https://www.sec.gov/Archives/x/ex99.htm", "Issuer release 8-K", "issuer_release")
    cards = r.finance_cards(2, [fc])
    assert cards and cards[0]["id"] == "3" and cards[0]["capture"] == "source_card" and cards[0]["source_id"]
    assert (r.shots / "finance_3.png").exists() and "35,082" in " ".join(w[0] for w in cards[0]["words"])


def test_chart_year_labels_clear_the_source_line():
    import datetime as dt

    from engine.explainer.charts import ChartPainter
    from engine.explainer.evidence import Chart

    ch = Chart("X", "T", "%", "Src", "u", [(dt.date(2019, 1, 1), 1.0), (dt.date(2026, 1, 1), 2.0)])
    region = (0, 700, 1080, 1800)
    plot = ChartPainter(ch, 0.0).plot_rect(region)
    h = min(region[3] - region[1], 760)
    year_baseline, source_top = plot[3] + 32, region[1] + h - 24 - 26
    assert year_baseline + 4 <= source_top  # 20 px year labels and the 26 px source line never touch
