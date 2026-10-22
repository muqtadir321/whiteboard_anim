"""Narration claims and how far the collected sources support them.

A claim is one sentence of the supplied script. It is classified first (factual, prediction,
opinion, question, narrative); only factual sentences are checked. Matching is conservative:

* every key number must appear in one source sentence (same value at the claim's precision)
  next to enough of the claim's content words  -> supported
* some of the numbers, or all numbers with too little shared wording       -> partially_supported
* the same kind of figure, close wording, a different value                -> contradicted (check it)
* supported only by a source older than research.claims.outdated_after_days
  while the claim says "now/today/this year"                                -> outdated
* nothing comparable                                                        -> not_found

Shared keywords alone never make a claim "supported". Claims without numbers can only be
"supported" when a meaning model (semantic.py) is available and agrees strongly; otherwise
they stay "partially_supported" with the uncertainty noted. Urdu / Roman Urdu claims are
compared with (mostly English) sources through their numbers and, when available, meaning.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from ..numbers import extract_quantities
from ..script_analyzer import analyze_script
from .extract import sentences as split_sentences

STATUSES = ("supported", "partially_supported", "contradicted", "outdated", "not_found", "opinion")
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been being it its this that these those
there their they them he she his her we our you your i me my not no so than then too very can could will would should may might
just about into over under after before more most less also has have had do does did what which who whom how why when where
all any some each other such only own same up down out off again further once here both few many much said says
percent per cent percentage million billion trillion thousand dollars dollar rupees""".split())
OPINION = re.compile(r"\b(i think|i believe|i feel|in my (?:view|opinion)|we think|should|must|ought to|best|worst|amazing|terrible|"
                     r"meri raye|mere khayal|میرے خیال|میری رائے|چاہیے|chahiye)\b", re.I)
PREDICTION = re.compile(r"\b(will|won't|going to|could|might|expected to|expects?|forecasts?|predicts?|projected|likely to|"
                        r"ga|gi|ge)\b|(?:گا|گی|گے)(?:\s|۔|$)|(?-i:\bmay\b)", re.I)  # lower-case "may" only: "May" is the month
ATTRIBUTED = re.compile(r"\b(according to|said|says|reported|reports|estimates?|data|survey|study|report|shows?|found)\b|کے مطابق|mutabiq", re.I)
NOW_WORDS = re.compile(r"\b(now|today|currently|right now|this year|this month|this week|latest|abhi|aaj|aajkal)\b|ابھی|آج|اس سال", re.I)
WORD = re.compile(r"[a-z][a-z'-]+")


@dataclass
class Evidence:
    source_id: str
    excerpt: str
    numbers_matched: list[str] = field(default_factory=list)
    word_overlap: float = 0.0
    semantic: float | None = None


@dataclass
class Claim:
    claim_id: str
    language: str
    sentence_index: int
    paragraph: int
    text: str
    kind: str  # factual | prediction | opinion | question | narrative
    numbers: list[str] = field(default_factory=list)
    status: str = "not_found"
    source_ids: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    independent_sources: int = 0
    primary_source: bool = False
    confidence: str = "low"
    notes: list[str] = field(default_factory=list)
    finance: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Doc:
    """A source's extracted text, split into sentences once."""
    source_id: str
    text: str
    source_type: str = "news"
    domain: str = ""
    published_at: str = ""
    duplicate_of: str = ""
    sents: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.sents:
            self.sents = [s for s in split_sentences(re.sub(r"\s+", " ", self.text)) if len(s) > 12][:600]


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    return w[:6]


def content_words(text: str) -> set[str]:
    """Content words, plural folded and cut to 6 letters ("rates" = "rate", "housing" = "housin")."""
    return {_stem(w) for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 2}


def key_numbers(text: str, lang: str) -> list[tuple[float, str, str]]:
    """(value, kind, display) for figures worth checking: years only count when nothing else is there."""
    qs = extract_quantities(text, lang)
    figs = [(q.value, q.kind, q.display) for q in qs if q.kind != "year"]
    return figs or [(q.value, q.kind, q.display) for q in qs if q.kind == "year"]


