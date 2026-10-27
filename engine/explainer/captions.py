"""Word-by-word captions (animation.explainer.captions: karaoke).

Two or three spoken words at a time in a band above the Shorts title area: white letters with a
thick black outline, the word being said in yellow. Most Shorts are watched with the sound on, but bold captions keep
viewers who skim or watch muted, and they carry the hook while the board is still being drawn.
Urdu and Pashto chunks are shaped with HarfBuzz and laid out right to left.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..text_shaper import shape_line

BAND = (70.0, 1410.0, 1010.0, 1560.0)  # design units: above the Shorts title/buttons, below the board
SIZE = 96.0
MAX_WORDS = 3
GAP_BREAK = 0.4  # a pause this long starts a new chunk
ENDS = ".!?;:,،۔؟"


@dataclass
class CapWord:
    contours: list
    bbox: tuple[float, float, float, float]
    t0: float
    t1: float
    text: str


@dataclass
class Chunk:
    t0: float
    t1: float
    words: list[CapWord] = field(default_factory=list)
    box: tuple[float, float, float, float] = (0, 0, 0, 0)


def group(tokens: list[str], times: list[tuple[float, float]], max_words: int = MAX_WORDS) -> list[list[int]]:
    """Token indices per chunk: at most max_words, broken after punctuation and at pauses."""
    out: list[list[int]] = []
    cur: list[int] = []
    for i, tok in enumerate(tokens):
        if cur and (len(cur) >= max_words or times[i][0] - times[cur[-1]][1] > GAP_BREAK):
            out.append(cur)
            cur = []
        cur.append(i)
        if tok.rstrip()[-1:] in ENDS:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def build(tokens: list[str], times: list[tuple[float, float]], lang: str, start_after: float = 0.0,
          band: tuple[float, float, float, float] = BAND) -> list[Chunk]:
    """Caption chunks for the whole narration (absolute times). Chunks that start before `start_after`
    (the hook card already shows those words) are left out."""
    rtl = lang in ("ur", "ps")
    groups = group(tokens, times)
    chunks: list[Chunk] = []
    x0, y0, x1, y1 = band
    for gi, idx in enumerate(groups):
        t0 = times[idx[0]][0]
        if t0 < start_after - 0.05:
            continue
        nxt = times[groups[gi + 1][0]][0] if gi + 1 < len(groups) else None
        last_end = times[idx[-1]][1]
        t1 = nxt if nxt is not None and nxt - last_end < 0.6 else last_end + 0.25
        words = [re.sub(r"^[\"'“(]+|[\"'”),;:]+$", "", tokens[i]) for i in idx]
        size = SIZE
        shaped = [shape_line(w, size, lang, True) for w in words]
        space = size * 0.3
        width = sum(s.width for s in shaped) + space * (len(shaped) - 1)
        if width > x1 - x0:
            size *= (x1 - x0) / width
            shaped = [shape_line(w, size, lang, True) for w in words]
            space = size * 0.3
            width = sum(s.width for s in shaped) + space * (len(shaped) - 1)
        ascent = max(s.ascent for s in shaped)
        descent = max(s.descent for s in shaped)
        base = (y0 + y1) / 2 + (ascent - descent) / 2
        x = (x0 + x1) / 2 + width / 2 if rtl else (x0 + x1) / 2 - width / 2
        cw: list[CapWord] = []
        for i, (w, sl) in zip(idx, zip(words, shaped)):
            left = x - sl.width if rtl else x
            contours = [[(left + px, base + py) for px, py in c] for g in sl.glyphs for c in g.contours()]
            cw.append(CapWord(contours, (left, base - ascent, left + sl.width, base + descent), times[i][0], times[i][1], w))
            x = left - space if rtl else x + sl.width + space
        pad = 24.0
        box = (min(c.bbox[0] for c in cw) - pad, base - ascent - pad * 0.6, max(c.bbox[2] for c in cw) + pad, base + descent + pad * 0.6)
        chunks.append(Chunk(t0, t1, cw, box))
    return chunks
