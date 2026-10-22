"""Financial claims: company figures in the narration checked against filings, releases and news.

For each finance sentence: company (ticker/CIK), metric, value with currency/scale/unit, period
(fiscal year/quarter), comparison (YoY/QoQ) and basis (GAAP or adjusted) are extracted, then
compared with SEC XBRL company facts (GAAP, as filed), the issuer's earnings release (8-K EX-99)
and any news text collected, after normalising units but keeping the originals.

Match statuses: exact_match, normalized_match, partial_match, conflict, not_found. Restatements,
GAAP vs non-GAAP, percent vs percentage points, quarterly vs annual and calendar vs fiscal years
are flagged, never resolved silently. Derived values (growth, margins, Q4 = FY - Q1..Q3) record
their formula and inputs. The script is never changed; a correction is only proposed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MATCH_STATUSES = ("exact_match", "normalized_match", "partial_match", "conflict", "not_found")
SCALES = {"trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "mm": 1e6,
          "thousand": 1e3, "k": 1e3}
CURRENCY = {"$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "rs": "PKR/INR", "₹": "INR"}
# metric -> words in narration, XBRL tags (us-gaap unless "dei:"), unit, or a derivation
METRICS: dict[str, dict] = {
    "revenue": {"words": ("total revenue", "net revenue", "revenues", "revenue", "net sales", "total sales", "sales", "top line"),
                "tags": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                         "RevenueFromContractWithCustomerIncludingAssessedTax"), "unit": "USD"},
    "net_income": {"words": ("net income", "net profit", "net loss", "net earnings", "profit", "loss"),
                   "tags": ("NetIncomeLoss", "ProfitLoss"), "unit": "USD"},
    "operating_income": {"words": ("operating income", "operating profit", "operating loss", "income from operations"),
                         "tags": ("OperatingIncomeLoss",), "unit": "USD"},
    "gross_profit": {"words": ("gross profit",), "tags": ("GrossProfit",), "unit": "USD"},
    "eps_diluted": {"words": ("diluted earnings per share", "diluted eps", "earnings per share", "eps"),
                    "tags": ("EarningsPerShareDiluted",), "unit": "USD/shares"},
    "eps_basic": {"words": ("basic earnings per share", "basic eps"), "tags": ("EarningsPerShareBasic",), "unit": "USD/shares"},
    "operating_cash_flow": {"words": ("operating cash flow", "cash flow from operations", "cash from operations", "cash provided by operating activities"),
                            "tags": ("NetCashProvidedByUsedInOperatingActivities",), "unit": "USD"},
    "capex": {"words": ("capital expenditures", "capital expenditure", "capex"), "tags": ("PaymentsToAcquirePropertyPlantAndEquipment",), "unit": "USD"},
    "free_cash_flow": {"words": ("free cash flow",), "derived": ("operating_cash_flow", "-", "capex"), "unit": "USD", "non_gaap": True},
    "cash": {"words": ("cash and cash equivalents", "cash on hand", "cash pile"), "tags": ("CashAndCashEquivalentsAtCarryingValue",), "unit": "USD", "instant": True},
    "long_term_debt": {"words": ("long-term debt", "long term debt"), "tags": ("LongTermDebt", "LongTermDebtNoncurrent"), "unit": "USD", "instant": True},
    "gross_margin": {"words": ("gross margin",), "derived": ("gross_profit", "/", "revenue"), "unit": "percent"},
    "operating_margin": {"words": ("operating margin",), "derived": ("operating_income", "/", "revenue"), "unit": "percent"},
    "net_margin": {"words": ("net margin", "profit margin"), "derived": ("net_income", "/", "revenue"), "unit": "percent"},
    "shares_outstanding": {"words": ("shares outstanding",), "tags": ("dei:EntityCommonStockSharesOutstanding",), "unit": "shares", "instant": True},
    "market_cap": {"words": ("market capitalization", "market capitalisation", "market cap", "market value"), "market_data": True, "unit": "USD"},
}
FINANCE_HINT = re.compile(r"\b(revenue|sales|profit|loss|earnings|eps|per share|margin|guidance|net income|cash flow|debt|market cap|"
                          r"quarter|fiscal|shares|dividend|stock|nasdaq|nyse|10-k|10-q|8-k)\b", re.I)
NON_GAAP = re.compile(r"\b(adjusted|non-gaap|non gaap|excluding|ex-items|core|underlying|pro forma|organic)\b", re.I)
GAAP = re.compile(r"\bGAAP\b|\bas reported\b", re.I)  # "the company reported X" says nothing about the basis
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


# -- parsing ----------------------------------------------------------------------------
@dataclass
class Amount:
    value: float  # in base units (USD, percent, percentage points, shares)
    unit: str  # USD | USD/shares | percent | pp | shares | number
    currency: str
    scale: float
    raw: str
    decimals: int
    pos: int = 0  # character offset in the sentence

    def tolerance(self) -> float:
        """Half a unit of the last digit the narration states (in base units)."""
        return 0.5 * 10 ** (-self.decimals) * self.scale


AMOUNT_RE = re.compile(
    r"(?P<cur>US\$|\$|€|£|₹|\bUSD\s?|\bRs\.?\s?)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
    r"(?P<scale>trillion|billion|million|thousand|tn|bn|mn|mm|[bmk])?\b\s?(?P<pct>%|percent\b|per cent\b)?\s?"
    r"(?P<pp>percentage points?|pp\b|(?P<bp>basis points?|bps?\b))?(?P<cents>\s?cents?\b)?", re.I)


def parse_amounts(text: str) -> list[Amount]:
    out = []
    for m in AMOUNT_RE.finditer(text):
        num = m["num"].replace(",", "")
        if re.fullmatch(r"(19|20)\d\d", num) and not (m["cur"] or m["scale"] or m["pct"]):
            continue  # a year
        decimals = len(num.split(".")[1]) if "." in num else 0
        scale_w = (m["scale"] or "").lower()
        scale = SCALES.get(scale_w, 1.0)
        v = float(num) * scale
        cur = CURRENCY.get((m["cur"] or "").strip().lower().rstrip("."), "")
        if m["pp"]:
            unit = "pp"
            if m["bp"]:  # 25 basis points = 0.25 percentage points
                v, scale = v / 100, 0.01
        elif m["pct"]:
            unit = "percent"
        elif m["cents"]:
            v, scale, unit, cur = v / 100, 0.01, "USD/shares" if "share" in text[m.end():m.end() + 20].lower() else "USD", "USD"
        elif cur:
            after = text[m.end():m.end() + 24].lower()
            unit = f"{cur}/shares" if ("per share" in after or "a share" in after) else cur
        elif scale_w in ("million", "billion", "thousand", "trillion") and re.match(r"\s*(?:shares|units)", text[m.end():].lower()):
            unit = "shares"
        else:
            unit = "number"
        out.append(Amount(round(v, 6), unit, cur, scale, m.group(0).strip(), decimals, m.start()))
    return out


@dataclass
class Period:
    fy: int | None = None
    fp: str | None = None  # FY | Q1..Q4
    fiscal: bool = False  # the narration says "fiscal"
    raw: str = ""


def parse_period(text: str) -> Period:
    t = text
    m = re.search(r"\b(?:Q([1-4])|([1-4])Q)\s*(?:of\s+)?(?:FY|fiscal(?:\s+year)?)?\s*'?(\d{4}|\d{2})\b", t, re.I)
    if m:
        y = int(m[3])
        return Period(y + 2000 if y < 100 else y, f"Q{m[1] or m[2]}", "fiscal" in m[0].lower() or "fy" in m[0].lower(), m[0])
    m = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter(?:\s+of)?(\s+fiscal(?:\s+year)?)?\s*(?:of\s+)?'?(\d{4})\b", t, re.I)
    if m:
        return Period(int(m[3]), f"Q{ORD[m[1].lower()]}", bool(m[2]), m[0])
    m = re.search(r"\b(?:fiscal(?:\s+year)?|FY)\s*'?(\d{4}|\d{2})\b", t, re.I)
    if m:
        y = int(m[1])
        return Period(y + 2000 if y < 100 else y, "FY", True, m[0])
    m = re.search(r"\b(?:in|for|during|of)\s+(?:the\s+)?(?:full[- ]year\s+|calendar\s+(?:year\s+)?)?((?:19|20)\d\d)\b", t, re.I)
    if m:
        return Period(int(m[1]), "FY", False, m[0])
    return Period()


def metric_mentions(text: str) -> list[tuple[int, str]]:
    """(offset, metric) for each metric named; the longest wording wins where they overlap."""
    low = text.lower()
    found: list[tuple[int, int, str]] = []
    for name, spec in METRICS.items():
        for w in spec["words"]:
            for m in re.finditer(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low):
                found.append((m.start(), m.end(), name))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out, last_end = [], -1
    for a, b, name in found:
        if a >= last_end:
            out.append((a, name))
            last_end = b
    return out


def parse_metric(text: str) -> str | None:
    mentions = metric_mentions(text)
    return mentions[0][1] if mentions else None


def parse_comparison(text: str) -> str | None:
    low = text.lower()
    if re.search(r"year[- ]over[- ]year|\byoy\b|from a year (?:earlier|ago)|than a year (?:earlier|ago)|compared (?:with|to) (?:the )?(?:same|last|prior) (?:quarter|period) (?:of )?(?:last|the prior) year|annual(?:ly)? (?:growth|increase)", low):
        return "yoy"
    if re.search(r"quarter[- ]over[- ]quarter|\bqoq\b|sequential(?:ly)?|from the (?:previous|prior) quarter", low):
        return "qoq"
    if re.search(r"\b(rose|grew|increased|jumped|climbed|fell|dropped|declined|decreased|slid|surged|up|down)\b[^.]{0,30}\d", low):
        return "change"  # growth stated without a baseline
    return None


def is_financial(text: str) -> bool:
    return bool(FINANCE_HINT.search(text)) and bool(parse_metric(text))


# -- XBRL facts -------------------------------------------------------------------------
@dataclass
class Fact:
    tag: str
    value: float
    unit: str
    start: str
    end: str
    fy: int | None
    fp: str
    form: str
    accn: str
    filed: str
    label: str = ""  # FY2024 / Q3 FY2024 after labelling
    restated_from: list = field(default_factory=list)  # earlier filed values for the same period
    derived: str = ""  # formula when computed


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def facts_for(companyfacts: dict, tag: str, unit: str) -> list[Fact]:
    ns, _, name = tag.rpartition(":")
    block = companyfacts.get("facts", {}).get(ns or "us-gaap", {}).get(name)
    if not block:
        return []
    units = block.get("units", {})
    rows = units.get(unit) or (units.get("USD") if unit == "USD" else None) or []
    return [Fact(tag, float(r["val"]), unit, r.get("start", ""), r["end"], r.get("fy"), r.get("fp", ""), r.get("form", ""), r.get("accn", ""),
                 r.get("filed", "")) for r in rows if r.get("val") is not None]


def label_periods(facts: list[Fact], instant: bool = False) -> dict[tuple[int, str], Fact]:
    """(fiscal year, FY|Q1..Q4) -> fact, from the filings that report each period as their current one.
    Later filings of the same period replace earlier ones; the earlier values are kept as restatement history."""
    by_accn: dict[str, str] = {}
    for f in facts:
        by_accn[f.accn] = max(by_accn.get(f.accn, ""), f.end)
    periods: dict[tuple[str, str], list[Fact]] = {}
    for f in facts:
        periods.setdefault((f.start, f.end), []).append(f)
    out: dict[tuple[int, str], Fact] = {}
    for (start, end), group in periods.items():
        group.sort(key=lambda f: (f.filed, f.accn))
        current = [f for f in group if f.end == by_accn[f.accn] and f.fy and f.fp in ("FY", "Q1", "Q2", "Q3", "Q4")]
        if not current:
            continue
        own = current[0]  # the filing that first reported this period
        if instant:
            key = (own.fy, own.fp)
        else:
            d = _days(start, end) if start else 0
            if own.fp == "FY" and 340 <= d <= 380:
                key = (own.fy, "FY")
            elif own.fp in ("Q1", "Q2", "Q3", "Q4") and 75 <= d <= 105:
                key = (own.fy, own.fp)
            else:
                continue  # year-to-date values
        latest = group[-1]
        vals = []
        for f in group:
            if f.value != latest.value and (f.filed, f.value) not in vals:
                vals.append((f.filed, f.value))
        fact = Fact(**{**asdict(latest), "restated_from": [{"filed": a, "value": b} for a, b in vals], "label": f"{key[1]} FY{key[0]}"})
        if key not in out or fact.filed > out[key].filed:
            out[key] = fact
    for fy in {k[0] for k in out}:  # Q4 is rarely filed on its own: FY - (Q1 + Q2 + Q3)
        if (fy, "Q4") not in out and all((fy, q) in out for q in ("FY", "Q1", "Q2", "Q3")) and not instant:
            fyf = out[(fy, "FY")]
            q = [out[(fy, x)] for x in ("Q1", "Q2", "Q3")]
            out[(fy, "Q4")] = Fact(fyf.tag, round(fyf.value - sum(x.value for x in q), 6), fyf.unit, q[2].end, fyf.end, fy, "Q4", fyf.form,
                                   fyf.accn, fyf.filed, f"Q4 FY{fy}", derived=f"FY{fy} - (Q1 + Q2 + Q3) = {fyf.value} - ({' + '.join(str(x.value) for x in q)})")
    return out


def metric_series(companyfacts: dict, metric: str) -> dict[tuple[int, str], Fact]:
    spec = METRICS[metric]
    if "derived" in spec:
        a, op, b = spec["derived"]
        sa, sb = metric_series(companyfacts, a), metric_series(companyfacts, b)
        out = {}
        for k in sa.keys() & sb.keys():
            x, y = sa[k], sb[k]
            if op == "-":
                v, formula = x.value - y.value, f"{a} - {b} = {x.value} - {y.value}"
            else:
                if not y.value:
                    continue
                v, formula = 100 * x.value / y.value, f"100 * {a} / {b} = 100 * {x.value} / {y.value}"
            out[k] = Fact(f"derived:{metric}", round(v, 6), spec["unit"], x.start, x.end, x.fy, x.fp, x.form, x.accn, max(x.filed, y.filed),
                          x.label, derived=formula)
        return out
    unit = {"USD": "USD", "USD/shares": "USD/shares", "shares": "shares"}.get(spec["unit"], "USD")
    for tag in spec.get("tags", ()):
        series = label_periods(facts_for(companyfacts, tag, unit), instant=bool(spec.get("instant")))
        if series:
            return series
    return {}


def growth(series: dict, key: tuple[int, str], kind: str) -> Fact | None:
    fy, fp = key
    prior = (fy - 1, fp) if kind == "yoy" else ((fy, f"Q{int(fp[1]) - 1}") if fp.startswith("Q") and fp != "Q1" else (fy - 1, "Q4"))
    if key not in series or prior not in series or not series[prior].value:
        return None
    cur, old = series[key], series[prior]
    v = 100 * (cur.value - old.value) / abs(old.value)
    return Fact(cur.tag, round(v, 4), "percent", cur.start, cur.end, fy, fp, cur.form, cur.accn, cur.filed, cur.label,
                derived=f"100 * ({cur.label} - {old.label}) / |{old.label}| = 100 * ({cur.value} - {old.value}) / {abs(old.value)}")


# -- claims -----------------------------------------------------------------------------
@dataclass
class FinancialClaim:
    claim_id: str
    text: str
    language: str
    metric: str
    amount: dict | None
    period: dict
    comparison: str | None
    basis: str  # gaap | non_gaap | unknown
    company: str = ""
    ticker: str = ""
    cik: int | None = None
    status: str = "not_found"
    matches: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    sources_found: int = 0
    distinct_publishers: int = 0
    limitations: list[str] = field(default_factory=list)
    proposed_correction: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _clause(text: str, pos: int) -> str:
    """The clause around a figure: between the nearest commas/semicolons/"and"."""
    starts = [m.end() for m in re.finditer(r"[,;]|\band\b|\bwhile\b|\bbut\b", text[:pos])]
    a = starts[-1] if starts else 0
    m = re.search(r"[,;]|\band\b|\bwhile\b|\bbut\b", text[pos:])
    b = pos + m.start() if m else len(text)
    return text[a:b]


def parse_claims(claim_id: str, text: str, language: str = "en") -> list[FinancialClaim]:
    """One financial claim per figure (atomic claims): "revenue of $35.1 billion, up 94% year over year"
    gives a level claim and a growth claim. Each figure takes the nearest metric named before it."""
    mentions = metric_mentions(text)
    if not mentions:
        return []
    amounts = [a for a in parse_amounts(text) if a.unit != "number" or a.scale > 1]
    period = asdict(parse_period(text))
    basis_all = "non_gaap" if NON_GAAP.search(text) else ("gaap" if GAAP.search(text) else "unknown")
    out = []
    for k, a in enumerate(amounts):
        before = [m for m in mentions if m[0] <= a.pos]
        metric = (before[-1] if before else mentions[0])[1]
        spec = METRICS[metric]
        clause = _clause(text, a.pos)
        comp = parse_comparison(clause) if a.unit in ("percent", "pp") and spec["unit"] != "percent" else None
        if a.unit in ("percent", "pp") and spec["unit"] != "percent" and comp is None:
            comp = "change"  # "revenue up 94%": growth with no stated baseline
        if spec["unit"] == "percent" and a.unit in ("percent", "pp"):
            comp = parse_comparison(clause) if re.search(r"\b(rose|fell|up|down|expanded|narrowed|widened|improved)\b", clause, re.I) else None
        if spec["unit"] == "USD/shares" and a.unit == "USD":
            a.unit = "USD/shares"  # "EPS was $1.24"
        basis = "non_gaap" if spec.get("non_gaap") else basis_all
        cid = claim_id if len(amounts) == 1 else f"{claim_id}{'abcdefghij'[k] if k < 10 else k}"
        out.append(FinancialClaim(cid, text, language, metric, asdict(a), period, comp, basis))
    return out


def parse_claim(claim_id: str, text: str, language: str = "en") -> FinancialClaim | None:
    claims = parse_claims(claim_id, text, language)
    return claims[0] if claims else None


def _amount(fc: FinancialClaim) -> Amount | None:
    return Amount(**fc.amount) if fc.amount else None


def compare_value(a: Amount, value: float, unit: str) -> str:
    """exact_match (same number as written), normalized_match (equal after unit/scale), or conflict."""
    if a.unit == "pp" and unit == "percent" or a.unit == "percent" and unit == "pp":
        return "conflict"
    tol = max(a.tolerance(), abs(a.value) * 1e-9)
    if abs(a.value - value) <= tol + 1e-9:
        written = a.raw.replace(",", "")
        exact = a.scale == 1 and re.search(re.escape(f"{value:g}"), written) is not None
        return "exact_match" if exact else "normalized_match"
    return "conflict"


def verify_with_facts(fc: FinancialClaim, companyfacts: dict, source_id: str, source_url: str, entity: str) -> None:
    a = _amount(fc)
    spec = METRICS[fc.metric]
    if spec.get("market_data"):
        fc.limitations.append("market capitalisation is market data, not in filings; needs a dated market-data source")
        return
    series = metric_series(companyfacts, fc.metric)
    if not series:
        fc.limitations.append(f"no XBRL facts for {fc.metric} in {entity}'s filings")
        return
    p = fc.period
    keys = sorted(series, key=lambda k: (k[0], {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}.get(k[1], 0)))
    if p["fy"] and p["fp"]:
        key = (p["fy"], p["fp"])
        if key not in series:
            fc.matches.append({"source_id": source_id, "status": "not_found", "note": f"no {p['fp']} FY{p['fy']} value in XBRL"})
            return
        flags = []
        if not p["fiscal"]:
            fy_end = series[key].end
            if p["fp"] == "FY" and not fy_end.endswith("-12-31"):
                flags.append("calendar_vs_fiscal: narration names a year; the company's fiscal year ends " + fy_end)
    else:
        key = keys[-1] if not spec.get("instant") or True else keys[-1]
        flags = ["period_not_stated: compared with the latest reported period"]
    fact = series[key]
    target = fact
    if fc.comparison in ("yoy", "qoq") and a is not None and a.unit in ("percent", "pp"):
        g = growth(series, key, fc.comparison)
        if g is None:
            fc.matches.append({"source_id": source_id, "status": "not_found", "note": f"no prior period for {fc.comparison} growth"})
            return
        target = g
    elif fc.comparison == "change" and a is not None and a.unit in ("percent", "pp") and spec["unit"] != "percent":
        g = growth(series, key, "yoy")
        if g is None:
            fc.matches.append({"source_id": source_id, "status": "not_found", "note": "growth stated without a baseline; no prior year to compare"})
            return
        target = g
        flags.append("baseline_not_stated: compared year over year")
    status = "not_found" if a is None else compare_value(a, target.value, target.unit)
    if a is not None and a.unit in ("percent", "pp") and target.unit == "percent" and spec["unit"] == "percent" and fc.comparison:
        flags.append("percent_vs_points: a margin change can be stated in percent or percentage points; check which")
    if fc.basis == "non_gaap":
        flags.append("basis_differs: narration is adjusted/non-GAAP, XBRL facts are GAAP")
        if status == "conflict":
            status = "partial_match"
    if fact.restated_from:
        old = [r["value"] for r in fact.restated_from]
        flags.append(f"restated: earlier filed value(s) {old}, latest {fact.value}")
        if a is not None and any(compare_value(a, v, fact.unit) != "conflict" for v in old) and status == "conflict":
            status = "conflict"
            flags.append("matches the originally reported value, not the restated one")
    if flags and status in ("exact_match", "normalized_match") and any(f.startswith(("period_not_stated", "calendar_vs_fiscal", "baseline_not_stated")) for f in flags):
        status = "partial_match"
    fc.matches.append({"source_id": source_id, "source_url": source_url, "document": f"SEC XBRL company facts ({entity})", "form": fact.form,
                       "accession": fact.accn, "filed": fact.filed, "metric_label": fact.tag, "fiscal_period": fact.label,
                       "period_start": fact.start, "period_end": fact.end, "reported_value": fact.value, "unit": fact.unit,
                       "compared_value": target.value, "compared_unit": target.unit, "calculation": target.derived or fact.derived,
                       "status": status, "flags": flags})
    if status == "conflict" and a is not None:
        shown = f"{target.value:,.2f}{'%' if target.unit == 'percent' else ''}"
        fc.proposed_correction = f"filings give {shown} for {fact.label} ({fc.metric.replace('_', ' ')}); the script says {a.raw}"


def verify_in_text(fc: FinancialClaim, text: str, source_id: str, source_url: str, document: str, kind: str) -> None:
    """A release or article: the claimed figure next to the metric words (tables in thousands/millions too)."""
    a = _amount(fc)
    if a is None or not text:
        return
    words = METRICS[fc.metric]["words"]
    best = None
    for line in re.split(r"(?<=[.!?])\s+|\n", text):
        low = line.lower()
        if not any(w in low for w in words):
            continue
        for b in parse_amounts(line):
            for k in (1.0, 1e3, 1e6) if b.unit in ("number", "USD") and b.scale == 1 else (1.0,):
                cand = b.value * k
                st = compare_value(a, cand, a.unit if b.unit in ("number", "USD", "USD/shares") else b.unit)
                if st != "conflict":
                    best = (st, line.strip()[:400], b.raw, k)
                    break
            if best:
                break
        if best:
            break
    if best:
        st, excerpt, raw, k = best
        fc.matches.append({"source_id": source_id, "source_url": source_url, "document": document, "kind": kind, "status": st,
                           "excerpt": excerpt, "reported_text": raw, "table_scale": k if k != 1.0 else None})


def summarise(fc: FinancialClaim, ledger, min_sources: int) -> None:
    hits = [m for m in fc.matches if m["status"] in ("exact_match", "normalized_match", "partial_match")]
    conflicts = [m for m in fc.matches if m["status"] == "conflict"]
    ids = {m["source_id"] for m in hits}
    pubs = set()
    for sid in ids:
        src = ledger.get(sid) if ledger else None
        pubs.add((src.duplicate_of or src.domain) if src else sid)
    fc.sources_found, fc.distinct_publishers = len(ids), len(pubs)
    if conflicts and hits:
        fc.status = "conflict"
        fc.flags.append("sources disagree: see matches")
    elif conflicts:
        fc.status = "conflict"
    elif hits:
        order = {"exact_match": 0, "normalized_match": 1, "partial_match": 2}
        fc.status = min((m["status"] for m in hits), key=order.get)
    else:
        fc.status = "not_found"
    if fc.distinct_publishers < min_sources:
        fc.limitations.append(f"{fc.distinct_publishers} distinct source(s) found; target is {min_sources}")
    if not any(m.get("document", "").startswith(("SEC", "Issuer")) for m in hits):
        fc.limitations.append("no primary source (filing or issuer release) confirms this figure")
    for m in fc.matches:
        for f in m.get("flags", []):
            if f not in fc.flags:
                fc.flags.append(f)


# -- outputs ----------------------------------------------------------------------------
def write_reports(folder: Path, claims: list[FinancialClaim]) -> None:
    rows = [c.to_dict() for c in claims]
    (folder / "financial_claims.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    buf = io.StringIO()
    cols = ["claim_id", "status", "company", "ticker", "cik", "metric", "value", "unit", "period", "comparison", "basis", "sources_found",
            "distinct_publishers", "flags", "limitations", "proposed_correction", "text"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for c in claims:
        a = c.amount or {}
        p = c.period
        w.writerow({"claim_id": c.claim_id, "status": c.status, "company": c.company, "ticker": c.ticker, "cik": c.cik or "", "metric": c.metric,
                    "value": a.get("value", ""), "unit": a.get("unit", ""), "period": f"{p.get('fp') or ''} {p.get('fy') or ''}".strip(),
                    "comparison": c.comparison or "", "basis": c.basis, "sources_found": c.sources_found, "distinct_publishers": c.distinct_publishers,
                    "flags": "; ".join(c.flags), "limitations": "; ".join(c.limitations), "proposed_correction": c.proposed_correction, "text": c.text})
    (folder / "financial_claims.csv").write_text(buf.getvalue(), encoding="utf-8")
    lines = ["# Financial verification report", "",
             "Each figure is compared after normalising units (scale, currency, percent vs points) but the narration is not changed.",
             "XBRL company facts are GAAP as filed; adjusted (non-GAAP) figures can only be confirmed by the issuer's release.", ""]
    for c in claims:
        a = c.amount or {}
        lines += [f"## {c.claim_id}: {c.status}", f"> {c.text}", "",
                  f"- company: {c.company or 'not identified'} {f'({c.ticker}, CIK {c.cik})' if c.ticker else ''}",
                  f"- metric: {c.metric}; value: {a.get('raw', '—')} → {a.get('value', '—')} {a.get('unit', '')}; "
                  f"period: {c.period.get('raw') or 'not stated'}; comparison: {c.comparison or 'none'}; basis: {c.basis}",
                  f"- sources: {c.sources_found} ({c.distinct_publishers} distinct publishers)"]
        for m in c.matches:
            desc = m.get("document", m["source_id"])
            extra = f" {m['fiscal_period']} {m['metric_label']} = {m['reported_value']}" if "reported_value" in m else ""
            calc = f" | {m['calculation']}" if m.get("calculation") else ""
            ex = f" | “{m['excerpt'][:200]}”" if m.get("excerpt") else ""
            lines.append(f"  - [{m['source_id']}] {desc}: **{m['status']}**{extra}{calc}{ex} {m.get('note', '')}".rstrip())
        lines += [f"- flag: {f}" for f in c.flags] + [f"- limitation: {x}" for x in c.limitations]
        if c.proposed_correction:
            lines.append(f"- proposed correction (not applied): {c.proposed_correction}")
        lines.append("")
    (folder / "financial_verification_report.md").write_text("\n".join(lines), encoding="utf-8")
