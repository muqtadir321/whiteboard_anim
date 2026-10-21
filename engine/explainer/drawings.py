"""Choose the drawings for a script: the things the narrator actually names, on the word they are said.

1. With the free local model (Ollama): it lists the drawable things each sentence mentions, in
   English, with the exact word from the sentence ("oil = تیل"). Listing things is easy for a
   small model in any language; each English name is then matched to one of the ~1,250 Fluent
   Emoji drawings by meaning (multilingual embeddings with CSLS hub correction), and only clear
   matches are kept ("rupee" finds no drawing and gets none).
2. Without a model, every word and word pair is compared with the drawing names directly and
   only near-certain matches are kept.

The result is written into the script as `[icon name]` tags right before the word, so the
renderer draws it exactly when that word is spoken, and a person can still edit the choice.
"""

from __future__ import annotations

import re

import numpy as np

from .cues import CUE_TAG, VOICE_TAG, extract_cues
from .emoji import EmojiLibrary

SENT_END = re.compile(r"(?<=[.!?۔؟])\s+")
PUNCT = ".,،۔؟?!:;\"'“”‘’()"
STRICT_COS, STRICT_CSLS = 0.83, 0.45  # without the model: the word alone must clearly name the drawing
FLOOR = 0.30  # shortlist entries below this are dropped


