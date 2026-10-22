# Research policy

## Order of sources

1. **URLs you give** (`source_urls`), read first.
2. **Official publishers**: robots.txt-allowed RSS feeds in `research.official_feeds` (defaults:
   Federal Reserve press releases, Census economic indicators, Bank of England news), filtered to the topic.
3. **GDELT DOC 2.0** article search: free, no key, commercial use allowed with citation. Its limit
   (one request per 5 s) is respected; on HTTP 429 the fetcher waits at least twice that.
4. **Datasets**: FRED series (CSV) when the topic or script mentions what a series measures.
5. **Finance** (when company figures are detected): SEC EDGAR XBRL company facts, the company's 8-K
   item 2.02 earnings release (EX-99), news text as cross-checks, and Yahoo Finance as a *link only*.
6. **Off by default** (restricted): Bing News RSS (its feed says personal, non-commercial use only)
   and Google News RSS (news.google.com/robots.txt disallows `/rss` for crawlers). Turn them on in
   `research.providers` only if you have checked that your use is allowed.

Wikipedia and Wikidata are not used as evidence. (The older Prepare Topic workflow still fetches logos
from Wikidata.)

## How requests are made (`engine/package/http.py`)

* Each request sends a User-Agent (`research.user_agent`, or the `RESEARCH_USER_AGENT` repository
  variable). SEC requests need `SEC_USER_AGENT` = "Your Name you@example.com"; without it EDGAR is
  skipped and the report says so.
* robots.txt is checked before every host. A disallowed page is not fetched, and is recorded as
  `blocked_robots`.
* Per-host minimum interval (1 s by default; GDELT 5.5 s; SEC 0.17 s), bounded retries with backoff,
  and `Retry-After` honoured (capped at 30 s). 401/403/451 are never retried.
* Paywalls, bot checks and login walls are not bypassed. Such a page becomes `paywall_or_blocked`, and its
  title is never used as a headline.
* Responses are cached under `$WHITEBOARD_CACHE/research` for `research.cache_ttl_hours` (24 h), with
  the URL, status, content type, fetch time and User-Agent stored next to each body.
* Page content is parsed with the standard-library HTML parser. Scripts are never run (except inside
  the sandboxed Chromium used for screenshots), and article text is treated as data, never as
  instructions.
* URLs must be http(s), without credentials, and not local or private addresses.

## Claim checking (`engine/package/claims.py`)

Each sentence of a supplied script is one claim, classified as `factual`, `prediction`, `opinion`,
`question` or `narrative`. Only factual claims are checked. Statuses:

| Status | Meaning |
|---|---|
| supported | every figure in the sentence appears in one source sentence (at the narration's precision) with enough shared wording; claims without figures need a strong meaning match from the multilingual model |
| partially_supported | some figures, or figures without enough shared wording; or matched by wording only; or an Urdu claim whose numbers match an English source |
| contradicted | a source says the same kind of figure, in very similar wording, with a different value |
| outdated | supported only by sources older than `research.claims.outdated_after_days` while the claim says "now/today/this year" |
| not_found | nothing comparable in the collected sources |
| opinion | not a checkable fact (predictions, opinions, questions, transitions) |

Keyword overlap alone is never verification. Syndicated copies (≥ 60 % shared text) count as one
source. Publication dates (`published_at`) and data periods (`data_period`) are kept apart. Scripts
are never rewritten. Unsupported claims are only reported, in `research/source_report.md` and
`claims.json`.

## Financial claims (`engine/package/finance.py`, `sec.py`)

Each figure in a finance sentence is its own claim: company (ticker/CIK), metric, value, unit,
scale, currency, period, comparison and basis. Figures are compared after normalising (bn/million,
cents, basis points, percent vs percentage points), while the original wording is kept.

* XBRL facts are labelled by the filing that first reported the period, and year-to-date values are
  skipped. Q4 = FY − (Q1 + Q2 + Q3), with the formula recorded. Restatements keep the earlier value,
  and a claim that matches only the original value is a `conflict`.
* YoY/QoQ growth and margins are recomputed, with the formula and inputs stored.
* Adjusted or non-GAAP figures cannot be confirmed from XBRL (which is GAAP), so they get
  `partial_match` with a `basis_differs` flag.
* Statuses: `exact_match`, `normalized_match`, `partial_match`, `conflict`, `not_found`. The target is
  `min_sources_per_claim` = 3 distinct publishers. Fewer is a recorded limitation, never invented
  corroboration.
* Outputs: `financial_claims.json/.csv`, `financial_verification_report.md`, and
  `data/finance/<ticker>_facts_used.json`. A correction is proposed, never applied.
