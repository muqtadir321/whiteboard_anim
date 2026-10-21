"""Draft scripts for a topic from the captured evidence (local Ollama model, free).

The writer only gets the evidence (headlines, article sentences, chart values) and must tag
each visual with a cue. The draft is then checked: unknown evidence ids are removed,
article quotes that are not on the page lose their highlight, and every number that the
evidence does not contain becomes `{VERIFY: ...}`, which blocks rendering until a human
replaces it. Without a model, an extractive draft is built from the evidence instead.
"""

from __future__ import annotations

import json
import re
import urllib.request

from ..numbers import extract_quantities
from .cues import CUE_TAG, extract_cues

RULES = """Rules:
- 110 to 150 words in total (45 to 60 seconds). Short spoken sentences. 4 to 6 short paragraphs (one idea each).
- Paragraph 1 is a hook that opens with the surprising fact. The last line is a one-sentence takeaway.
- Explain cause and effect in plain words, like a smart friend. No greetings, no "in this video", no hashtags.
- Use ONLY facts from the evidence below. Never invent numbers, names or dates.
- Put visual tags in square brackets directly before the words they illustrate:
    [news]                      the headline cards (use once, in the hook)
    [article N "exact words"]   show article N and highlight those words; copy 4 to 12 words EXACTLY from its sentences
    [chart ID DATE]             show chart ID with a marker on DATE (YYYY or YYYY-MM-DD from the chart values)
    [logo "Organisation"]       show the logo of an organisation named in the evidence
- Use at least one [article ...] tag and, if a chart is listed, one [chart ...] tag.
- Return only the script."""


def evidence_brief(topic: str, news: list[dict], articles: list[dict], charts: list[dict], chart_summaries: dict[str, str]) -> str:
    lines = [f"Topic: {topic}", "", "Headlines:"]
    lines += [f"- {n['title']} ({n.get('publisher', '')})" for n in news[:6]]
    for a in articles:
        lines += ["", f"Article {a['id']}: {a['title']} ({a['publisher']}). Sentences:"]
        lines += [f"- {s}" for s in key_sentences(a.get("paragraphs", []))[:12]]
    for c in charts:
        lines += ["", f"Chart {c['id']}: {c['title']}. Values: {chart_summaries.get(c['id'], '')}"]
    return "\n".join(lines)


def key_sentences(paragraphs: list[str], limit: int = 14) -> list[str]:
    """Sentences with numbers first: they carry the facts a short video needs."""
    sents = []
    for p in paragraphs:
        sents += [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if 40 <= len(s.strip()) <= 260]
    with_num = [s for s in sents if re.search(r"\d", s)]
    rest = [s for s in sents if s not in with_num]
    return (with_num + rest)[:limit]


