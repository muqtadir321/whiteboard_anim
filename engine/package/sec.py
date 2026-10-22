"""SEC EDGAR (U.S. reporting companies): tickers, XBRL company facts, 8-K earnings releases.

Fair access (https://www.sec.gov/os/accessing-edgar-data): requests carry a User-Agent with a name
and contact e-mail (SEC_USER_AGENT or research.sec_user_agent), at most ~6 requests per second here
(SEC allows 10), cached, and 403/429 are not hammered. Without a contact User-Agent EDGAR is
skipped and the report says so. Structured data (companyfacts) first; the earnings release
exhibit (EX-99.x of an 8-K with item 2.02) as text second.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass

from .extract import parse_html
from .http import FetchError, Fetcher

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|the|class [a-c]|n\.?v|s\.?a|ag|/\w+/?)\b\.?", re.I)


def sec_user_agent(rc: dict) -> str:
    ua = os.environ.get("SEC_USER_AGENT") or rc.get("sec_user_agent", "")
    return ua if "@" in ua else ""


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", SUFFIXES.sub(" ", re.sub(r"[^\w\s/&.-]", " ", name.lower()))).strip(" .,-")


def _capitalised(name: str, text: str) -> bool:
    """The name is written as a name ("Target", "TARGET"), not as a common word ("the target rate")."""
    pat = r"\s+".join(re.escape(w[0].upper()) + "(?i:" + re.escape(w[1:]) + ")" for w in name.split())
    return re.search(rf"(?<!\w){pat}(?!\w)", text) is not None


@dataclass
class Company:
    cik: int
    ticker: str
    title: str


class SecClient:
    def __init__(self, fetcher: Fetcher):
        self.f = fetcher
        self._companies: list[Company] | None = None

    @classmethod
    def from_config(cls, rc: dict, **kw) -> "SecClient | None":
        ua = sec_user_agent(rc)
        if not ua:
            return None
        f = Fetcher.from_config(rc, **kw)
        f.ua, f.min_interval = ua, 0.17
        return cls(f)

    def _json(self, url: str) -> dict:
        return json.loads(self.f.get(url, "application/json").body)

    def companies(self) -> list[Company]:
        if self._companies is None:
            data = self._json(TICKERS_URL)
            rows = data.values() if isinstance(data, dict) else data
            self._companies = [Company(int(r["cik_str"]), r["ticker"].upper(), r["title"]) for r in rows]
        return self._companies

    def find(self, text: str, limit: int = 3) -> list[Company]:
        """Companies named in text: explicit tickers ("(NASDAQ: NVDA)", "$NVDA") first, then names."""
        comps = self.companies()
        by_ticker = {c.ticker: c for c in comps}
        found: list[Company] = []
        for m in re.finditer(r"\((?:NASDAQ|NYSE|AMEX|NYSE American)?:?\s*([A-Z]{1,5}(?:\.[A-Z])?)\)|\$([A-Z]{1,5})\b", text):
            c = by_ticker.get((m.group(1) or m.group(2) or "").upper())
            if c and c not in found:
                found.append(c)
        low = " " + norm_name(text) + " "
        names = sorted(((norm_name(c.title), c) for c in comps), key=lambda x: -len(x[0]))
        for name, c in names:
            if len(found) >= limit:
                break
            if len(name) >= 4 and f" {name} " in low and c not in found and all(c.cik != f.cik for f in found) and _capitalised(name, text):
                found.append(c)
        return found[:limit]

    def facts(self, cik: int) -> dict:
        return self._json(FACTS_URL.format(cik=cik))

    def submissions(self, cik: int) -> dict:
        return self._json(SUBMISSIONS_URL.format(cik=cik))

    def earnings_releases(self, cik: int, limit: int = 4) -> list[dict]:
        """Recent 8-K filings with item 2.02 (results of operations) and their EX-99 exhibit URL."""
        rec = self.submissions(cik).get("filings", {}).get("recent", {})
        out = []
        for i, form in enumerate(rec.get("form", [])):
            items = (rec.get("items") or [""] * (i + 1))[i] or ""
            if form not in ("8-K", "8-K/A") or "2.02" not in items:
                continue
            acc = rec["accessionNumber"][i]
            base = ARCHIVE.format(cik=cik, acc=acc.replace("-", ""))
            try:
                idx = self._json(base + "/index.json")
            except (FetchError, ValueError):
                continue
            ex = next((it["name"] for it in idx.get("directory", {}).get("item", [])
                       if re.search(r"ex-?99|exhibit99|ex99", it.get("name", ""), re.I) and it["name"].lower().endswith((".htm", ".html", ".txt"))), None)
            if ex:
                out.append({"form": form, "accession": acc, "filed": rec["filingDate"][i], "report_date": rec.get("reportDate", [""] * (i + 1))[i],
                            "url": f"{base}/{ex}", "filing_index": f"{base}/"})
            if len(out) >= limit:
                break
        return out

    def document_text(self, url: str) -> str:
        res = self.f.get(url, "text/html,text/plain")
        if url.lower().endswith(".txt"):
            return res.text
        page = parse_html(res.text, max_paragraphs=2000)
        # releases keep figures in tables: keep table rows as lines too
        rows = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " | ", r))).strip(" |") for r in re.findall(r"<tr[^>]*>(.*?)</tr>", res.text, re.S | re.I)]
        return "\n".join(page.paragraphs + [r for r in rows if re.search(r"\d", r)])
