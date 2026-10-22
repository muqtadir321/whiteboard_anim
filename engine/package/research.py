"""Research & Prepare: topic (+ optional scripts and URLs) -> research package (no rendering).

Order of work: validate inputs -> discover documents (user URLs, official feeds, GDELT, opt-in
providers) -> capture the best pages (screenshot, else a source card from verified metadata)
-> official charts (FRED) -> source ledger -> claims (+ financial verification) -> cue tags
in the supplied scripts (words unchanged) -> draft storyboard and consistency report ->
package.json with hashes. Every provider can fail without stopping the others.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..explainer import draft, fred
from ..explainer.cues import extract_cues
from ..explainer.evidence import Evidence
from ..explainer.sources import PAYWALLED
from ..language_detector import detect_language
from ..script_analyzer import normalize_script_with_cues
from . import claims as claims_mod
from . import consistency, finance, providers
from .extract import parse_html
from .http import FetchError, Fetcher
from .job import create_job_dir, new_job_id, sha256_text, write_package_manifest
from .ledger import Ledger, make_source
from .source_card import excerpt_of, make_card, make_headline_cards
from .urls import canonical_url, domain, source_id

CODES = {"english": "en", "urdu": "ur", "roman_urdu": "roman_urdu"}
MODES = {"english": ("english",), "urdu": ("urdu",), "both": ("english", "urdu")}


class ResearchError(ValueError):
    pass


@dataclass
class ResearchInputs:
    topic: str
    language_mode: str = "both"
    scripts: dict[str, str] = field(default_factory=dict)  # "english" / "urdu" (Urdu script or Roman Urdu, as supplied)
    urls: list[str] = field(default_factory=list)
    region: str = "global"
    depth: str = "standard"
    duration_seconds: float | None = None
    label: str = ""

    def validate(self) -> None:
        if not self.topic.strip() or len(self.topic) > 300:
            raise ResearchError("topic is required (at most 300 characters)")
        if self.language_mode not in MODES:
            raise ResearchError(f"language_mode must be english, urdu or both, not {self.language_mode!r}")
        if self.depth not in ("standard", "deep"):
            raise ResearchError("research_depth must be standard or deep")
        for k, v in self.scripts.items():
            if k not in ("english", "urdu"):
                raise ResearchError(f"unknown script language {k!r}")
            if len(v.encode("utf-8")) > 200_000:
                raise ResearchError(f"{k} script is larger than 200 kB")


def urdu_form(text: str, mode: str) -> tuple[str, str]:
    """(language key, reason): the supplied form is kept; auto picks it from the script."""
    if mode in ("urdu", "roman_urdu"):
        return mode, f"ur_script_mode={mode}"
    det = detect_language(text, "ur")
    if det.code == "ur":
        return "urdu", f"detected Urdu script ({det.confidence:.2f})"
    return "roman_urdu", f"detected Latin script, treated as Roman Urdu ({det.code}, {det.confidence:.2f})"


WORDS_PER_SECOND = {"en": 2.5, "ur": 2.2, "roman_urdu": 2.3}  # typical narration pace (150 / 130 / 140 words a minute)


def estimate_seconds(text: str, code: str) -> float:
    words = len(extract_cues(text)[0].split())
    return round(words / WORDS_PER_SECOND.get(code, 2.4), 1)


def _model_available(host: str, model: str) -> bool:
    from ..explainer.prepare import model_available

    return model_available(host, model)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _default_browser():
    from ..explainer.capture import Browser

    return Browser()


def shingles(text: str, n: int = 6) -> set[int]:
    w = re.findall(r"\w+", text.lower())
    return {hash(" ".join(w[i:i + n])) for i in range(max(0, len(w) - n + 1))}


def mark_syndicated(docs: list[claims_mod.Doc], ledger: Ledger) -> None:
    """Same article on two sites (wire copy): the later/less official one is a duplicate, not a second source."""
    rank = {"official": 0, "primary_filing": 0, "issuer_release": 0, "dataset": 0, "user_provided": 1, "news": 2}
    sh = {d.source_id: shingles(d.text) for d in docs}
    order = sorted(docs, key=lambda d: (rank.get(d.source_type, 3), d.published_at or "9999"))
    for i, a in enumerate(order):
        if a.duplicate_of or len(sh[a.source_id]) < 30:
            continue
        for b in order[i + 1:]:
            if b.duplicate_of or b.domain == a.domain or len(sh[b.source_id]) < 30:
                continue
            j = len(sh[a.source_id] & sh[b.source_id]) / len(sh[a.source_id] | sh[b.source_id])
            if j >= 0.6:
                b.duplicate_of = a.source_id
                src = ledger.get(b.source_id)
                if src:
                    src.duplicate_of = a.source_id
                    src.notes = (src.notes + f" Syndicated copy of {a.source_id} (text overlap {j:.0%}).").strip()


def chart_doc(sid: str, chart: dict) -> claims_mod.Doc:
    """Verified chart values as sentences, so narration figures can be matched to the dataset."""
    pts = chart["points"]
    u = chart["units"]
    keep = {pts[0], pts[-1], min(pts, key=lambda p: p[1]), max(pts, key=lambda p: p[1])}
    years: dict[str, tuple] = {}
    for p in pts:
        years[p[0][:4]] = p
    keep |= set(years.values())
    sents = [f"{chart['title']} was {v}{'%' if u == '%' else ''} on {d}." if u != "$" else f"{chart['title']} was ${v} on {d}."
             for d, v in sorted(keep)]
    return claims_mod.Doc(sid, " ".join(sents), "dataset", domain(chart["url"]), pts[-1][0], sents=sents)


class Research:
    def __init__(self, inp: ResearchInputs, cfg: dict, out_root: Path, log=print, fetcher: Fetcher | None = None, sec="auto",
                 browser_factory=_default_browser, embedder="auto", draft_model: str = "none", ollama_host: str = "http://127.0.0.1:11434"):
        inp.validate()
        self.inp, self.cfg, self.rc = inp, cfg, cfg["research"]
        self.log = log
        self.f = fetcher or Fetcher.from_config(self.rc)
        self.sec = sec
        self.browser_factory = browser_factory
        self.embedder = embedder
        self.draft_model, self.ollama_host = draft_model, ollama_host
        self.job = create_job_dir(out_root, new_job_id(inp.label or inp.topic))
        self.res = self.job / "research"
        self.topic_dir = self.res / "topic"
        self.ev = self.topic_dir / "evidence"
        self.shots = self.res / "screenshots"
        for d in (self.ev, self.shots, self.res / "extracted_text", self.res / "charts", self.res / "data", self.res / "scripts",
                  self.job / "storyboard"):
            d.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger()
        self.coverage: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.docs: list[claims_mod.Doc] = []
        self.sid_of: dict[str, str] = {}  # discovered URL -> source id it resolved to (after redirects/canonical links)
        self._lines: list[str] = []  # research.log
        outer = log

        def logged(msg: str) -> None:
            self._lines.append(f"{_now()} {msg}")
            outer(msg)

        self.log = logged

    # -- helpers ---------------------------------------------------------------------
    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self.log(f"warning: {msg}")

    def _save_text(self, sid: str, text: str) -> str:
        rel = f"research/extracted_text/{sid}.txt"
        (self.job / rel).write_text(text, encoding="utf-8")
        return rel

    def fetch_page(self, url: str, provider: str, source_type: str, title: str = "", publisher: str = "", published: str = ""):
        """HTTP fetch + text extraction. Returns (Source, Page | None); failures are ledger rows too."""
        try:
            res = self.f.get(url, "text/html,application/xhtml+xml")
            page = parse_html(res.text)
            final = page.canonical if page.canonical.startswith("http") else res.final_url
            status = "paywall_or_blocked" if page.blocked or len(page.text) < 200 else ("cached" if res.from_cache else "ok")
            if status == "paywall_or_blocked":  # a bot check's <title> ("Just a moment...") is not the document's
                page.title, final = "", url
            src = make_source(final or url, page.title or title or url, page.publisher or publisher or domain(url), res.fetched_at,
                              published_at=page.published_at or published, source_type=source_type, author=page.author,
                              retrieval_method="cache" if res.from_cache else "http", retrieval_status=status, provider=provider,
                              license_or_terms_url="")
            src = self.ledger.add(src)
            self.sid_of[url] = src.source_id
            if status in ("ok", "cached"):
                src.extracted_text_path = self._save_text(src.source_id, page.text)
                self.docs.append(claims_mod.Doc(src.source_id, page.text, source_type, src.domain, src.published_at))
                return src, page
            return src, None
        except FetchError as exc:
            status = "blocked_robots" if "robots" in exc.reason else ("http_error" if exc.status else "network_error")
            src = self.ledger.add(make_source(url, title or url, publisher or domain(url), _now(), published_at=published, source_type=source_type,
                                              retrieval_method="http", retrieval_status=status, provider=provider, notes=exc.reason))
            return src, None

    # -- steps -----------------------------------------------------------------------
    def scripts(self) -> dict[str, str]:
        """Supplied scripts kept byte for byte (research/scripts/<key>.original.txt) and NFC for processing."""
        out = {}
        wanted = MODES[self.inp.language_mode]
        self.script_info: dict[str, dict] = {}
        for lang, text in self.inp.scripts.items():
            text = text.lstrip("﻿")
            if not text.strip():
                continue
            if lang not in wanted:
                self.warn(f"{lang} script supplied but language_mode is {self.inp.language_mode}: ignored")
                continue
            key, why = (lang, "supplied") if lang == "english" else urdu_form(text, self.rc["ur_script_mode"])
            (self.res / "scripts" / f"{key}.original.txt").write_text(text, encoding="utf-8")
            out[key] = unicodedata.normalize("NFC", text)
            self.script_info[key] = {"origin": "supplied", "form": why, "sha256": sha256_text(text), "characters": len(text)}
        for lang in wanted:
            have = lang in out or (lang == "urdu" and ("urdu" in out or "roman_urdu" in out))
            if have or not out:
                continue
            if not self.rc["auto_translate_missing_language"]:
                self.warn(f"no {lang} script supplied; it is not translated (research.auto_translate_missing_language is false)")
                continue
            out[lang] = self.translate(lang, out)
        for key, text in out.items():  # SRS: duration is inferred from the narration unless given
            self.script_info[key]["estimated_seconds"] = estimate_seconds(text, CODES[key])
            if self.inp.duration_seconds and abs(self.script_info[key]["estimated_seconds"] - self.inp.duration_seconds) > 0.25 * self.inp.duration_seconds:
                self.warn(f"{key} narration is about {self.script_info[key]['estimated_seconds']:.0f} s, target is {self.inp.duration_seconds:.0f} s")
        return out

    def translate(self, lang: str, have: dict[str, str]) -> str:
        """Only with research.auto_translate_missing_language: a free local model (Ollama). The result is
        marked machine-translated; its numbers are compared with the source script."""
        from .. import numbers as nums

        if self.draft_model in ("", "none") or not _model_available(self.ollama_host, self.draft_model):
            raise ResearchError(f"auto_translate_missing_language is true but no local model is reachable ({self.draft_model!r} at "
                                f"{self.ollama_host}); supply the {lang} script or start Ollama and pass --draft-model")
        src_key = "english" if "english" in have else next(iter(have))
        src = have[src_key]
        if lang == "urdu":
            prompt = draft.urdu_prompt(src)
        else:
            prompt = ("Translate this narration into natural spoken English. Keep every number, name and date exactly as given, "
                      "keep the paragraph breaks, add nothing. Return only the translation.\n\n" + src)
        text = draft.clean_output(draft.ollama(prompt, self.draft_model, self.ollama_host, temperature=0.2))
        if not text.strip():
            raise ResearchError(f"the local model returned no {lang} translation")
        key = "urdu" if lang == "urdu" else "english"
        (self.res / "scripts" / f"{key}.machine_translated.txt").write_text(text, encoding="utf-8")
        a = sorted(round(q.value, 4) for q in nums.extract_quantities(src, CODES[src_key]))
        b = sorted(round(q.value, 4) for q in nums.extract_quantities(text, CODES[key]))
        self.script_info[key] = {"origin": "machine_translated", "from": src_key, "model": self.draft_model, "sha256": sha256_text(text),
                                 "numbers_match_source": a == b}
        self.warn(f"{key} script was machine-translated from {src_key} with {self.draft_model}: review it before rendering"
                  + ("" if a == b else f" (numbers differ: {a} vs {b})"))
        return unicodedata.normalize("NFC", text)

    def discover(self) -> list[providers.Found]:
        limit = self.rc["max_news_items"] * (2 if self.inp.depth == "deep" else 1)
        found: list[providers.Found] = []
        urls, bad = providers.clean_user_urls(self.inp.urls)
        for b in bad:
            self.warn(f"source URL rejected: {b}")
        self.user_urls = urls
        feed_errors: list[str] = []
        jobs = [("official_feeds", lambda: providers.official_feeds(self.f, self.inp.topic, self.rc["official_feeds"], limit, feed_errors)),
                ("gdelt", lambda: providers.gdelt(self.f, self.inp.topic, limit, region=self.inp.region)),
                ("bing_news_rss", lambda: providers.bing_news(self.f, self.inp.topic, limit)),
                ("google_news_rss", lambda: providers.google_news(self.f, self.inp.topic, limit))]
        for name, run in jobs:
            meta = providers.PROVIDERS[name]
            if not self.rc["providers"].get(name):
                self.coverage[name] = {"status": "disabled", "items": 0, **meta}
                continue
            try:
                items = run()
                self.coverage[name] = {"status": "ok", "items": len(items), **meta}
                if name == "official_feeds" and feed_errors:
                    self.coverage[name].update(status="partial", error="; ".join(feed_errors)[:300])
                    for e in feed_errors:
                        self.warn(f"official feed skipped: {e}")
                found += items
            except Exception as exc:
                reason = exc.reason if isinstance(exc, FetchError) else f"{type(exc).__name__}: {exc}"
                self.coverage[name] = {"status": "blocked" if "robots" in str(reason) else "failed", "items": 0, "error": str(reason)[:300], **meta}
                self.warn(f"provider {name} failed: {reason}")
        seen, uniq = set(), []
        for it in found:
            key = canonical_url(it.url) if it.resolved else it.title.lower()
            if key not in seen:
                seen.add(key)
                uniq.append(it)
        return uniq

    def rank(self, items: list[providers.Found]) -> list[providers.Found]:
        """Official first, paywalls last, then by how many topic words the headline has, newest first."""
        def newest(it):
            try:
                return -dt.datetime.fromisoformat(it.published_at).timestamp()
            except ValueError:
                return 0.0

        def score(it):
            paywall = any(domain(it.url).endswith(p) for p in PAYWALLED)
            return (0 if it.source_type == "official" else 1, paywall, -providers.relevance(self.inp.topic, it.title), newest(it))
        return sorted([it for it in items if providers.relevance(self.inp.topic, it.title) >= 0.34 or it.source_type == "official"], key=score)

    def capture(self, items: list[providers.Found]) -> tuple[list[dict], list[dict]]:
        """Up to max_articles pages shown in the video: screenshot, else a source card. More pages are read for claims."""
        n_show = self.rc["max_articles"] * (2 if self.inp.depth == "deep" else 1)
        n_read = n_show * 3
        browser, why = None, ""
        if self.rc["screenshots"]:
            try:
                browser = self.browser_factory()
            except Exception as exc:
                why = f"{type(exc).__name__}: {exc}"
            if browser is None:
                self.warn(f"screenshots unavailable ({why or 'no browser'}); source cards are used instead")
        self.coverage["screenshots"] = {"status": "ok" if browser else "unavailable", "detail": why}
        cands = [("user_provided", u, None) for u in self.user_urls] + [(it.source_type, it.url, it) for it in items[:n_read]]
        articles, cards = [], []
        try:
            for stype, url, it in cands:
                if len(articles) >= n_show and len([d for d in self.docs if d.source_type != "dataset"]) >= n_read:
                    break
                title, pub, date = (it.title, it.publisher, it.published_at) if it else ("", "", "")
                show = len(articles) < n_show
                k = len(articles) + 1
                cap = None
                if show and browser is not None:
                    cap = browser.article(url, self.shots / f"article_{k}.png")
                if cap is not None:
                    src = self.ledger.add(make_source(cap.final_url, cap.title or title, pub or domain(cap.final_url), _now(), published_at=date,
                                                      source_type=stype, retrieval_method="playwright", retrieval_status="ok",
                                                      provider=it.provider if it else "user"))
                    self.sid_of[url] = src.source_id
                    text = "\n\n".join(cap.paragraphs)
                    src.extracted_text_path = self._save_text(src.source_id, text)
                    src.screenshot_path = f"research/screenshots/article_{k}.png"
                    self.docs.append(claims_mod.Doc(src.source_id, text, stype, src.domain, src.published_at))
                    # a readable alternative from the same verified text, used when the page's own text is too small on screen
                    best = max(cap.paragraphs, key=lambda p: (providers.relevance(self.inp.topic, p), bool(re.search(r"\d", p))), default="")
                    alt = make_card(self.shots / f"article_{k}_card.png", src.publisher or src.domain, src.title, src.canonical_url,
                                    src.published_at, excerpt_of(best) if best else "", "Source card · text from the captured page")
                    articles.append({"id": str(k), "image": f"../../screenshots/article_{k}.png", "url": src.canonical_url, "title": src.title,
                                     "publisher": src.publisher, "words": cap.words, "source_id": src.source_id, "capture": "screenshot",
                                     "published": src.published_at, "paragraphs": cap.paragraphs,
                                     "alt_image": f"../../screenshots/article_{k}_card.png", "alt_words": alt["words"]})
                    self.log(f"  screenshot {src.domain}: {src.title[:70]}")
                    continue
                if it is not None and not it.resolved:
                    src = self.ledger.add(make_source(url, title, pub, _now(), published_at=date, source_type=stype, retrieval_method="rss",
                                                      retrieval_status="metadata_only", provider=it.provider,
                                                      notes="aggregator link; publisher page not reachable without a browser"))
                    page = None
                else:
                    src, page = self.fetch_page(url, it.provider if it else "user", stype, title, pub, date)
                if not show:
                    continue
                if page is None and it is None:
                    continue  # a URL we could not read and know nothing else about: nothing verified to show
                paras = page.paragraphs if page else []
                best = max(paras, key=lambda p: (providers.relevance(self.inp.topic, p), bool(re.search(r"\d", p))), default="")
                ex = excerpt_of(best) if best else ""
                card = make_card(self.shots / f"card_{k}.png", src.publisher or src.domain, src.title, src.canonical_url, src.published_at, ex,
                                 "Source card · page not captured" if browser else "Source card · text from the page" if ex else "Source card · headline only")
                src.screenshot_path = f"research/screenshots/card_{k}.png"
                src.notes = (src.notes + " Shown as a source card (not a screenshot).").strip()
                articles.append({"id": str(k), "image": f"../../screenshots/card_{k}.png", "url": src.canonical_url, "title": src.title,
                                 "publisher": src.publisher, "words": card["words"], "source_id": src.source_id, "capture": "source_card",
                                 "published": src.published_at, "paragraphs": paras})
                cards.append({"source_id": src.source_id, "excerpt": bool(ex)})
                self.log(f"  source card {src.domain}: {src.title[:70]}")
            heads = [{"title": it.title, "publisher": it.publisher or domain(it.url), "url": it.url, "published": it.published_at,
                      "source_id": self.sid_of.get(it.url) or (source_id(it.url) if it.resolved else "")} for it in items[:4]]
            news = []
            if heads:
                if browser is not None:
                    try:
                        news = browser.news_cards([{**h, "age": h["published"][:10]} for h in heads], self.shots / "news_cards.png")
                    except Exception as exc:
                        self.warn(f"headline cards via browser failed ({exc}); drawing them instead")
                if not news:
                    names = {"official_feeds": "official feeds", "gdelt": "GDELT", "bing_news_rss": "Bing News", "google_news_rss": "Google News"}
                    note = "Headlines found via " + ", ".join(sorted({names.get(it.provider, it.provider) for it in items[:4]}))
                    news = make_headline_cards(self.shots / "news_cards.png", heads, note)
                for h in heads:
                    if h["source_id"] and not self.ledger.get(h["source_id"]):
                        self.ledger.add(make_source(h["url"], h["title"], h["publisher"], _now(), published_at=h["published"], source_type="news",
                                                    retrieval_method="rss", retrieval_status="metadata_only", provider="discovery"))
        finally:
            if browser is not None:
                browser.close()
        return articles, news

    def charts(self, text_pool: str) -> list[dict]:
        if not self.rc["official_charts"]:
            self.coverage["fred"] = {"status": "disabled"}
            return []
        out = []
        for sid in fred.pick_series(text_pool):
            title, units, source, _, transform = fred.SERIES[sid]
            start = (dt.date.today() - dt.timedelta(days=365 * 8)).isoformat()
            csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}" + (f"&transformation={transform}" if transform else "")
            try:
                res = self.f.get(csv_url, "text/csv")
            except FetchError as exc:
                self.warn(f"chart {sid} not downloaded: {exc.reason}")
                continue
            points = fred.parse_csv(res.body)
            if len(points) < 10:
                self.warn(f"chart {sid}: too few data points")
                continue
            (self.res / "charts" / f"{sid}.csv").write_bytes(res.body)
            if len(points) > 600:
                step = len(points) // 400 + 1
                points = points[::step] + ([points[-1]] if (len(points) - 1) % step else [])
            page = f"https://fred.stlouisfed.org/series/{sid}"
            src = self.ledger.add(make_source(page, title, source, res.fetched_at, data_period=f"{points[0][0]} to {points[-1][0]}",
                                              source_type="dataset", retrieval_method="api", provider="fred",
                                              license_or_terms_url="https://fred.stlouisfed.org/legal/",
                                              notes=f"series {sid}; transformation: {transform or 'none (as published)'}; "
                                                    f"downloaded from {csv_url}"))
            chart = {"id": sid, "title": title, "units": units, "source": source, "url": page, "points": points, "source_id": src.source_id,
                     "series_id": sid, "transformation": transform or "", "retrieved_at": res.fetched_at, "csv": f"research/charts/{sid}.csv"}
            (self.res / "charts" / f"{sid}.json").write_text(json.dumps(chart, indent=1), encoding="utf-8")
            self.docs.append(chart_doc(src.source_id, chart))
            out.append(chart)
        self.coverage["fred"] = {"status": "ok", "items": len(out), "commercial_status": "unknown",
                                 "terms": "https://fred.stlouisfed.org/legal/ (some series are third-party copyrighted)"}
        return out

    def financial(self, claims: list[claims_mod.Claim]) -> list[finance.FinancialClaim]:
        fc_list = []
        for c in claims:
            if c.language == "en" and c.kind == "factual" and finance.is_financial(c.text):
                fc_list += finance.parse_claims(c.claim_id, c.text, c.language)
        mode = self.rc["finance"]["enabled"]
        if not fc_list or mode is False:
            self.coverage["finance"] = {"status": "not_needed" if not fc_list else "disabled"}
            return []
        for fc in fc_list:
            for cl in claims:
                if fc.claim_id.startswith(cl.claim_id):
                    cl.finance = True
        fin = self.rc["finance"]
        sec = self.sec
        if sec == "auto":
            from .sec import SecClient

            sec = SecClient.from_config(self.rc) if fin["sec_edgar"] else None
        cov = {"status": "ok", "sec_edgar": "skipped" if sec is None else "ok", "issuer_releases": 0, "companies": []}
        if sec is None and fin["sec_edgar"]:
            self.warn("SEC EDGAR skipped: set SEC_USER_AGENT to 'Your Name you@example.com' (SEC fair-access rule)")
        companies = []
        if sec is not None:
            try:
                companies = sec.find(" ".join([self.inp.topic] + [c.text for c in claims]), fin["max_companies"])
            except Exception as exc:
                cov["sec_edgar"] = f"failed: {exc}"
                self.warn(f"SEC company lookup failed: {exc}")
        data_dir = self.res / "data" / "finance"
        data_dir.mkdir(parents=True, exist_ok=True)
        for comp in companies:
            cov["companies"].append({"ticker": comp.ticker, "cik": comp.cik, "name": comp.title})
            mine = [fc for fc in fc_list if not fc.ticker and (comp.ticker in fc.text or comp.title.split()[0].lower() in fc.text.lower() or len(companies) == 1)]
            try:
                facts = sec.facts(comp.cik)
                facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{comp.cik:010d}.json"
                src = self.ledger.add(make_source(facts_url, f"XBRL company facts: {comp.title}", "U.S. SEC (EDGAR)", _now(), source_type="primary_filing",
                                                  retrieval_method="api", provider="sec_edgar", confidence="high",
                                                  license_or_terms_url="https://www.sec.gov/privacy#dissemination"))
                used = {}
                for fc in mine:
                    fc.company, fc.ticker, fc.cik = comp.title, comp.ticker, comp.cik
                    finance.verify_with_facts(fc, facts, src.source_id, facts_url, comp.title)
                    for tag in finance.METRICS[fc.metric].get("tags", ()):
                        ns, _, name = tag.rpartition(":")
                        blk = facts.get("facts", {}).get(ns or "us-gaap", {}).get(name)
                        if blk:
                            used[tag] = blk
                (data_dir / f"{comp.ticker}_facts_used.json").write_text(json.dumps({"cik": comp.cik, "source": facts_url, "facts": used}, indent=1),
                                                                         encoding="utf-8")
            except Exception as exc:
                self.warn(f"SEC facts for {comp.ticker} failed: {exc}")
            if fin["issuer_releases"]:
                try:
                    for rel in sec.earnings_releases(comp.cik, limit=2):
                        text = sec.document_text(rel["url"])
                        rsrc = self.ledger.add(make_source(rel["url"], f"{comp.title} earnings release ({rel['form']} filed {rel['filed']})",
                                                           comp.title, _now(), published_at=rel["filed"], data_period=rel.get("report_date", ""),
                                                           source_type="issuer_release", retrieval_method="http", provider="sec_edgar",
                                                           notes=f"8-K item 2.02 exhibit, accession {rel['accession']}"))
                        rsrc.extracted_text_path = self._save_text(rsrc.source_id, text)
                        self.docs.append(claims_mod.Doc(rsrc.source_id, text, "issuer_release", rsrc.domain, rel["filed"]))
                        cov["issuer_releases"] += 1
                        for fc in mine:
                            finance.verify_in_text(fc, text, rsrc.source_id, rel["url"], f"Issuer release {rel['form']} {rel['accession']}", "issuer_release")
                except Exception as exc:
                    self.warn(f"earnings releases for {comp.ticker} failed: {exc}")
            if fin["yahoo_finance_links"]:
                y = f"https://finance.yahoo.com/quote/{comp.ticker}/financials"
                self.ledger.add(make_source(y, f"{comp.title} financials (Yahoo Finance)", "Yahoo Finance", _now(), source_type="reference",
                                            retrieval_method="none", retrieval_status="not_fetched", commercial_status="restricted",
                                            provider="yahoo_finance_link", notes="discovery link for a human cross-check; not fetched (terms restrict automated access)"))
        for fc in fc_list:  # news and user pages as independent cross-checks
            for d in self.docs:
                if d.source_type in ("news", "user_provided", "official") and not d.duplicate_of:
                    finance.verify_in_text(fc, d.text, d.source_id, self.ledger.get(d.source_id).canonical_url if self.ledger.get(d.source_id) else "",
                                           f"{d.domain}", d.source_type)
            if not fc.ticker:
                fc.limitations.append("company not identified (name or ticker not found in SEC's list)")
            finance.summarise(fc, self.ledger, fin["min_sources_per_claim"])
            for m in fc.matches:
                if m["status"] != "not_found":
                    self.ledger.link(m["source_id"], fc.claim_id)
        finance.write_reports(self.res, fc_list)
        self.coverage["finance"] = cov
        return fc_list

    # -- all -------------------------------------------------------------------------
    def run(self) -> Path:
        inp = self.inp
        self.log(f"research job {self.job.name}: {inp.topic!r} ({inp.language_mode}, {inp.depth})")
        scripts = self.scripts()
        items = self.rank(self.discover())
        self.log(f"{len(items)} relevant documents discovered")
        articles, news = self.capture(items)
        text_pool = " ".join([inp.topic] + list(scripts.values()) + [it.title for it in items[:20]])
        charts = self.charts(text_pool)
        mark_syndicated(self.docs, self.ledger)
        emb = None
        if self.embedder == "auto":
            from ..explainer.semantic import get_embedder

            emb = get_embedder(log=self.log) if self.cfg["animation"]["explainer"]["semantic"] else None
        else:
            emb = self.embedder
        all_claims: dict[str, list[claims_mod.Claim]] = {}
        for key, text in scripts.items():
            plain = normalize_script_with_cues(text)[0]  # the same sentences the renderer times
            cl = claims_mod.extract_claims(plain, CODES[key], {"english": "EN", "urdu": "UR", "roman_urdu": "RU"}[key])
            claims_mod.match_claims(cl, self.docs, self.rc["claims"]["min_word_overlap"], self.rc["claims"]["outdated_after_days"], emb)
            all_claims[key] = cl
        fin = self.financial(all_claims.get("english", []))
        for cl in all_claims.values():
            for c in cl:
                for sid in c.source_ids:
                    self.ledger.link(sid, c.claim_id)
        # evidence for the renderer (paths relative to topic/evidence)
        manifest = {"news": {"image": "../../screenshots/news_cards.png", "cards": news} if news else {},
                    "articles": [{k: v for k, v in a.items() if k != "paragraphs"} for a in articles],
                    "charts": charts, "logos": []}
        (self.ev / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        evidence = Evidence.load(self.ev)
        paragraphs = {a["id"]: a["paragraphs"] for a in articles}
        tagged: dict[str, str] = {}
        for key, text in scripts.items():
            out, notes = draft.auto_tag(text, CODES[key], evidence.articles, paragraphs, charts, bool(news), emb=emb)
            if extract_cues(out)[0].split() != extract_cues(text)[0].split():
                raise ResearchError(f"internal error: tagging changed the {key} narration")  # never ship changed words
            tagged[key] = out
            (self.topic_dir / f"{key}.txt").write_text(out, encoding="utf-8")
            self.script_info[key]["visual_tags"] = notes
        if "english" in MODES[inp.language_mode] and "english" not in scripts:
            text = draft.extractive_draft([{"title": n.get("title", "")} for n in news], articles, charts)
            (self.topic_dir / "english.txt").write_text(text, encoding="utf-8")
            self.script_info["english"] = {"origin": "generated_draft", "note": "built from source sentences; has {VERIFY} placeholders and "
                                                                                 "must be rewritten and checked before rendering"}
            self.warn("no English script supplied: an extractive draft was written (rendering refuses it until {VERIFY} is resolved)")
        # storyboard drafts + consistency
        for key, text in tagged.items():
            sb = []
            for pi, para in enumerate(p for p in text.strip().split("\n\n") if p.strip()):
                plain, cues = extract_cues(para)
                cl = [c for c in all_claims[key] if c.paragraph == pi]
                ids = {a["id"]: a["source_id"] for a in articles}
                cue_src = [ids.get(c.ref) if c.kind == "article" else next((ch["source_id"] for ch in charts if ch["id"] == c.ref), None)
                           if c.kind == "chart" else None for c in cues]
                sb.append({"group": pi + 1, "narration": plain.strip(), "cues": [c.to_dict() for c in cues],
                           "claim_ids": [c.claim_id for c in cl], "source_ids": sorted({s for s in cue_src if s} | {s for c in cl for s in c.source_ids})})
            (self.job / "storyboard" / f"draft_storyboard.{CODES[key]}.json").write_text(json.dumps(sb, ensure_ascii=False, indent=1), encoding="utf-8")
        cons = consistency.report(tagged, CODES, all_claims)
        (self.job / "storyboard" / "consistency_report.json").write_text(json.dumps(cons, ensure_ascii=False, indent=1), encoding="utf-8")
        # reports
        flat = [c for cl in all_claims.values() for c in cl]
        (self.res / "claims.json").write_text(json.dumps([c.to_dict() for c in flat], ensure_ascii=False, indent=1), encoding="utf-8")
        self.ledger.write(self.res)
        gates = self.qa_gates(tagged, articles, charts, flat)
        (self.res / "qa.json").write_text(json.dumps(gates, ensure_ascii=False, indent=1), encoding="utf-8")
        cov_lines = ["## Research QA", ""] + [f"- {k}: {'pass' if v['passed'] else 'FAIL'} {v.get('detail', '')}".rstrip() for k, v in gates.items()]
        cov_lines += ["", "## Research coverage", ""] + [f"- {k}: {v.get('status')} ({v.get('items', '')} items) {v.get('error', '')}".rstrip()
                                                    for k, v in self.coverage.items()]
        (self.res / "source_report.md").write_text(claims_mod.report_markdown(flat, self.ledger) + "\n" + "\n".join(cov_lines) + "\n" +
                                                   ("\n## Warnings\n\n" + "\n".join(f"- {w}" for w in self.warnings) + "\n" if self.warnings else ""),
                                                   encoding="utf-8")
        info = {"topic": inp.topic, "language_mode": inp.language_mode, "region": inp.region, "depth": inp.depth, "label": inp.label,
                "duration_seconds": inp.duration_seconds, "scripts": self.script_info, "user_urls": self.user_urls,
                "providers": self.coverage, "articles": [{k: a[k] for k in ("id", "source_id", "capture", "url")} for a in articles],
                "charts": [c["id"] for c in charts], "financial_claims": len(fin),
                "claim_status": {s: sum(1 for c in flat if c.status == s) for s in claims_mod.STATUSES},
                "requests": {"total": len(self.f.log), "failed": sum(1 for r in self.f.log if not r["status"] or r["status"] >= 400)},
                "qa": {k: v["passed"] for k, v in gates.items()},
                "warnings": self.warnings, "created_at_utc": _now()}
        (self.res / "research.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        self.log(f"research package ready: {self.job}")
        (self.res / "research.log").write_text("\n".join(self._lines) + "\n", encoding="utf-8")
        write_package_manifest(self.job, {"topic": inp.topic, "language_mode": inp.language_mode, "label": inp.label,
                                          "languages": {k: v.get("origin") for k, v in self.script_info.items()}})
        return self.job

    def qa_gates(self, tagged: dict[str, str], articles: list[dict], charts: list[dict], claims: list) -> dict:
        """SRS research gates: claims map to sources, URLs canonical, quotes exact, chart values traceable,
        publication date kept apart from data period, unsupported claims flagged."""
        from .claims import Doc  # noqa: F401  (same sentence model as matching)

        gates: dict[str, dict] = {}
        bad_map = [c.claim_id for c in claims if c.status in ("supported", "partially_supported", "contradicted", "outdated") and not c.source_ids]
        gates["claims_map_to_sources"] = {"passed": not bad_map, "detail": ", ".join(bad_map)}
        unsupported = [c.claim_id for c in claims if c.kind == "factual" and c.status in ("not_found", "contradicted")]
        gates["unsupported_claims_flagged"] = {"passed": True, "detail": f"{len(unsupported)} flagged: {', '.join(unsupported)}" if unsupported else "none"}
        noncanon = [s.source_id for s in self.ledger.ordered() if canonical_url(s.canonical_url) != s.canonical_url]
        gates["urls_canonical"] = {"passed": not noncanon, "detail": ", ".join(noncanon)}
        texts = {a["id"]: re.sub(r"\s+", " ", " ".join(a.get("paragraphs", []))).lower() for a in articles}
        quotes, bad_q = 0, []
        for key, text in tagged.items():
            for c in extract_cues(text)[1]:
                if c.kind == "article" and c.quote:
                    quotes += 1
                    q = re.sub(r"\s+", " ", c.quote.replace("'", "\"")).lower().strip(" .,")
                    src = texts.get(c.ref, "").replace("'", "\"")
                    if q not in src:
                        bad_q.append(f"{key}: article {c.ref} \"{c.quote[:50]}\"")
        gates["quotes_match_extracted_text"] = {"passed": not bad_q, "detail": f"{quotes} quote(s)" + (f"; not exact: {bad_q}" if bad_q else "")}
        bad_c = []
        for ch in charts:
            rows = dict(r for r in fred.parse_csv((self.job / ch["csv"]).read_bytes()))
            missing = [d for d, v in ch["points"] if rows.get(d) != v]
            if missing:
                bad_c.append(f"{ch['id']}: {len(missing)} value(s) not in the downloaded CSV")
        gates["chart_values_traceable"] = {"passed": not bad_c, "detail": "; ".join(bad_c) or f"{len(charts)} chart(s) checked against their CSV"}
        mixed = [s.source_id for s in self.ledger.ordered() if s.source_type == "dataset" and not s.data_period]
        gates["publication_date_vs_data_period"] = {"passed": not mixed, "detail": "datasets without a data period: " + ", ".join(mixed) if mixed else
                                                    "datasets carry data_period; documents carry published_at"}
        return gates


def run_research(inp: ResearchInputs, cfg: dict, out_root: Path, log=print, **kw) -> Path:
    return Research(inp, cfg, out_root, log, **kw).run()