def ollama(prompt: str, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 900, temperature: float = 0.5) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"temperature": temperature, "num_predict": 700}}).encode()
    req = urllib.request.Request(f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("response", "").strip()


def english_prompt(brief: str) -> str:
    return ("You write narration for 60-second YouTube Shorts that explain money news with real proof on screen.\n\n"
            f"{RULES}\n\nEvidence:\n{brief}\n\nScript:")


def roman_urdu_prompt(english: str) -> str:
    return ("Translate this YouTube Shorts narration into natural spoken Roman Urdu (Urdu written in English letters, "
            "the way Pakistanis text, e.g. 'Ghar khareedna mushkil kyun ho gaya?'). Keep every [tag] exactly as it is, "
            "including the English words in quotes, at the same place. Keep the numbers. Return only the narration.\n\n"
            f"{english}")


def urdu_prompt(english: str) -> str:
    return ("Translate this YouTube Shorts narration into natural spoken Urdu written in Urdu script (Nastaliq letters, "
            "e.g. 'گھر خریدنا مشکل کیوں ہو گیا؟'). Write common English loanwords the way Urdu speakers say them, in Urdu script "
            "(e.g. پولیس، اکاؤنٹ، ٹرانسفر). Keep every [tag] exactly as it is, including the English words in quotes, at the same "
            "place. Keep the numbers as digits. Return only the narration.\n\n"
            f"{english}")


def clean_output(text: str) -> str:
    text = re.sub(r"^```\w*|```$", "", text.strip(), flags=re.M).strip()
    text = re.sub(r"^(script|narration)\s*:\s*", "", text, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def evidence_numbers(texts: list[str]) -> set[float]:
    out: set[float] = set()
    for t in texts:
        for q in extract_quantities(t, "en"):
            out.add(round(q.value, 2))
    return out


def validate(script: str, articles: dict, charts: set[str], logos_ok: set[str] | None, known_numbers: set[float], lang: str = "en") -> tuple[str, list[str]]:
    """Fix or flag a draft. Returns (script, notes)."""
    notes: list[str] = []

    def fix_tag(m: re.Match) -> str:
        kind = m.group(1).lower()
        _, cues = extract_cues(m.group(0))
        c = cues[0]
        if kind == "article":
            art = articles.get(c.ref)
            if art is None:
                notes.append(f"removed [article {c.ref}]: no such article")
                return ""
            if c.quote and not art.find_quote(c.quote):
                notes.append(f"article {c.ref}: quote not on the page, highlight removed: {c.quote!r}")
                return f"[article {c.ref}]"
        if kind == "chart" and c.ref not in charts:
            notes.append(f"removed [chart {c.ref}]: series not downloaded")
            return ""
        if kind == "logo" and logos_ok is not None and c.ref.lower() not in logos_ok:
            notes.append(f"removed [logo {c.ref}]: no logo found")
            return ""
        return m.group(0)

    script = CUE_TAG.sub(fix_tag, script)
    # numbers outside tags must come from the evidence
    pieces = re.split(r"(\[[^\]]*\])", script)
    for i, piece in enumerate(pieces):
        if piece.startswith("["):
            continue
        qs = [q for q in extract_quantities(piece, lang) if q.kind in ("percent", "currency", "multiplier", "count")
              and (q.kind != "count" or any(ch.isdigit() for ch in q.source) or q.value >= 10)]
        for q in sorted(qs, key=lambda q: -q.span[0]):
            if any(abs(q.value - v) <= max(0.011, abs(v) * 0.005) for v in known_numbers):
                continue
            notes.append(f"number not found in the evidence: {q.source!r}")
            piece = piece[:q.span[0]] + "{VERIFY: " + q.source + "}" + piece[q.span[1]:]
        pieces[i] = piece
    return "".join(pieces), notes


def extractive_draft(news: list[dict], articles: list[dict], charts: list[dict]) -> str:
    """No model available: the strongest evidence sentences in order, with cue tags."""
    paras = []
    if news:
        paras.append(f"[news] {news[0]['title'].rstrip('.')}.")
    for c in charts[:1]:
        last = c["points"][-1]
        paras.append(f"[chart {c['id']} {last[0][:4]}] {c['title']}: the latest value is {last[1]}{c['units'] if c['units'] == '%' else ''}.")
    for a in articles[:2]:
        for s in key_sentences(a.get("paragraphs", []))[:2]:
            quote = " ".join(s.split()[:10])
            paras.append(f'[article {a["id"]} "{quote}"] {s}')
    paras.append("{VERIFY: write the takeaway line}")
    return "\n\n".join(paras) + "\n"


def drop_unverified(script: str) -> tuple[str, int]:
    """One-click mode: remove every sentence that still holds a {VERIFY: ...} placeholder
    (a number the evidence does not support), together with its tags."""
    removed = 0
    paras = []
    for para in script.split("\n\n"):
        keep = []
        for sent in re.split(r"(?<=[.!?۔؟])\s+", para.strip()):
            if "{VERIFY" in sent:
                removed += 1
            elif sent.strip():
                keep.append(sent.strip())
        if keep:
            paras.append(" ".join(keep))
    return "\n\n".join(paras) + "\n", removed


SENT_SPLIT = re.compile(r"(?<=[.!?۔؟])\s+")
LEAD_TAGS = re.compile(r"^(\s*\[[^\]]*\]\s*)*")
WORD = re.compile(r"[a-z]{4,}")
EN_STOP = {"that", "this", "with", "from", "have", "they", "their", "there", "about", "which", "would", "into", "than", "more", "most",
           "only", "were", "been", "when", "what", "said", "also", "some", "just", "like", "over", "even", "will", "your"}


def _numbers(text: str, lang: str) -> set[float]:
    out = set()
    for q in extract_quantities(text, lang):
        out.add(round(q.value, 2))
        if q.kind == "currency" and q.scale_word:
            out.add(round(q.value / {"lakh": 1e5, "crore": 1e7, "million": 1e6, "billion": 1e9, "trillion": 1e12}.get(q.scale_word, 1), 2))
    return out


def auto_tag(script: str, lang: str, articles: dict, paragraphs: dict[str, list[str]], charts: list[dict], news: bool) -> tuple[str, list[str]]:
    """Your own script, no tags: insert [news] / [article N "quote"] / [chart ID date] before the
    sentences the evidence backs. Shared numbers count most, then shared concepts (language
    independent, so an Urdu sentence can point at an English article). Words are never changed."""
    from ..concept_mapper import match_concepts

    if extract_cues(script)[1]:
        return script, ["script already has visual tags; left as it is"]
    notes: list[str] = []
    cands = []  # (article id, sentence, numbers, concepts, words)
    for aid, art in articles.items():
        for sent in key_sentences(paragraphs.get(aid, []), limit=40):
            if art.find_quote(" ".join(sent.split()[:10])):
                low = sent.lower()
                cands.append((aid, sent, _numbers(sent, "en"), {m.concept for m in match_concepts(sent, "en")},
                              {w for w in WORD.findall(low) if w not in EN_STOP}))
    used: set[str] = set()
    chart_done: set[str] = set()
    out_paras = []
    first = True
    for para in script.strip().split("\n\n"):
        out_sents = []
        for sent in SENT_SPLIT.split(para.strip()):
            lead = LEAD_TAGS.match(sent).group(0)
            body = sent[len(lead):]
            cue = ""
            if first and news:
                cue, first = "[news] ", False
            else:
                nums = _numbers(body, lang)
                cons = {m.concept for m in match_concepts(body, lang)}
                words = {w for w in WORD.findall(body.lower()) if w not in EN_STOP} if lang == "en" else set()
                best, best_score = None, 0.0
                for c in cands:
                    if c[1] in used:
                        continue
                    shared = {n for n in nums & c[2] if n not in (0, 1)}
                    score = 3 * len(shared) + len(cons & c[3]) + 0.5 * len(words & c[4])
                    if score > best_score:
                        best, best_score = c, score
                if best and best_score >= 4:
                    used.add(best[1])
                    toks = best[1].split()
                    hit = next((i for i, t in enumerate(toks) if _numbers(t, "en") & nums), 0)
                    a = max(0, min(hit - 4, len(toks) - 10))
                    quote = " ".join(toks[a:a + 10]).replace('"', "'").strip(" ,;:")
                    if articles[best[0]].find_quote(quote):
                        cue = f'[article {best[0]} "{quote}"] '
                if not cue:
                    for ch in charts:
                        if ch["id"] in chart_done:
                            continue
                        vals = [v for _, v in ch["points"]]
                        near = [n for n in nums if any(abs(n - v) <= max(0.5, abs(v) * 0.03) for v in vals[-60:])]
                        if near:
                            chart_done.add(ch["id"])
                            cue = f"[chart {ch['id']} {ch['points'][-1][0]}] "
                            break
            if cue:
                notes.append(f"{cue.strip()} -> {body[:60]}")
            out_sents.append(lead + cue + body)
        out_paras.append(" ".join(out_sents))
    return "\n\n".join(out_paras) + "\n", notes
