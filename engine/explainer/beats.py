"""Beats: one connected diagram per idea instead of a new loose icon every few seconds.

A beat is a run of consecutive scenes inside one paragraph of the script (long paragraphs are split
at a sentence boundary). The board builds up during the beat:

    node = a drawing + a handwritten label taken from the narration words that named it
    nodes appear at the moment their words are spoken, drawn stroke by stroke, and the previous
    node points to the new one with a hand-drawn arrow (the order the narration explains them)

Placement is a fixed grid chosen by the number of nodes (1 large and centred, 2-3 zigzag, 4 as a
2 x 2 flow), inside the safe area and below the key amounts at the top. Evidence (screenshots,
charts) has priority: nodes wait while it is on screen. A beat with nothing that can be drawn gets
its key phrase handwritten large, not a random picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..text_shaper import shape_line
from .render import Sprite, Stage, Word

SAFE_X0, SAFE_X1, BOTTOM = 70.0, 1010.0, 1790.0
MAX_NODES = 4
MIN_GAP = 1.0  # seconds between two nodes starting
MAX_BEAT = 14.0  # longer paragraphs are split at a sentence boundary
LABEL_SIZE = 58.0
INK = "#1E1E1E"
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been it its this that these those their
there they them he she his her we our you your i my not no so than then too very can could will would should may might
just about into over under after before more most less also has have had do does did what which who how why when where
all any some each such only own same up down out off again once here both few many much said says
almost really quite even still already usually right back well like just ever never always often""".split())
UR_STOP = set("کے کی کا میں سے کو نے اور یہ وہ ہے ہیں تھا تھی پر بھی تو ہی جو کہ ایک".split())


@dataclass
class Node:
    name: str
    params: dict
    t: float
    label: str
    forced: bool = False


@dataclass
class Beat:
    scenes: list[int]
    start: float
    end: float
    nodes: list[Node] = field(default_factory=list)
    sprites: list[Sprite] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    objective: str = ""


@dataclass
class _Block:
    box: tuple[float, float, float, float]