def _variants(value: float) -> set[float]:
    """The value and the same amount written with a scale word (51 lakh -> 5,100,000 and 51)."""
    out = {round(value, 4)}
    for scale in (1e3, 1e5, 1e6, 1e7, 1e9, 1e12):
        if abs(value) >= scale and abs(value / scale - round(value / scale, 3)) < 1e-9:
            out.add(round(value / scale, 4))
    return out


def same_value(a: float, b: float) -> bool:
    """Equal at the precision the narration gives (rounding allowed, unit scale words ignored)."""
    for x in _variants(a):
        for y in _variants(b):
            if x == y:
                return True
            decimals = len(f"{x}".split(".")[1].rstrip("0")) if "." in f"{x}" else 0
            if abs(x - y) <= 0.5 * 10 ** (-decimals) + 1e-9 and abs(x) >= 1:
                return True
    return False


def distinctive(value: float) -> bool:
    """Small whole numbers (1-10) match too much by chance to count on their own."""
    return not (float(value).is_integer() and abs(value) <= 10)


def classify(text: str) -> str:
    t = text.strip()
    if t.endswith(("?", "؟")):
        return "question"
    if OPINION.search(t):
        return "opinion"
    has_fig = bool(re.search(r"\d", t)) or bool(extract_quantities(t, "en"))
    if PREDICTION.search(t) and not (ATTRIBUTED.search(t) and has_fig):
        return "prediction"
    names = any(w[:1].isupper() and w[1:2].islower() for w in t.split()[1:])  # a named person, company, place
    if has_fig or ATTRIBUTED.search(t) or names:
        return "factual"
    return "narrative"


def paragraph_of(text: str, offset: int) -> int:
    """Index of the blank-line separated paragraph (the scene group) that holds `offset`."""
    return sum(1 for m in re.finditer(r"\n[ \t]*\n\s*", text.strip("\n") + "\n") if m.end() <= offset)


def extract_claims(text: str, lang: str, prefix: str) -> list[Claim]:
    out = []
    body = text.strip("\n")
    for s in analyze_script(text, lang):
        kind = classify(s.text)
        nums = [d for _, _, d in key_numbers(s.text, lang)]
        pos = body.find(s.text)
        out.append(Claim(f"{prefix}-{s.index + 1:03d}", lang, s.index, paragraph_of(body, pos if pos >= 0 else s.start), s.text, kind, nums,
                         status="not_found" if kind == "factual" else "opinion"))
    return out


def _date(s: str) -> dt.date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None


