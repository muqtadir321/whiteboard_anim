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


SMALL_NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}


def find_contrast(tokens: list[str], times: list[float], unit: str) -> "Contrast | None":
    """'Not zero. Not five. Exactly two.' -> values with the rejected ones and the chosen one, in spoken order.
    Needs at least two values and one rejection; English wording only."""
    items = []
    for i in range(len(tokens) - 1):
        lead = re.sub(r"\W", "", tokens[i].lower())
        word = re.sub(r"[^\w.]", "", tokens[i + 1].lower()).rstrip(".")
        if lead not in ("not", "no", "exactly", "only", "just"):
            continue
        value = SMALL_NUMBERS.get(word)
        if value is None and re.fullmatch(r"\d+(\.\d+)?", word):
            value = float(word) if "." in word else int(word)
        if value is None:
            continue
        items.append((f"{value}{unit}", lead in ("exactly", "only", "just"), times[i + 1]))
    if len(items) >= 2 and any(not c for _, c, _ in items) and any(c for _, c, _ in items):
        return Contrast(items)
    return None


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
    """(drawing box, label centre) per node, in narration order. One dominant drawing: the first node (the idea's
    subject) is large at the top; the steps that follow are smaller below it, in reading order."""
    avail = bottom - top
    lh = LABEL_SIZE * 1.35
    cx = (SAFE_X0 + SAFE_X1) / 2
    if n <= 1:
        s = max(260.0, min(680.0, avail - lh - 60))
        y0 = top + (avail - s - lh) / 2
        return [((cx - s / 2, y0, cx + s / 2, y0 + s), (cx, y0 + s + lh * 0.75))]
    support = {2: 1, 3: 2, 4: 3}[n]
    sw = {1: 440.0, 2: 420.0, 3: 300.0}[support]  # supporting drawings
    hero = max(300.0, min({2: 660.0, 3: 620.0, 4: 600.0}[n], avail - sw - 2 * lh - 3 * 95))  # gaps >= 95: arrows stay readable
    gap = (avail - hero - sw - 2 * lh) / 3
    y_hero = top + gap
    y_sup = y_hero + hero + lh + gap
    out = [((cx - hero / 2, y_hero, cx + hero / 2, y_hero + hero), (cx, y_hero + hero + lh * 0.75))]
    if support == 1:
        xs = [cx + 150]
    elif support == 2:
        xs = [cx - 235, cx + 235]
    else:
        xs = [SAFE_X0 + sw / 2 + 10, cx, SAFE_X1 - sw / 2 - 10]
    for x in xs:
        out.append(((x - sw / 2, y_sup, x + sw / 2, y_sup + sw), (x, y_sup + sw + lh * 0.75)))
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


# colour carries meaning, used sparingly: red for falling values and warnings, green for gains
RED, GREEN = "#C0392B", "#2E8B57"
NEGATIVE = set("""fall falls fell falling drop drops dropped decline declines cut cuts cutting loss losses lose shrink shrinks
danger dangerous risk worse crash recession slowdown layoffs debt deflation lower down weaker expensive""".split())
# no "higher"/"rise": whether more is good depends on what rises (higher inflation is not good news)
POSITIVE = set("""gain gains profit profits save savings safety cushion stronger stable recovery""".split())


def tone_color(label: str, context: str = "") -> str:
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", label + " " + context)}
    if words & NEGATIVE:
        return RED
    if words & POSITIVE and not words & NEGATIVE:
        return GREEN
    return INK


@dataclass
class Contrast:
    """'Not zero. Not five. Exactly two.': values compared, the rejected ones crossed out, the chosen one circled."""
    items: list[tuple[str, bool, float]]  # (shown value, chosen?, time said)


def stroke_sprite(layout, strokes, box, t: float, draw: float, name: str) -> Sprite:
    """Hand-drawn strokes (geometry.Pen) as a sprite revealed stroke by stroke."""
    from ..geometry import Element
    from .layout import element_surface, fit_box

    w, h = box[2] - box[0], box[3] - box[1]
    el = Element(name, w, h)
    el.add(*strokes)
    surf, st = element_surface(el, max(w, h) * layout.scale, layout.style, layout.lang)
    return Sprite(surf, fit_box(surf.get_width(), surf.get_height(), box), t, "draw", name=name, strokes=st, draw=draw)


def compose_contrast(beat: Beat, c: Contrast, top: float, layout, lang: str) -> float:
    """Value cards in a row at the top of the board. Returns the y where the rest of the board can start."""
    import random

    from ..geometry import Pen

    n = len(c.items)
    cw, ch = min(270.0, (SAFE_X1 - SAFE_X0 - (n - 1) * 30) / n), 190.0
    x = (SAFE_X0 + SAFE_X1) / 2 - (n * cw + (n - 1) * 30) / 2
    for k, (value, chosen, t) in enumerate(c.items):
        box = (x, top + 30, x + cw, top + 30 + ch)
        pen = Pen(random.Random(k + 7), wobble=0.5)
        card = stroke_sprite(layout, [pen.rect(0, 0, cw, ch, r=18, width=1.0)], box, t, 0.45, "value_card")
        card.end = beat.end
        beat.sprites.append(card)
        w = text_word(value, 104.0, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), t + 0.25, lang, color=INK, max_width=cw - 30)
        if w:
            w.end = beat.end
            beat.words.append(w)
        mark_t = max(t + 0.7, (c.items[k + 1][2] - 0.2) if k + 1 < n else t + 0.7)
        if chosen:  # circled in green
            ring = Pen(random.Random(k + 11), wobble=0.4).ellipse(cw / 2 + 20, ch / 2 + 20, cw / 2 + 12, ch / 2 + 14, width=1.5, color="positive")
            sp = stroke_sprite(layout, [ring], (box[0] - 20, box[1] - 20, box[2] + 20, box[3] + 20), t + 0.5, 0.6, "circle_choice")
        else:  # crossed out in red
            p = Pen(random.Random(k + 13), wobble=0.4)
            sp = stroke_sprite(layout, [p.line((14, 14), (cw - 14, ch - 14), width=1.6, color="negative"),
                                        p.line((cw - 14, 14), (14, ch - 14), width=1.6, color="negative")], box, mark_t, 0.35, "cross_out")
        sp.end = beat.end
        beat.sprites.append(sp)
        x += cw + 30
    beat.objective = "compare: " + " / ".join(("✓ " if ch_ else "✗ ") + v for v, ch_, _ in c.items)
    return top + 30 + ch + 50


