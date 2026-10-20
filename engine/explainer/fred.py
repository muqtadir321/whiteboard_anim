"""Economic data series from FRED (Federal Reserve Bank of St. Louis), free CSV, no API key."""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

from ..research.sources import fetch

# id: (title, units, original source, keywords, transformation)
SERIES = {
    "MORTGAGE30US": ("30-Year Fixed Rate Mortgage Average in the United States", "%", "Freddie Mac via FRED",
                     ("mortgage", "home loan", "housing", "home buyer", "homebuyer", "refinanc"), ""),
    "MSPUS": ("Median Sales Price of Houses Sold for the United States", "$", "U.S. Census Bureau and HUD via FRED",
              ("home price", "house price", "housing market", "home value", "afford"), ""),
    "CPIAUCSL": ("Inflation: Consumer Prices, Percent Change from a Year Ago", "%", "U.S. Bureau of Labor Statistics via FRED",
                 ("inflation", "cost of living", "prices", "cpi", "groceries"), "pc1"),
    "FEDFUNDS": ("Federal Funds Effective Rate", "%", "Board of Governors of the Federal Reserve System via FRED",
                 ("federal reserve", "fed ", "interest rate", "rate cut", "rate hike", "powell"), ""),
    "UNRATE": ("Unemployment Rate", "%", "U.S. Bureau of Labor Statistics via FRED", ("unemployment", "jobs", "layoff", "hiring"), ""),
    "DGS10": ("Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity", "%", "Board of Governors of the Federal Reserve System via FRED",
              ("treasury", "bond", "yield"), ""),
    "GASREGW": ("US Regular All Formulations Gas Price", "$", "U.S. Energy Information Administration via FRED", ("gas price", "gasoline", "fuel"), ""),
    "PSAVERT": ("Personal Saving Rate", "%", "U.S. Bureau of Economic Analysis via FRED", ("saving", "savings"), ""),
    "REVOLSL": ("Revolving Consumer Credit Owned and Securitized (billions of dollars)", "$", "Board of Governors of the Federal Reserve System via FRED",
                ("credit card", "consumer debt", "revolving"), ""),
}


def pick_series(text: str, limit: int = 2) -> list[str]:
    low = " " + text.lower() + " "
    scored = []
    for sid, (_, _, _, keys, _) in SERIES.items():
        hits = sum(low.count(k) for k in keys)
        if hits:
            scored.append((hits, sid))
    return [sid for _, sid in sorted(scored, reverse=True)[:limit]]


def parse_csv(data: bytes) -> list[tuple[str, float]]:
    rows = csv.reader(io.StringIO(data.decode("utf-8", "replace")))
    next(rows, None)
    out = []
    for row in rows:
        if len(row) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]):
            try:
                out.append((row[0], round(float(row[1]), 3)))
            except ValueError:
                continue  # "." marks a missing value
    return out


def download(sid: str, years: int = 8, timeout: float = 30) -> dict | None:
    title, units, source, _, transform = SERIES[sid]
    start = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}" + (f"&transformation={transform}" if transform else "")
    try:
        points = parse_csv(fetch(url, timeout))
    except Exception:
        return None
    if len(points) < 10:
        return None
    if len(points) > 600:  # weekly/daily series: thin out, keep the last point
        step = len(points) // 400 + 1
        points = points[::step] + ([points[-1]] if (len(points) - 1) % step else [])
    return {"id": sid, "title": title, "units": units, "source": source, "url": f"https://fred.stlouisfed.org/series/{sid}", "points": points}


def summary(chart: dict) -> str:
    """A few dated values for the script writer (first, low, high, latest)."""
    pts = chart["points"]
    lo = min(pts, key=lambda p: p[1])
    hi = max(pts, key=lambda p: p[1])
    u = chart["units"]
    fmt = (lambda v: f"{v:.2f}%") if u == "%" else (lambda v: f"${v:,.0f}" if v >= 100 else f"${v:.2f}")
    picks = sorted({pts[0], lo, hi, pts[-1]})
    return "; ".join(f"{d}: {fmt(v)}" for d, v in picks)
