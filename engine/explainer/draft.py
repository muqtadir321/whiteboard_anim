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


CITATION = re.compile(r"\[(?:\d+|[a-z]|citation needed|note \d+)\]", re.I)


def key_sentences(paragraphs: list[str], limit: int = 14, max_len: int = 260) -> list[str]:
    """Sentences with numbers first: they carry the facts a short video needs."""
    sents = []
    for p in paragraphs:
        p = CITATION.sub("", p)  # "1990.[6] The" is two sentences, and "[6]" is not part of a quote
        # sentences only: bylines and captions start with a symbol ("✍️ Anshul Majumdar looks at ...")
        sents += [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if 40 <= len(s.strip()) <= max_len and s.strip()[0].isalnum()]
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


# sentence ends, but not after initials such as "U.S." or titles such as "Dr."
SENT_SPLIT = re.compile(r"(?<!\b[A-Z]\.[A-Z]\.)(?<!\b[A-Z]\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<=[.!?۔؟])\s+")
PCT = re.compile(r"%|percent|per cent|fisad|فیصد", re.I)
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


def auto_tag(script: str, lang: str, articles: dict, paragraphs: dict[str, list[str]], charts: list[dict], news: bool,
             emb=None, model: str | None = None, host: str = "http://127.0.0.1:11434") -> tuple[str, list[str]]:
    """Your own script, no tags: insert [news] / [article N "quote"] / [chart ID date] before the
    sentences the evidence backs. Shared numbers count most, then shared concepts (language
    independent, so an Urdu sentence can point at an English article). With an embedder
    (semantic.get_embedder), paragraphs still without evidence then get the article sentence or
    chart closest in meaning, so screenshots keep coming through the video. Words are never changed."""
    from ..concept_mapper import match_concepts

    if extract_cues(script)[1]:
        return script, ["script already has visual tags; left as it is"]
    notes: list[str] = []
    cands = []  # (article id, sentence, numbers, concepts, words)
    for aid, art in articles.items():
        for sent in key_sentences(paragraphs.get(aid, []), limit=40, max_len=480):  # official pages write long sentences
            if art.find_quote(" ".join(sent.split()[:10])):
                low = sent.lower()
                cands.append((aid, sent, _numbers(sent, "en"), {m.concept for m in match_concepts(sent, "en")},
                              {w for w in WORD.findall(low) if w not in EN_STOP}))
    used: set[str] = set()
    chart_done: set[str] = set()

    def article_cue(c, nums) -> str:
        used.add(c[1])
        toks = c[1].split()
        hit = next((i for i, t in enumerate(toks) if _numbers(t, "en") & nums), 0)
        a = max(0, min(hit - 4, len(toks) - 10))
        # citation marks ("[1]") and stray brackets would end the [article ...] tag early and leak into the narration
        quote = re.sub(r"\[[^\]]*\]", "", " ".join(toks[a:a + 10])).replace("[", "").replace("]", "")
        quote = re.sub(r"\s+", " ", quote).replace('"', "'").strip(" ,;:")
        return f'[article {c[0]} "{quote}"] ' if articles[c[0]].find_quote(quote) else ""

    paras = []  # per paragraph, per sentence: [lead tags, body, new cue]
    for para in script.strip().split("\n\n"):
        rows = []
        for sent in SENT_SPLIT.split(para.strip()):
            lead = LEAD_TAGS.match(sent).group(0)
            rows.append([lead, sent[len(lead):], ""])
        paras.append(rows)
    first = news
    prev = ""
    for rows in paras:
        for row in rows:
            body = row[1]
            context, prev = prev, body
            if first:
                row[2], first = "[news] ", False
                # the hook's figure can still show its chart, at the number word, once the headlines have had their time
                hit = chart_cue(charts, body, _numbers(body, lang), chart_done, context)
                if hit:
                    toks = body.split(" ")
                    at = next((i for i, t in enumerate(toks) if _numbers(t, lang) & {hit[1]}), -1)
                    if at >= 6:
                        row[1] = " ".join(toks[:at] + [hit[0].strip()] + toks[at:])
                        chart_done.add(hit[2])
                continue
            nums = _numbers(body, lang)
            cons = {m.concept for m in match_concepts(body, lang)}
            words = {w for w in WORD.findall(body.lower()) if w not in EN_STOP} if lang == "en" else set()
            best, best_score = None, 0.0
            for c in cands:
                if c[1] in used:
                    continue
                shared = {n for n in nums & c[2] if n not in (0, 1)}
                same_unit = bool(shared) and bool(PCT.search(body)) and bool(PCT.search(c[1]))  # "2 percent" said and written
                score = 3 * len(shared) + len(cons & c[3]) + 0.5 * len(words & c[4]) + (1 if same_unit else 0)
                if score > best_score:
                    best, best_score = c, score
            if best and best_score >= 4:
                row[2] = article_cue(best, nums)
            if not row[2]:
                hit = chart_cue(charts, body, nums, chart_done, context)
                if hit:
                    row[2] = hit[0]
                    chart_done.add(hit[2])
    if emb is not None:
        notes += semantic_gaps(paras, lang, cands, charts, used, emb, article_cue, model, host)
    out_paras = []
    for rows in paras:
        for _, body, cue in rows:
            if cue:
                notes.append(f"{cue.strip()} -> {body[:60]}")
        out_paras.append(" ".join(lead + cue + body for lead, body, cue in rows))
    return "\n\n".join(out_paras) + "\n", notes