def compose(beat: Beat, picks: list[Node], stages: list[Stage], top: float, layout, lang: str, key_phrase: str,
            memory: dict | None = None, contrast: Contrast | None = None) -> None:
    """Fill beat.sprites / beat.words: one dominant drawing and its supporting steps, arrows between them in the
    order they are explained, labels under them. `memory` (name -> label) keeps objects from earlier boards:
    they come back looking the same and are recognised faster than they were first drawn."""
    memory = memory if memory is not None else {}

    def stage_at(t):
        return next((st for st in stages if st.t_in <= t < st.t_out), None)

    if contrast is not None:
        # evidence first: values said while a screenshot is up are shown, in order, as soon as it has gone
        st = stage_at(contrast.items[0][2])
        if st is None or st.t_out < contrast.items[-1][2] + 1.0:
            free = st.t_out if st is not None else 0.0
            items, t_prev = [], 0.0
            for v, ch, t in contrast.items:
                t = max(t, free, t_prev + 0.35)
                items.append((v, ch, t))
                t_prev = t
            top = compose_contrast(beat, Contrast(items), top, layout, lang)
            values = {v for v, _, _ in items}
            picks = [p for p in picks if p.label not in values]  # the circled card already shows that value
        else:
            contrast = None
    max_nodes = MAX_NODES if contrast is None else 2
    chosen: list[Node] = []
    # spoken-word drawings first, then objects the viewer already knows from earlier boards, then the rest
    for nd in sorted(picks, key=lambda p: (not p.forced, p.name not in memory, p.t)):
        if len(chosen) >= max_nodes:
            break
        if any(c.name == nd.name or (nd.label and c.label.lower() == nd.label.lower()) for c in chosen):
            continue  # one drawing per thing said
        # evidence first, and room between drawings; each move is checked against both again
        for _ in range(6):
            st = stage_at(nd.t)
            if st is not None:  # the drawing waits until the screenshot/chart has gone
                nd.t = st.t_out + 0.05
                continue
            if any(abs(c.t - nd.t) < MIN_GAP for c in chosen):
                nd.t = max(c.t for c in chosen) + MIN_GAP
                continue
            break
        if nd.t > beat.end - 0.8 or stage_at(nd.t) is not None:
            continue
        chosen.append(nd)
    chosen.sort(key=lambda n: n.t)
    beat.nodes = chosen
    end = beat.end
    # a screenshot arriving later in the beat clears the drawings that came before it
    later = [st.t_in for st in stages if beat.start < st.t_in < beat.end]
    if not chosen:
        if contrast is None and stage_at(beat.start + 0.2) is None and key_phrase:
            w = text_word(key_phrase, 92.0, ((SAFE_X0 + SAFE_X1) / 2, top + (BOTTOM - top) * 0.42), beat.start + 0.15, lang)
            if w:
                w.dur = min(2.2, 0.3 + 0.06 * len(key_phrase))
                beat.words.append(w)
                beat.objective = f"key phrase: {key_phrase}"
        return
    boxes = slots(len(chosen), top)
    prev = prev_block = None
    for k, (nd, (box, lab)) in enumerate(zip(chosen, boxes)):
        known = nd.name in memory
        # the same object always looks the same (seeded by its name), so it reads as the same thing coming back
        sp = layout.icon_sprite(nd.name, nd.params, box, nd.t + (0.35 if prev else 0.0), f"object:{nd.name}")
        if sp is None:
            continue
        if known:
            sp.draw = min(sp.draw, 0.6)
        sp.float_amp = 3.0
        stop = next((x for x in later if x > nd.t), None)
        if stop is not None and stop - sp.t < 1.5:
            continue  # cannot be drawn and labelled before evidence takes the board: a flicker, not an explanation
        sp.end = stop if stop is not None else end
        block = (box, lab[1] + LABEL_SIZE * 0.55)  # drawing box and the bottom of its label
        if prev is not None:
            a, b = _arrow_ends(prev_block, block)
            arrow = layout.arrow_sprite(a, b, nd.t)
            arrow.end = sp.end
            beat.sprites.append(arrow)
        beat.sprites.append(sp)
        size = LABEL_SIZE if k == 0 or len(chosen) < 4 else 46.0
        w = text_word(nd.label, size, lab, sp.t + min(sp.draw, 1.0) * 0.7, lang, color=tone_color(nd.label),
                      max_width=(box[2] - box[0]) + (160 if len(chosen) < 4 else 30))
        if w:
            w.end = sp.end
            beat.words.append(w)
        memory.setdefault(nd.name, nd.label)
        prev, prev_block = sp, block
    chain = " → ".join(n.label or n.name for n in chosen)
    beat.objective = f"{beat.objective}; {chain}" if beat.objective else chain