def match_claims(claims: list[Claim], docs: list[Doc], min_overlap: float = 0.5, outdated_days: int = 400, emb=None,
                 today: dt.date | None = None) -> None:
    """Fill status, evidence and source ids in place."""
    today = today or dt.date.today()
    doc_figs = {(d.source_id, i): [(v, k) for v, k, _ in key_numbers(s, "en")] for d in docs for i, s in enumerate(d.sents)}
    by_id = {d.source_id: d for d in docs}
    for c in claims:
        if c.kind != "factual":
            c.notes.append(f"{c.kind}: not checked as a fact")
            continue
        figs = key_numbers(c.text, c.language)
        words = content_words(c.text) if c.language == "en" else set()
        hits: list[tuple[int, float, Doc, int, list[str]]] = []  # (numbers matched, overlap, doc, sentence, displays)
        conflicts: list[tuple[Doc, int]] = []
        for d in docs:
            for i, s in enumerate(d.sents):
                sf = doc_figs[(d.source_id, i)]
                found = [(v, disp) for v, k, disp in figs if any(same_value(v, sv) for sv, _ in sf)]
                matched = [disp for _, disp in found]
                ctx = " ".join(d.sents[max(0, i - 1):i + 2])
                ov = len(words & content_words(ctx)) / len(words) if words else 0.0
                if found and (ov >= 0.2 or any(distinctive(v) for v, _ in found)):
                    hits.append((len(matched), ov, d, i, matched))
                elif figs and ov >= max(0.6, min_overlap) and any(k == fk for _, fk, _ in figs for _, k in sf):
                    conflicts.append((d, i))
        sem: dict[tuple[str, int], float] = {}
        if emb is not None and docs:
            try:
                cand = [(d, i) for d in docs for i in range(len(d.sents))]
                vecs = emb.embed([c.text] + [d.sents[i] for d, i in cand])
                sims = vecs[1:] @ vecs[0]
                sem = {(d.source_id, i): float(s) for (d, i), s in zip(cand, sims)}
            except Exception as exc:
                c.notes.append(f"meaning model failed ({type(exc).__name__}); lexical matching only")
        need = len(figs)
        full = [h for h in hits if h[0] == need and need > 0 and (h[1] >= min_overlap * 0.6 or c.language != "en")]
        part = [h for h in hits if h not in full]
        status = "not_found"
        chosen: list[tuple] = []
        if full:
            chosen = sorted(full, key=lambda h: -h[1])
            cross = c.language != "en"
            strong_meaning = any(sem.get((h[2].source_id, h[3]), 0) >= 0.75 for h in chosen)
            if cross and not strong_meaning:
                status = "partially_supported"
                c.notes.append("numbers found in an English source; meaning not verified across languages")
            else:
                status = "supported"
        elif part:
            chosen = sorted(part, key=lambda h: (-h[0], -h[1]))[:3]
            status = "partially_supported"
            c.notes.append("only some figures or little shared wording found")
        elif not figs:
            best = []
            for d in docs:
                for i, s in enumerate(d.sents):
                    ov = len(words & content_words(s)) / len(words) if words else 0.0
                    m = sem.get((d.source_id, i))
                    if ov >= min_overlap or (m is not None and m >= 0.7):
                        best.append((0, ov, d, i, []))
            chosen = sorted(best, key=lambda h: -(sem.get((h[2].source_id, h[3]), 0) + h[1]))[:3]
            if chosen:
                top = chosen[0]
                m = sem.get((top[2].source_id, top[3]))
                if m is not None and m >= 0.8 and top[1] >= min_overlap:
                    status = "supported"
                else:
                    status = "partially_supported"
                    c.notes.append("matched by wording only (keyword overlap is not verification)")
        if status == "not_found" and conflicts:
            status = "contradicted"
            d, i = conflicts[0]
            chosen = [(0, 0.0, d, i, [])]
            c.notes.append("a source states a different figure in similar wording: check before publishing")
        for n, ov, d, i, matched in chosen[:5]:
            c.evidence.append(asdict(Evidence(d.source_id, d.sents[i][:400], matched, round(ov, 2),
                                              round(sem[(d.source_id, i)], 3) if (d.source_id, i) in sem else None)))
        ids = list(dict.fromkeys(e["source_id"] for e in c.evidence))
        c.source_ids = ids
        originals = {by_id[s].duplicate_of or s for s in ids}
        c.independent_sources = len({by_id[s].domain for s in originals if s in by_id} or originals)
        c.primary_source = any(by_id[s].source_type in ("official", "primary_filing", "issuer_release", "dataset") for s in ids if s in by_id)
        if status == "supported" and NOW_WORDS.search(c.text):
            dates = [_date(by_id[s].published_at) for s in ids if s in by_id]
            dates = [x for x in dates if x]
            if dates and (today - max(dates)).days > outdated_days:
                status = "outdated"
                c.notes.append(f"newest supporting source is from {max(dates)}")
        c.status = status
        c.confidence = ("high" if status == "supported" and c.primary_source and c.independent_sources >= 2 else
                        "medium" if status == "supported" else "low")
        if status == "supported" and c.independent_sources < 2:
            c.notes.append("only one independent source")


def report_markdown(claims: list[Claim], ledger) -> str:
    counts = {s: sum(1 for c in claims if c.status == s) for s in STATUSES}
    lines = ["# Claim report", "", "Statuses: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v), "",
             "Matching is conservative and automatic. A person must still read the sources before publishing.", ""]
    for c in claims:
        lines.append(f"## {c.claim_id} — {c.status}" + (f" ({c.kind})" if c.kind != "factual" else ""))
        lines.append(f"> {c.text}")
        for e in c.evidence:
            src = ledger.get(e["source_id"]) if ledger else None
            name = f"{src.publisher or src.domain}: {src.title}" if src else e["source_id"]
            lines.append(f"- [{e['source_id']}] {name} — “{e['excerpt'][:240]}”")
        for n in c.notes:
            lines.append(f"- note: {n}")
        lines.append("")
    return "\n".join(lines)