# what a chart measures, for cues (download choice uses fred.SERIES keywords): naming the Fed is not naming its rate
CHART_SUBJECT = {"FEDFUNDS": ("interest rate", "fed funds", "federal funds", "rate cut", "rate hike", "raising rates", "cutting rates",
                              "raise rates", "cut rates", "policy rate")}


def _said_precision(n: float) -> float:
    """Half a unit of the last digit said: "7 percent" covers 6.5-7.5, "3.4 percent" covers 3.35-3.45."""
    text = f"{n:.6f}".rstrip("0").rstrip(".")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return 0.5 * 10 ** -decimals + 1e-9


def chart_cue(charts: list[dict], body: str, nums: set[float], done: set, context: str = "") -> tuple[str, float, tuple] | None:
    """(cue, matched number, key) when a figure in the sentence is a value of a chart: at a year the sentence names
    ("around 3% in 2021" -> marker on 2021), else among the latest values. Each chart/date is used once."""
    if body.rstrip().endswith(("?", "؟")):
        return None  # a question ("why not aim for four percent?") is not a claim about the data
    years = {m for m in re.findall(r"\b(?:19|20)\d\d\b", body)}
    unit = bool(re.search(r"%|percent|per cent|fisad|فیصد|\$|dollar", body, re.I))
    # small whole numbers ("Not zero.", "Not five.") only count next to a unit; years are dates, not values
    figs = {n for n in nums if not (1900 <= n <= 2100 and float(n).is_integer()) and (unit or not (float(n).is_integer() and abs(n) <= 10))}
    from . import fred

    low = " " + (context + " " + body).lower() + " "  # the subject may be named in the sentence before
    for ch in charts:
        pts = ch["points"]
        # the sentence must be about what the chart measures (its subject words), not just share a number with it
        keys = CHART_SUBJECT.get(ch["id"]) or (fred.SERIES[ch["id"]][3] if ch["id"] in fred.SERIES else
                                                tuple(w for w in re.findall(r"[a-z]{5,}", ch.get("title", "").lower())))
        if keys and not any(k in low for k in keys):
            continue
        for y in sorted(years):
            vals = [v for d, v in pts if str(d).startswith(y)]
            n = next((n for n in figs for v in vals if abs(n - v) <= max(0.5, abs(v) * 0.03)), None)
            if n is not None and (ch["id"], y) not in done:
                return f"[chart {ch['id']} {y}] ", n, (ch["id"], y)
        # the most recent date whose value is the figure said: the marker shows that value, not a different latest one
        # no year named: the figure describes the present, so only the latest 12 months count, closest value first
        last = str(pts[-1][0]) if pts else ""
        since = f"{int(last[:4]) - 1}{last[4:]}" if last[:4].isdigit() else ""
        recent = [(d, v) for d, v in pts if str(d) > since]
        close = [(abs(n - v), -i, n, d) for i, (d, v) in enumerate(recent) for n in figs if abs(n - v) <= _said_precision(n)]
        hit = (min(close)[2], min(close)[3]) if close else None  # the value closest to what is said, then the most recent
        if hit is not None and (ch["id"], hit[1]) not in done:
            return f"[chart {ch['id']} {hit[1]}] ", hit[0], (ch["id"], hit[1])
    return None


SEMANTIC_MIN = 0.15  # evidence kept without the model: CSLS + cosine - 0.45 (unrelated text scores below 0)