def _word_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of spoken words, skipping [tags]."""
    tags = [m.span() for m in CUE_TAG.finditer(text)] + [m.span() for m in VOICE_TAG.finditer(text)]
    spans = []
    for m in re.finditer(r"\S+", text):
        if not any(a <= m.start() < b for a, b in tags):
            spans.append(m.span())
    return spans


def shortlist(sentences: list[str], lib: EmojiLibrary, emb, per_sentence: int = 8):
    """Per sentence: [(score, word index, phrase, drawing, word cosine, word CSLS)], best first, one per drawing."""
    words_per = []
    phrases, owner = [], []
    for si, s in enumerate(sentences):
        words = [w.strip(PUNCT) for w in s.split()]
        words_per.append(words)
        for wi, w in enumerate(words):
            if w:
                phrases.append(w)
                owner.append((si, wi))
            if wi + 1 < len(words) and w and words[wi + 1]:
                phrases.append(f"{w} {words[wi + 1]}")
                owner.append((si, wi))
    out = [[] for _ in sentences]
    if not phrases:
        return out
    labels = lib.vectors(emb)
    # CSLS: words and drawings that are close to everything score lower
    sim, score = lib.scores(phrases, emb)
    ctx = emb.embed([" ".join(w for w in ws if w) or "-" for ws in words_per]) @ labels.T
    ctx -= ctx.mean(axis=1, keepdims=True)
    for row, (si, wi) in enumerate(owner):
        total = score[row] + 0.8 * ctx[si]
        for j in np.argsort(total)[-3:][::-1]:
            out[si].append((float(total[j]), wi, phrases[row], lib.names[j], float(sim[row, j]), float(score[row, j])))
    for si, rows in enumerate(out):
        best: dict[str, tuple] = {}
        for r in sorted(rows, reverse=True):
            if r[0] >= FLOOR and r[3] not in best:
                best[r[3]] = r
        out[si] = list(best.values())[:per_sentence]
    return out


THING_COS, THING_CSLS = 0.62, 0.22  # an English thing name must clearly match the drawing


def things_prompt(sentences: list[str]) -> str:
    lines = [f"{i}. {s}" for i, s in enumerate(sentences, 1)]
    return ("Below are the numbered sentences of a narrated video. For each sentence, list the concrete things it "
            "mentions that can be shown as a small picture: objects, places, vehicles, money, products, buildings, "
            "people's jobs. Skip abstract ideas, numbers, dates, feelings and idioms. Give each thing as a short English "
            "name, then \"=\", then the exact word or words from the sentence. At most 2 per sentence.\n"
            "Answer with one line per sentence and nothing else, for example:\n"
            "3: oil = تیل; mobile phone = موبائل فون\n4: none\n\n" + "\n".join(lines))


def parse_things(raw: str, n: int) -> list[list[tuple[str, str]]]:
    out: list[list[tuple[str, str]]] = [[] for _ in range(n)]
    for m in re.finditer(r"^\W*(\d+)\s*[:.)\-]+\s*(.*)$", raw, re.M):
        si = int(m.group(1)) - 1
        if not 0 <= si < n or m.group(2).strip().lower().startswith("none"):
            continue
        for part in re.split(r"[;|]", m.group(2)):
            if "=" in part:
                name, src = (x.strip(" *\"'`") for x in part.split("=", 1))
                if name and src and len(out[si]) < 2:
                    out[si].append((name.lower(), src))
    return out


def _source_index(sentence: str, src: str, name: str, emb) -> int:
    words = [w.strip(PUNCT) for w in sentence.split()]
    first = src.split()[0].strip(PUNCT) if src.split() else ""
    for k, w in enumerate(words):
        if first and (w == first or (len(first) > 2 and first in w)):
            return k
    if not words:
        return 0
    sims = emb.embed([w or "-" for w in words]) @ emb.embed([name])[0]
    return int(np.argmax(sims))  # the word that means the thing


def choose_with_model(sentences: list[str], lib: EmojiLibrary, emb, model: str, host: str, log=print):
    """Things named by the local model -> drawings; at most 2 per sentence, no repeats in neighbouring sentences."""
    from .draft import ollama

    raw = ollama(things_prompt(sentences), model, host, timeout=900, temperature=0.1)
    log("things named by the model: " + " | ".join(raw.strip().splitlines()[:12])[:400])
    things = parse_things(raw, len(sentences))
    flat = [(si, name, src) for si, rows in enumerate(things) for name, src in rows]
    if not flat:
        raise ValueError("no usable lines in the reply")
    names = sorted({name for _, name, _ in flat})
    sim, score = lib.scores(names, emb)
    row_of = {name: k for k, name in enumerate(names)}
    out: list[list[tuple]] = [[] for _ in sentences]
    recent: dict[str, int] = {}
    for si, name, src in flat:
        row = row_of[name]
        j = int(np.argmax(score[row]))
        drawing = lib.names[j]
        if sim[row, j] < THING_COS or score[row, j] < THING_CSLS:
            continue
        if si - recent.get(drawing, -9) <= 2 or any(drawing == q[3] for q in out[si]):
            continue  # the same picture again right away says nothing new
        recent[drawing] = si
        out[si].append((float(score[row, j]), _source_index(sentences[si], src, name, emb), src, drawing))
    return out


def choose_strict(lists):
    out = []
    for rows in lists:
        keep = []
        for r in sorted(rows, key=lambda r: -r[5]):
            # one drawing per phrase ("موبائل فون" and "فون" are the same thing)
            if r[4] >= STRICT_COS and r[5] >= STRICT_CSLS and all(abs(r[1] - k[1]) > 1 for k in keep):
                keep.append(r[:4])
        out.append(keep[:2])
    return out


def tag_drawings(script: str, lang: str, lib: EmojiLibrary, emb, model: str | None = None,
                 host: str = "http://127.0.0.1:11434", log=print) -> tuple[str, list[str]]:
    """Insert `[icon drawing]` tags before the words that name them. Scripts that already have
    [icon] tags are left alone (a person chose them)."""
    if any(c.kind == "icon" for c in extract_cues(script)[1]):
        return script, ["script already has [icon] tags; drawings left as they are"]
    paras = script.strip().split("\n\n")
    sent_raw: list[tuple[int, str]] = []
    for pi, para in enumerate(paras):
        for s in SENT_END.split(para.strip()):
            sent_raw.append((pi, s))
    spoken = [" ".join(s[a:b] for a, b in _word_spans(s)) for _, s in sent_raw]
    chosen = None
    if model:
        try:
            chosen = choose_with_model(spoken, lib, emb, model, host, log)
            log(f"drawings from the things {model} named: {sum(map(len, chosen))}")
        except Exception as exc:
            log(f"naming things with {model} failed ({type(exc).__name__}: {exc}); keeping only confident matches")
    if chosen is None:
        chosen = choose_strict(shortlist(spoken, lib, emb))
    notes = []
    new_sents: list[tuple[int, str]] = []
    for (pi, raw), picks in zip(sent_raw, chosen):
        spans = _word_spans(raw)
        for _, wi, phrase, name in sorted(picks, key=lambda r: -r[1]):
            if wi < len(spans):
                a = spans[wi][0]
                raw = raw[:a] + f"[icon {name}] " + raw[a:]
                notes.append(f"[icon {name}] -> {phrase}")
        new_sents.append((pi, raw))
    out = []
    for pi in range(len(paras)):
        out.append(" ".join(s for p, s in new_sents if p == pi))
    return "\n\n".join(out) + "\n", notes


def render_time_drawings(raw, lang: str, cfg: dict, log=print):
    """A script without [icon] tags (not prepared by the Prepare Topic workflow): add confident
    drawings now (no model at render time). Returns (script, notes); unchanged when not possible."""
    from .layout import emoji_library
    from .semantic import get_embedder

    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    ex = cfg["animation"]["explainer"]
    if any(c.kind == "icon" for c in extract_cues(text)[1]) or not ex["semantic"]:
        return raw, []
    lib = emoji_library(log)
    emb = get_embedder(ex["semantic_model"], log) if lib else None
    if lib is None or emb is None:
        return raw, []
    tagged, notes = tag_drawings(text, lang, lib, emb, model=None, log=log)
    return tagged, notes