def _arrow_ends(a, b) -> tuple[_Block, _Block]:
    """Boxes for layout.arrow_sprite (which stops at 0.8 of each half size) so the arrow never touches a label:
    side by side, it joins the drawings at mid-height, above the labels; one above the other, it runs from
    below the upper label to the top of the lower drawing."""
    (ba, la), (bb, lb) = a, b
    ca, cb = ((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2), ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    if abs(cb[0] - ca[0]) >= abs(cb[1] - ca[1]):  # side by side: drawings only (labels are below)
        return _Block(ba), _Block(bb)

    def tall(box, bottom, grow_down: bool):
        # a box whose 0.8 x half height reaches just past the label (downwards) or the drawing top (upwards)
        x0, y0, x1, _ = box
        cy = (y0 + bottom) / 2
        half = ((bottom - y0) / 2 + 12) / 0.8
        hw = (x1 - x0) / 2
        return _Block(((x0 + x1) / 2 - hw, cy - half, (x0 + x1) / 2 + hw, cy + half))

    return tall(ba, la, True), tall(bb, lb, False)


def group_beats(spans: list[tuple[float, float]], para_ends: list[float], sentence_ends: list[float], max_seconds: float = MAX_BEAT) -> list[list[int]]:
    """Scene indices per beat: a new beat after each paragraph end, and inside a paragraph after a sentence
    end once the beat is longer than max_seconds."""
    beats: list[list[int]] = []
    cur: list[int] = []
    start = spans[0][0] if spans else 0.0
    for i, (s0, s1) in enumerate(spans):
        if cur:
            para_break = any(abs(s0 - p) < 0.35 or (spans[cur[-1]][1] - 0.05 <= p <= s0 + 0.05) for p in para_ends)
            long_break = s0 - start > max_seconds and any(abs(s0 - e) < 0.35 for e in sentence_ends)
            if para_break or long_break:
                beats.append(cur)
                cur, start = [], s0
        cur.append(i)
    if cur:
        beats.append(cur)
    return beats


NUMBER_WORDS = set("""zero one two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty hundred
thousand million billion trillion percent per cent point half""".split())
CONTRACTIONS = set("there's isn't it's don't that's can't won't didn't doesn't aren't wasn't they're we're you're here's what's".split())
# verbs that end a noun phrase ("savings shrink" -> "savings"); a short list of common ones is enough here
VERBS = set("""became become becomes made make makes set sets get gets run runs cut cuts wait waits earn earns spend spends hit hits
fight fights shrink shrinks double doubles rise rises rose fall falls fell grow grows grew keep keeps want wants expect expects
give gives copy copied start started came come comes slip slips buy buys sell sells need needs pay pays go goes went know take
takes took show shows say says think thinks look looks seem seems mean means barely notice shapes shape""".split())


def label_for(tokens: list[str], i: int, lang: str) -> str:
    """The narration words that name a drawing: the word itself, a describing word before it ("central
    bank"), the noun after a describing word ("interest" -> "interest rates") and a following capitalised
    word ("New Zealand"). Never a little word, a number word or a verb on its own."""
    if not tokens:
        return ""
    i = max(0, min(i, len(tokens) - 1))
    stop = UR_STOP if lang in ("ur", "ps") else STOP | CONTRACTIONS | NUMBER_WORDS

    def clean(w: str) -> str:
        return re.sub(r"^[^\w$%]+|[^\w%]+$", "", w)

    def content(w: str) -> bool:
        return bool(w) and w.lower() not in stop and not w[:1].isdigit()

    j = i
    while j < min(len(tokens), i + 5) and not content(clean(tokens[j])):  # tagged on a little word: the next content word
        j += 1
    if j >= len(tokens) or not content(clean(tokens[j])):
        return ""
    words = [clean(tokens[j])]
    ends = lambda k: tokens[k].rstrip()[-1:] in ".!?;:,،۔؟"  # noqa: E731
    if j > 0 and not ends(j - 1):
        prev = clean(tokens[j - 1])
        if content(prev) and prev.lower() not in VERBS and not re.search(r"(ed|ing|ly)$", prev):
            words.insert(0, prev)
    if j + 1 < len(tokens) and not ends(j) and len(words) < 3:
        nxt = clean(tokens[j + 1])
        proper = words[-1][:1].isupper() and nxt[:1].isupper()
        noun_after = (lang == "en" and content(nxt) and nxt.lower() not in VERBS and not re.search(r"(ed|ing|ly)$", nxt)
                      and words[-1].lower() not in VERBS)
        if content(nxt) and (proper or noun_after):
            words.append(nxt)
    text = " ".join(w for w in words if w)
    return text if len(text) <= 22 else words[-1][:22]


def slots(n: int, top: float, bottom: float = BOTTOM) -> list[tuple[tuple[float, float, float, float], tuple[float, float]]]:
    """(drawing box, label centre) per node, in narration order."""
    avail = bottom - top
    lh = LABEL_SIZE * 1.35
    cx = (SAFE_X0 + SAFE_X1) / 2
    if n <= 1:
        s = max(260.0, min(600.0, avail - lh - 60))
        y0 = top + (avail - s - lh) / 2
        return [((cx - s / 2, y0, cx + s / 2, y0 + s), (cx, y0 + s + lh * 0.75))]
    if n == 4:
        s = max(220.0, min(430.0, (avail - 2 * lh - 120) / 2))
        xs = (cx - 245, cx + 245)
        row_h = (avail - 2 * (s + lh)) / 3
        ys = (top + row_h, top + 2 * row_h + s + lh)
        order = [(0, 0), (1, 0), (1, 1), (0, 1)]  # clockwise: the arrows go round the board
        return [((xs[c] - s / 2, ys[r], xs[c] + s / 2, ys[r] + s), (xs[c], ys[r] + s + lh * 0.75)) for c, r in order]
    s = max(220.0, min(520.0 if n == 2 else 430.0, (avail - n * lh - (n + 1) * 30) / n))
    gap = (avail - n * (s + lh)) / (n + 1)
    out = []
    for k in range(n):
        x = cx - 210 if k % 2 == 0 else cx + 210  # zigzag, so arrows run diagonally and labels never collide
        y0 = top + gap + k * (s + lh + gap)
        out.append(((x - s / 2, y0, x + s / 2, y0 + s), (x, y0 + s + lh * 0.75)))
    return out


def text_word(text: str, size: float, centre: tuple[float, float], t: float, lang: str, color: str = INK,
              max_width: float = SAFE_X1 - SAFE_X0) -> Word | None:
    """A handwritten label, written left to right (right to left for Urdu)."""
    if not text:
        return None
    line = shape_line(text, size, lang)
    if line.width > max_width:
        size *= max_width / line.width
        line = shape_line(text, size, lang)
    x = min(max(centre[0] - line.width / 2, SAFE_X0), SAFE_X1 - line.width)
    base = centre[1] + line.ascent / 2
    contours = [[(x + px, base + py) for px, py in c] for g in line.glyphs for c in g.contours()]
    dur = min(1.0, 0.18 + 0.045 * len(text))
    w = Word(contours, color, t, dur, (x, base - line.ascent, x + line.width, base + line.descent), text)
    w.rtl = lang in ("ur", "ps")
    w.missing = list(line.missing_chars)
    return w


def compose(beat: Beat, picks: list[Node], stages: list[Stage], top: float, layout, lang: str, key_phrase: str) -> None:
    """Fill beat.sprites / beat.words: nodes on the grid, arrows between them, labels under them."""
    def stage_at(t):
        return next((st for st in stages if st.t_in <= t < st.t_out), None)

    chosen: list[Node] = []
    for nd in sorted(picks, key=lambda p: (not p.forced, p.t)):
        if len(chosen) >= MAX_NODES:
            break
        if any(c.name == nd.name or (nd.label and c.label.lower() == nd.label.lower()) for c in chosen):
            continue  # one drawing per thing said
        st = stage_at(nd.t)
        if st is not None:  # evidence first: the drawing waits until the screenshot/chart has gone
            if st.t_out > beat.end - 1.2:
                continue
            nd.t = st.t_out - 0.1
        if any(abs(c.t - nd.t) < MIN_GAP for c in chosen):
            nd.t = max(c.t for c in chosen) + MIN_GAP
            if nd.t > beat.end - 0.8:
                continue
        chosen.append(nd)
    chosen.sort(key=lambda n: n.t)
    beat.nodes = chosen
    end = beat.end
    # a screenshot arriving later in the beat clears the drawings that came before it
    later = [st.t_in for st in stages if beat.start < st.t_in < beat.end]
    if not chosen:
        if stage_at(beat.start + 0.2) is None and key_phrase:
            w = text_word(key_phrase, 92.0, ((SAFE_X0 + SAFE_X1) / 2, top + (BOTTOM - top) * 0.42), beat.start + 0.15, lang)
            if w:
                w.dur = min(2.2, 0.3 + 0.06 * len(key_phrase))
                beat.words.append(w)
                beat.objective = f"key phrase: {key_phrase}"
        return
    boxes = slots(len(chosen), top)
    prev = prev_block = None
    for k, (nd, (box, lab)) in enumerate(zip(chosen, boxes)):
        sp = layout.icon_sprite(nd.name, nd.params, box, nd.t + (0.35 if prev else 0.0), f"beat{beat.scenes[0]}:{k}:{nd.name}")
        if sp is None:
            continue
        sp.float_amp = 3.0
        stop = next((x for x in later if x > nd.t), None)
        sp.end = stop if stop is not None else end
        block = (box, lab[1] + LABEL_SIZE * 0.55)  # drawing box and the bottom of its label
        if prev is not None:
            a, b = _arrow_ends(prev_block, block)
            arrow = layout.arrow_sprite(a, b, nd.t)
            arrow.end = sp.end
            beat.sprites.append(arrow)
        beat.sprites.append(sp)
        w = text_word(nd.label, LABEL_SIZE, lab, sp.t + min(sp.draw, 1.0) * 0.7, lang, max_width=(box[2] - box[0]) + 160)
        if w:
            w.end = sp.end
            beat.words.append(w)
        prev, prev_block = sp, block
    beat.objective = " → ".join(n.label or n.name for n in chosen)