def semantic_gaps(paras, lang, cands, charts, used, emb, article_cue, model=None, host="http://127.0.0.1:11434") -> list[str]:
    """Paragraphs without any visual get the closest article sentence or chart (by meaning, any language).
    The best three per paragraph go to the local model, which picks the one that really illustrates the
    paragraph (or none); without a model only clear matches are kept. A chart may come back once more,
    but not in neighbouring paragraphs."""
    from .semantic import csls

    sents = [(pi, si, row[1]) for pi, rows in enumerate(paras) for si, row in enumerate(rows) if len(row[1].split()) >= 3]
    items = [("article", c) for c in cands] + [("chart", ch) for ch in charts]
    if not sents or not items:
        return []
    sim = emb.embed([s for _, _, s in sents]) @ emb.embed([c[1] for c in cands] + [ch["title"] for ch in charts]).T
    score = csls(sim) + sim - 0.45  # CSLS stops bland sentences matching everything; cosine keeps strong matches
    shown: dict[str, list[int]] = {}
    for pi, rows in enumerate(paras):
        for row in rows:
            if row[2].startswith("[chart"):
                shown.setdefault(row[2].split()[1], []).append(pi)
    gaps = []  # (paragraph, [(score, sentence index, kind, item)])
    for pi, rows in enumerate(paras):
        if any(row[2] for row in rows):
            continue
        opts = []
        for k, (spi, si, _) in enumerate(sents):
            if spi != pi:
                continue
            for j, (kind, item) in enumerate(items):
                if kind == "article" and item[1] in used:
                    continue
                if kind == "chart":
                    seen = shown.get(item["id"], [])
                    if len(seen) >= 2 or any(abs(p - pi) <= 1 for p in seen):
                        continue
                if score[k, j] > 0:
                    opts.append((float(score[k, j]), si, kind, item))
        best: dict[str, tuple] = {}
        for o in sorted(opts, key=lambda o: -o[0]):
            key = o[3][1] if o[2] == "article" else o[3]["id"]
            best.setdefault(key, o)
        if best:
            gaps.append((pi, list(best.values())[:3]))
    picks: dict[int, tuple] = {}
    if model and gaps:
        try:
            picks = choose_evidence(paras, gaps, model, host)
        except Exception:
            picks = {}
            model = None
    if not model:
        picks = {pi: opts[0] for pi, opts in gaps if opts[0][0] >= SEMANTIC_MIN}
    notes = []
    for pi, (sc, si, kind, item) in sorted(picks.items()):
        if kind == "article":
            if item[1] in used:
                continue
            cue = article_cue(item, _numbers(paras[pi][si][1], lang))
        else:
            seen = shown.get(item["id"], [])
            if any(abs(p - pi) <= 1 for p in seen):
                continue
            cue = f"[chart {item['id']} {item['points'][-1][0]}] "
            shown.setdefault(item["id"], []).append(pi)
        if cue:
            paras[pi][si][2] = cue
            notes.append(f"meaning match {sc:.2f}{' (chosen by ' + model + ')' if model else ''}: {cue.strip()[:60]}")
    return notes


def choose_evidence(paras, gaps, model, host) -> dict[int, tuple]:
    """One prompt for the whole script: for each paragraph, which option (if any) illustrates it."""
    blocks = []
    for n, (pi, opts) in enumerate(gaps, 1):
        text = " ".join(row[1] for row in paras[pi])
        lines = [f"P{n}: {text}"]
        for k, (_, _, kind, item) in enumerate(opts, 1):
            lines.append(f"  {k}) {'chart: ' + item['title'] if kind == 'chart' else 'article: ' + item[1][:220]}")
        blocks.append("\n".join(lines))
    prompt = ("A narrated explainer video shows a screenshot or chart while a paragraph is spoken. For each paragraph "
              "below, choose the option that is clearly about the same thing the paragraph says (same fact, cause or "
              "subject), or 0 if none is. Be strict: a vaguely related option is 0.\n"
              "Answer with JSON only, for example {\"P1\": 2, \"P2\": 0}\n\n" + "\n\n".join(blocks))
    raw = ollama(prompt, model, host, timeout=900, temperature=0.1)
    m = re.search(r"\{.*\}", raw, re.S)
    answer = json.loads(m.group(0)) if m else {}
    out = {}
    for n, (pi, opts) in enumerate(gaps, 1):
        try:
            k = int(answer.get(f"P{n}", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= k <= len(opts):
            out[pi] = opts[k - 1]
    return out
