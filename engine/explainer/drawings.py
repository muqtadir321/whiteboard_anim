"""Choose the drawings for a script: the things the narrator actually names, on the word they are said.

1. Shortlist (any language, no word lists): every word and word pair of a sentence is compared
   with the names of the ~1,250 Fluent Emoji drawings using multilingual embeddings, corrected
   for "hub" words and drawings that are close to everything (CSLS), plus how well the drawing
   fits the whole sentence.
2. Choose: the free local model (Ollama) reads each sentence with its shortlist and keeps only
   drawings that literally show something said in that sentence ("hold your breath" is not
   lungs). Without the model, only very confident shortlist entries are kept.

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
FLOOR = 0.30  # below this a drawing is not even offered to the model


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
    q = emb.embed(phrases)
    sim = q @ labels.T
    # CSLS against the drawing names: words/drawings that are close to everything score lower
    rq = np.sort(sim, axis=1)[:, -10:].mean(axis=1, keepdims=True)
    rc = np.sort(sim, axis=0)[-10:, :].mean(axis=0, keepdims=True)
    score = 2 * sim - rq - rc
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


LETTERS = "abcdefgh"


def model_prompt(sentences: list[str], lists) -> str:
    lines = []
    for i, (s, rows) in enumerate(zip(sentences, lists), 1):
        if rows:
            opts = "  ".join(f"{LETTERS[k]}) {r[3]} ({r[2]})" for k, r in enumerate(rows[: len(LETTERS)]))
            lines.append(f"{i}. {s}\n   {opts}")
    return ("You choose small pictures for a narrated explainer video. Under each numbered sentence are picture options, "
            "each with the words it was matched to. Pick 0 to 2 options that show a thing or action the sentence really "
            "talks about. Skip idioms and other meanings of a word (\"hold your breath\" is not lungs, a patient person "
            "is not a hospital, a number is not a clock, December is not Christmas).\n"
            "Answer with one line per sentence and nothing else, using the option letters, for example:\n"
            "3: a, c\n4: none\n\n" + "\n".join(lines))


def parse_choice(raw: str, lists) -> list[list[tuple]]:
    out = [[] for _ in lists]
    for m in re.finditer(r"^\W*(\d+)\s*[:.)\-]+\s*(.*)$", raw, re.M):
        si = int(m.group(1)) - 1
        if not 0 <= si < len(lists) or "none" in m.group(2).lower():
            continue
        for letter in re.findall(r"\b([a-h])\b", m.group(2).lower()):
            k = LETTERS.index(letter)
            if k < len(lists[si]) and all(lists[si][k][3] != q[3] for q in out[si]):
                out[si].append(lists[si][k][:4])
    return [rows[:2] for rows in out]


def choose_with_model(sentences: list[str], lists, model: str, host: str, lang: str, log=print):
    """Ask the local model which shortlisted drawings show what each sentence literally says."""
    from .draft import ollama

    if not any(lists):
        return [[] for _ in sentences]
    raw = ollama(model_prompt(sentences, lists), model, host, timeout=900, temperature=0.1)
    log("drawing choice reply: " + " | ".join(raw.strip().splitlines()[:12])[:400])
    chosen = parse_choice(raw, lists)
    if not any(chosen):
        raise ValueError("no usable choice in the reply")
    return chosen


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
    lists = shortlist(spoken, lib, emb)
    chosen = None
    if model:
        try:
            chosen = choose_with_model(spoken, lists, model, host, lang, log)
            log(f"drawings chosen by {model}: {sum(map(len, chosen))}")
        except Exception as exc:
            log(f"drawing choice by {model} failed ({type(exc).__name__}: {exc}); keeping only confident matches")
    if chosen is None:
        chosen = choose_strict(lists)
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
