"""Source ledger: every document research looked at, with a stable id (sources.json / .csv / .txt)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .urls import canonical_url, domain, source_id

SOURCE_TYPES = ("user_provided", "official", "primary_filing", "issuer_release", "dataset", "news", "reference", "other")
RETRIEVAL_STATUS = ("ok", "cached", "blocked_robots", "http_error", "network_error", "paywall_or_blocked", "not_fetched", "metadata_only")
COMMERCIAL = ("approved", "restricted", "unknown")
# Works of U.S. federal agencies are public domain (17 U.S.C. 105). Other .gov sites (states, cities) and
# everything else stay "unknown" until a person checks the terms; third-party pages are never "approved".
FEDERAL_PUBLIC_DOMAIN = ("sec.gov", "census.gov", "bls.gov", "bea.gov", "eia.gov", "fhfa.gov", "treasury.gov", "cbo.gov",
                         "gao.gov", "ftc.gov", "consumerfinance.gov", "usa.gov")


@dataclass
class Source:
    source_id: str
    title: str
    publisher: str
    canonical_url: str
    accessed_at_utc: str
    published_at: str = ""  # publication date of the document
    data_period: str = ""  # period the data refers to (not the publication date)
    source_type: str = "other"
    author: str = ""
    license_or_terms_url: str = ""
    retrieval_method: str = ""  # http | playwright | rss | api | user | cache
    retrieval_status: str = "ok"
    extracted_text_path: str = ""
    screenshot_path: str = ""
    claims_supported: list[str] = field(default_factory=list)
    notes: str = ""
    confidence: str = "medium"  # high | medium | low (authority + directness, not truth)
    commercial_status: str = "unknown"
    # extra (not in the CSV): where the document came from and how it was found
    origin_url: str = ""
    provider: str = ""
    duplicate_of: str = ""  # syndicated copy of another source (same text, different site)

    @property
    def domain(self) -> str:
        return domain(self.canonical_url)

    def to_dict(self) -> dict:
        return asdict(self)


CSV_FIELDS = [f.name for f in fields(Source)][:18]


def make_source(url: str, title: str, publisher: str, accessed_at: str, **kw) -> Source:
    canon = canonical_url(url)
    kw.setdefault("commercial_status", default_commercial_status(canon, kw.get("source_type", "other")))
    return Source(source_id(canon), title, publisher, canon, accessed_at, origin_url=url, **kw)


def default_commercial_status(url: str, source_type: str) -> str:
    d = domain(url)
    return "approved" if any(d == h or d.endswith("." + h) for h in FEDERAL_PUBLIC_DOMAIN) else "unknown"


class Ledger:
    def __init__(self):
        self.sources: dict[str, Source] = {}

    def add(self, src: Source) -> Source:
        """Same document twice (e.g. RSS link and user URL): keep one row, merge what is known."""
        old = self.sources.get(src.source_id)
        if old is None:
            self.sources[src.source_id] = src
            return src
        for f in fields(Source):
            if f.name == "claims_supported":
                old.claims_supported = sorted(set(old.claims_supported) | set(src.claims_supported))
            elif not getattr(old, f.name) and getattr(src, f.name):
                setattr(old, f.name, getattr(src, f.name))
        if src.retrieval_status == "ok" and old.retrieval_status != "ok":
            old.retrieval_status = "ok"
        return old

    def get(self, sid: str) -> Source | None:
        return self.sources.get(sid)

    def link(self, sid: str, claim_id: str) -> None:
        s = self.sources.get(sid)
        if s and claim_id not in s.claims_supported:
            s.claims_supported.append(claim_id)
            s.claims_supported.sort()

    def ordered(self) -> list[Source]:
        rank = {t: i for i, t in enumerate(SOURCE_TYPES)}
        return sorted(self.sources.values(), key=lambda s: (rank.get(s.source_type, 99), s.source_id))

    # -- files ----------------------------------------------------------------------
    def write(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        rows = [s.to_dict() for s in self.ordered()]
        (folder / "sources.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "claims_supported": ";".join(r["claims_supported"])})
        (folder / "sources.csv").write_text(buf.getvalue(), encoding="utf-8")
        (folder / "sources.txt").write_text(self.as_text(), encoding="utf-8")

    def as_text(self, only: set[str] | None = None) -> str:
        lines = ["Sources (full URLs). Inclusion does not imply endorsement by any publisher.", ""]
        for s in self.ordered():
            if only is not None and s.source_id not in only:
                continue
            if s.duplicate_of:
                continue
            date = f" ({s.published_at[:10]})" if s.published_at else ""
            period = f" [data: {s.data_period}]" if s.data_period else ""
            lines.append(f"[{s.source_id}] {s.publisher or s.domain}: {s.title}{date}{period}\n    {s.canonical_url}")
        return "\n".join(lines) + "\n"

    @classmethod
    def load(cls, folder: Path) -> "Ledger":
        led = cls()
        p = folder / "sources.json"
        if p.exists():
            for row in json.loads(p.read_text(encoding="utf-8")):
                known = {f.name for f in fields(Source)}
                led.sources[row["source_id"]] = Source(**{k: v for k, v in row.items() if k in known})
        return led
