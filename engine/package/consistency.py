"""English vs Urdu (or Roman Urdu) script consistency: same facts, numbers, dates and evidence.

Scripts are aligned by paragraph (the scene groups), not by word count. When the paragraph
counts differ, numbers are compared over the whole script and the report says so. Nothing is
rewritten; this only lists what a person should check.
"""

from __future__ import annotations

from collections import Counter

from ..explainer.cues import extract_cues
from ..numbers import extract_quantities
from .claims import Claim

LATIN_SKIP = {"The", "This", "That", "And", "But", "In", "On", "At", "For", "So", "If", "It", "We", "You", "They", "He", "She", "A", "An"}


def _values(text: str, lang: str) -> tuple[Counter, Counter]:
    """(amounts, years) normalised to plain numbers."""
    figs, years = Counter(), Counter()
    for q in extract_quantities(text, lang):
        (years if q.kind == "year" else figs)[round(q.value, 4)] += 1
    return figs, years


def _names(text: str) -> set[str]:
    out = set()
    for w in text.split()[1:]:
        w = w.strip(".,;:!?\"'()“”")
        if len(w) > 2 and w[:1].isupper() and w not in LATIN_SKIP and not w.isupper():
            out.add(w)
    return out


def _cue_set(text: str) -> set[tuple[str, str]]:
    return {(c.kind, c.ref) for c in extract_cues(text)[1] if c.kind not in ("icon", "hook")}


def report(scripts: dict[str, str], codes: dict[str, str], claims: dict[str, list[Claim]]) -> dict:
    """`scripts`: language key -> tagged script text; `codes`: key -> language code."""
    keys = [k for k in ("english", "urdu", "roman_urdu") if scripts.get(k)]
    out: dict = {"languages": keys, "alignment": "none", "paragraph_pairs": [], "number_mismatches": [], "date_mismatches": [],
                 "name_mismatches": [], "missing_evidence": [], "unmatched_claims": [], "notes": []}
    if "english" not in keys or len(keys) < 2:
        out["notes"].append("only one language: nothing to compare")
        return out
    other = next(k for k in keys if k != "english")
    en_p = [p for p in scripts["english"].strip().split("\n\n") if p.strip()]
    ot_p = [p for p in scripts[other].strip().split("\n\n") if p.strip()]
    pairs = list(zip(range(len(en_p)), range(len(ot_p))))
    if len(en_p) == len(ot_p):
        out["alignment"] = "paragraph"
    else:
        out["alignment"] = "global"
        out["notes"].append(f"paragraph counts differ (english {len(en_p)}, {other} {len(ot_p)}): numbers compared over the whole script")
        pairs = [(-1, -1)]
        en_p, ot_p = ["\n\n".join(en_p)], ["\n\n".join(ot_p)]
    by_para = {k: {} for k in (["english", other])}
    for k in by_para:
        for c in claims.get(k, []):
            by_para[k].setdefault(c.paragraph if out["alignment"] == "paragraph" else -1, []).append(c.claim_id)
    for i, j in pairs:
        a, b = extract_cues(en_p[max(i, 0)])[0], extract_cues(ot_p[max(j, 0)])[0]
        fa, ya = _values(a, codes["english"])
        fb, yb = _values(b, codes[other])
        row = {"paragraph": i + 1 if i >= 0 else "all", "english_claims": by_para["english"].get(i, []), f"{other}_claims": by_para[other].get(j, [])}
        out["paragraph_pairs"].append(row)
        if fa != fb:
            out["number_mismatches"].append({"paragraph": row["paragraph"], "only_english": sorted((fa - fb).elements()),
                                             f"only_{other}": sorted((fb - fa).elements())})
        if ya != yb:
            out["date_mismatches"].append({"paragraph": row["paragraph"], "only_english": sorted((ya - yb).elements()),
                                           f"only_{other}": sorted((yb - ya).elements())})
        if codes[other] == "roman_urdu":
            na, nb = _names(a), _names(b)
            missing = sorted(n for n in na - nb if n.lower() not in b.lower())
            if missing:
                out["name_mismatches"].append({"paragraph": row["paragraph"], f"not_in_{other}": missing})
        ca, cb = _cue_set(en_p[max(i, 0)]), _cue_set(ot_p[max(j, 0)])
        if ca != cb:
            out["missing_evidence"].append({"paragraph": row["paragraph"], "only_english": sorted(map(list, ca - cb)),
                                            f"only_{other}": sorted(map(list, cb - ca))})
        if row["english_claims"] and not row[f"{other}_claims"] or row[f"{other}_claims"] and not row["english_claims"]:
            out["unmatched_claims"].append(row)
    if codes[other] == "ur":
        out["notes"].append("names in Urdu script are not compared automatically (different script)")
    out["consistent"] = not (out["number_mismatches"] or out["date_mismatches"] or out["missing_evidence"] or out["unmatched_claims"])
    return out
