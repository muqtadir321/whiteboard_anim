"""Turn raw signals into ranked, finance-focused topic ideas.

Score = weighted mix of
  * YouTube momentum   - views per hour of recent uploads that mention the topic
  * search trend       - Google Trends "trending now" traffic
  * news momentum      - number of fresh news stories (last 7 days)
  * social discussion  - Reddit top posts this week
  * search demand      - YouTube autocomplete suggestions containing the topic
  * breadth            - how many different sources mention it
Each component is log-scaled and normalised to 0..1 across all candidates, so the
score shows relative momentum, not a promise of views.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .sources import Signal

FINANCE_TERMS = set("""
money salary salaries income tax taxes taxed irs invest investing investment investor investors stock stocks shares market markets
inflation price prices pricing cost costs costly rate rates interest mortgage mortgages rent renting loan loans debt debts credit card cards
bank banks banking savings save saving budget budgeting retire retirement pension 401k ira roth crypto bitcoin ethereum gold silver dollar
dollars rupee rupees dirham dirhams economy economic recession job jobs wage wages pay paycheck paid billionaire millionaire rich wealth
wealthy poor poverty hustle passive dividend dividends etf etfs fund funds housing home homes house houses insurance fee fees tariff tariffs
gas petrol fuel electricity bill bills layoffs layoff unemployment social security medicare refund stimulus minimum remittance remittances
freelance freelancing business startup profit profits loss earnings earn earning spending spend expensive cheap afford affordability
broke frugal fire fintech nasdaq dow s&p sp500 bonds bond treasury fed federal reserve gdp exchange currency forex property
paycheck allowance benefits overtime gratuity visa dubai uae gulf expat expats overseas abroad kuwait saudi qatar pakistan india
""".split())

STOP = set("""
a an the and or but if then than so to of in on at by for with from into over under about after before is are was were be been being
it its this that these those as you your yours we our they their he she his her i me my not no yes do does did done have has had will
would can could should may might just only also more most less least very really what which who whom whose when where why how all any
each every some such new now today week weeks year years day days time vs via per up down out off amp s t don doesn isn aren
here there get gets got make makes made one two three first last next video watch official live full episode shorts short
much many too lot lots way ways thing things need needs know want like still even big biggest really ever never why's it's
""".split())


@dataclass
class Idea:
    topic: str
    score: float
    components: dict
    sources: list[str]
    evidence: list[dict]
    keywords: list[str]
    seeds: list[str] = field(default_factory=list)
    potential: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9$%&']+", text.lower().replace("’", "'")) if t not in STOP and len(t) > 1]


def is_finance(text: str, seeds: list[str]) -> bool:
    low = text.lower()
    toks = set(tokens(text))
    return bool(toks & FINANCE_TERMS) or any(s.lower() in low for s in seeds)


def phrases(text: str, seed_tokens: set[str]) -> set[str]:
    """Candidate topic phrases (2-3 word n-grams) that contain a finance or seed word."""
    # n-grams over the real word sequence; they may contain inner small words ("cost of living")
    # but must start and end with a content word
    words = [w for w in re.findall(r"[a-z0-9$%&']+", text.lower().replace("’", "'"))]
    out = set()
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if any(e in STOP or len(e) < 2 or re.fullmatch(r"[\d$%&']+", e) for e in (gram[0], gram[-1])):
                continue
            content = [g for g in gram if g not in STOP]
            if any(g in FINANCE_TERMS or g in seed_tokens for g in content) and not all(g.isdigit() for g in content):
                out.add(" ".join(gram))
    return out


def _texts(sig: Signal) -> str:
    extra = " ".join(sig.extra.get("news", []) or [])
    return f"{sig.title} {extra}"


def rank_topics(signals: list[Signal], autocomplete: dict[str, list[str]], seeds: list[str], top_n: int = 10,
                now: dt.datetime | None = None) -> list[Idea]:
    now = now or dt.datetime.now(dt.timezone.utc)
    seed_tokens = {t for s in seeds for t in tokens(s)}
    relevant = [s for s in signals if is_finance(_texts(s), seeds)]
    comp: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    ev: dict[str, list[tuple[float, Signal]]] = defaultdict(list)
    srcs: dict[str, set] = defaultdict(set)
    trend_titles: set[str] = set()
    for s in relevant:
        age = s.age_hours(now)
        if s.source in ("youtube", "youtube_api"):
            if age is None or age > 24 * 21:
                continue
            impact = s.views / age  # views per hour
            key = "youtube"
        elif s.source == "google_trends":
            impact = max(1.0, s.traffic)
            key = "trends"
        elif s.source == "google_news":
            impact = 1.0 if (age is None or age < 24 * 7) else 0.3
            key = "news"
        elif s.source == "reddit":
            impact = 1.0
            key = "social"
        else:
            continue
        cand = phrases(_texts(s), seed_tokens)
        if s.source == "google_trends" and tokens(s.title):
            cand.add(" ".join(tokens(s.title)))  # the trending search itself is a topic
            trend_titles.add(" ".join(tokens(s.title)))
        for ph in cand:
            comp[ph][key] += impact
            srcs[ph].add(key)
            ev[ph].append((impact, s))
    all_sugg = [q.lower() for v in autocomplete.values() for q in v]
    for ph in comp:
        toks = [t for t in ph.split() if t not in STOP]
        comp[ph]["demand"] = sum(1 for q in all_sugg if all(t in q for t in toks))
        if comp[ph]["demand"]:
            srcs[ph].add("search")
    # A topic must be a real trending search or recur in at least two separate items
    # (a phrase found in one headline only is a fragment, not a trend), and needs evidence
    # from two sources or from YouTube/Trends.
    def recurs(p):
        return p in trend_titles or len({s.url or s.title for _, s in ev[p]}) >= 2

    cands = [p for p in comp if recurs(p) and (len(srcs[p]) >= 2 or comp[p]["youtube"] > 0 or comp[p]["trends"] > 0)]
    weights = {"youtube": 0.35, "trends": 0.2, "news": 0.15, "social": 0.1, "demand": 0.15}
    maxima = {k: max((math.log1p(comp[p][k]) for p in cands), default=1.0) or 1.0 for k in weights}
    scored = []
    for p in cands:
        parts = {k: (math.log1p(comp[p][k]) / maxima[k]) for k in weights}
        breadth = len(srcs[p]) / 5.0
        score = sum(weights[k] * parts[k] for k in weights) + 0.05 * breadth
        scored.append((score, p, parts))
    scored.sort(reverse=True)
    # de-duplicate overlapping phrases ("dubai salary", "salary dubai jobs")
    chosen: list[tuple[float, str, dict]] = []
    for sc, p, parts in scored:
        pt = set(p.split()) - STOP
        if any(len(pt & (set(q.split()) - STOP)) / max(1, len(pt | (set(q.split()) - STOP))) >= 0.5 for _, q, _ in chosen):
            continue
        chosen.append((sc, p, parts))
        if len(chosen) >= top_n:
            break
    ideas = []
    best = chosen[0][0] if chosen else 1.0
    for rank, (sc, p, parts) in enumerate(chosen):
        items = sorted(ev[p], key=lambda x: -x[0])
        seen, evidence = set(), []
        for impact, s in items:
            if s.url in seen:
                continue
            seen.add(s.url)
            age = s.age_hours(now)
            evidence.append({"source": s.source, "title": s.title, "url": s.url, "region": s.region,
                             "views": int(s.views) or None, "views_per_hour": round(s.views / age, 1) if s.views and age else None,
                             "traffic": int(s.traffic) or None, "channel": s.extra.get("channel") or s.extra.get("subreddit")})
            if len(evidence) >= 6:
                break
        toks = [t for t in p.split() if t not in STOP]
        kw = [q for q in dict.fromkeys(all_sugg) if any(t in q for t in toks)][:15]
        rel = sc / best if best else 0
        potential = "High" if rel >= 0.75 else "Medium" if rel >= 0.45 else "Emerging"
        ideas.append(Idea(p, round(sc, 3), {k: round(v, 2) for k, v in parts.items()}, sorted(srcs[p]), evidence, kw,
                          [s for s in seeds if any(t in s for t in toks)], potential))
    return ideas
