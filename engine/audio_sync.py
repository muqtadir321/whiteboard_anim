"""Audio/visual synchronisation: turns scene timing + drawing program into a
frame-accurate timeline of strokes, pen travel, captions and transitions.

Drawing actions start near the moment their trigger word is spoken
(`anchor_char` -> time). When word timestamps exist they are used; otherwise
time is interpolated from character position inside the sentence and the
timeline is marked as estimated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .svg_draw_order import DrawItem, travel_cost

TRANSITION_SECONDS = {"none": 0.0, "slide": 0.3, "erase": 0.3, "fade": 0.25}


@dataclass
class OpEvent:
    item_index: int
    op_index: int
    t0: float
    t1: float


@dataclass
class TravelEvent:
    p0: tuple[float, float]
    p1: tuple[float, float]
    t0: float
    t1: float


@dataclass
class CaptionCue:
    text: str
    start: float
    end: float


@dataclass
class SceneTimeline:
    scene_id: str
    start: float
    end: float
    transition: str
    transition_end: float
    transition_start: float = 0.0
    ops: list[OpEvent] = field(default_factory=list)
    travel: list[TravelEvent] = field(default_factory=list)
    captions: list[CaptionCue] = field(default_factory=list)
    speed_factor: float = 1.0
    drawing_end: float = 0.0
    timing_source: str = "estimated"
    compressed: bool = False

    def summary(self) -> dict:
        return {
            "scene_id": self.scene_id, "start": round(self.start, 3), "end": round(self.end, 3), "transition": self.transition,
            "drawing_end": round(self.drawing_end, 3), "speed_factor": round(self.speed_factor, 3), "timing_source": self.timing_source,
            "compressed": self.compressed, "operations": len(self.ops), "captions": [c.__dict__ for c in self.captions],
        }


def char_to_time(scene: dict, char_index: int) -> tuple[float, str]:
    text = scene["narration_text"]
    start, end = scene["start_seconds"], scene["end_seconds"]
    words = scene.get("words") or []
    if words:
        # Map the character offset to the n-th word of the narration, then to its timestamp.
        tokens = [m.start() for m in re.finditer(r"\S+", text)]
        idx = sum(1 for s in tokens if s <= char_index) - 1
        idx = max(0, min(len(words) - 1, int(round(idx * len(words) / max(1, len(tokens))))))
        w = words[idx]
        return float(w.get("start", start)), ("estimated" if w.get("estimated") else "word_timestamps")
    frac = min(1.0, max(0.0, char_index / max(1, len(text))))
    return start + frac * (end - start) * 0.92, "estimated"


def split_caption(text: str, max_words: int = 6, max_chars: int = 30) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    cur: list[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        if cur and (len(cur) >= max_words or len(cand) > max_chars):
            chunks.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
        # Break after clause punctuation to keep phrases natural.
        if w[-1:] in ",;:،" and len(cur) >= 3:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    # Avoid a dangling one-word final cue.
    if len(chunks) >= 2 and len(chunks[-1].split()) == 1 and len(chunks[-2]) + len(chunks[-1]) < max_chars + 8:
        last = chunks.pop()
        chunks[-1] = chunks[-1] + " " + last
    return chunks


def caption_cues(scene: dict) -> list[CaptionCue]:
    text = scene["caption_text"]
    start, end = scene["start_seconds"], scene["end_seconds"]
    chunks = split_caption(text)
    words = scene.get("words") or []
    cues: list[CaptionCue] = []
    if words and sum(len(c.split()) for c in chunks) == len(words):
        i = 0
        for c in chunks:
            n = len(c.split())
            cues.append(CaptionCue(c, float(words[i]["start"]), float(words[i + n - 1]["end"])))
            i += n
    else:
        total = sum(len(c) for c in chunks) or 1
        t = start
        for c in chunks:
            d = (end - start) * len(c) / total
            cues.append(CaptionCue(c, t, t + d))
            t += d
    # Make cues contiguous inside the scene so captions never flash off mid-sentence.
    # (each cue lasts until the next one starts, so the next cue begins exactly on its first word)
    for a, b in zip(cues, cues[1:]):
        b.start = max(a.start, b.start)
        a.end = b.start
    if cues:
        cues[0].start = start
        cues[-1].end = end
    return cues


def order_by_mention(scene: dict, items: list[DrawItem]) -> list[DrawItem]:
    """Draw items in the order their concepts are spoken, respecting dependencies
    (held objects after their holder, arrows and marks after their endpoints)."""
    actions = {a["id"]: a for a in scene.get("visual_actions", [])}
    eff: dict[str, int] = {}

    def effective(iid: str, depth: int = 0) -> int:
        if iid in eff:
            return eff[iid]
        a = actions.get(iid, {})
        val = int(a.get("anchor_char", 0))
        deps = [a.get("source"), a.get("target")]
        pos = a.get("position", "")
        if pos.startswith("in_hand:"):
            deps.append(pos.split(":", 1)[1])
        for d in deps:
            if d and d in actions and depth < 10:
                val = max(val, effective(d, depth + 1))
        eff[iid] = val
        return val

    # Within the same spoken moment, written words/numbers come first so they appear exactly
    # when said; the related picture is drawn right after.
    return sorted(items, key=lambda it: (effective(it.id), 0 if it.obj == "text" else 1, it.order))


def build_timeline(scene: dict, items: list[DrawItem], cfg: dict, pen_rest: tuple[float, float]) -> SceneTimeline:
    start, end = float(scene["start_seconds"]), float(scene["end_seconds"])
    transition = scene.get("transition", "none")
    tr = min(TRANSITION_SECONDS.get(transition, 0.0), (end - start) * 0.2)
    tl = SceneTimeline(scene["scene_id"], start, end, transition, start + tr, start)
    tl.captions = caption_cues(scene)
    duration = end - start
    hold = min(0.9, max(0.25, duration * 0.18))
    w0 = start + 0.05
    w1 = max(w0 + 0.3, end - hold)
    window = w1 - w0

    # Each item gets a deadline: it must start when its trigger word is spoken. Items are
    # drawn in spoken order; an item that would run into the next item's moment is drawn
    # faster, so later items (numbers, labels) never lag behind the narration.
    base_speed = float(cfg["animation"]["drawing_speed"])
    lead = 0.12  # start a hair before the word so the stroke is visibly under way
    targets = []
    sources = set()
    for it in items:
        anchor_t, src = char_to_time(scene, it.anchor_char)
        sources.add(src)
        targets.append(min(w1 - 0.2, max(w0, anchor_t - lead)))
    for k in range(1, len(targets)):
        targets[k] = max(targets[k], targets[k - 1])  # spoken order is non-decreasing
    pen = pen_rest
    t = w0
    speeds = []
    natural = []
    for it in items:
        natural.append(sum(op.cost for op in it.ops) + (0.25 if it.emphasis else 0.0) + 0.05)
    for ii, it in enumerate(items):
        # the pen travels to the item beforehand, so ink touches the board on the word
        approach = travel_cost(pen, it.ops[0].start_point) / base_speed if it.ops else 0.0
        # Written words/numbers appear exactly on their word; pictures may be started a
        # little early (like an illustrator anticipating) when the pen is free.
        anticipation = 0.0 if it.obj == "text" else 1.2
        start_t = max(t, targets[ii] - approach - anticipation)
        # Items mentioned at (almost) the same moment share the time until the next mention.
        j = ii + 1
        while j < len(items) and targets[j] <= targets[ii] + 0.05:
            j += 1
        if j < len(items):
            nxt = items[j]
            end_pt = items[j - 1].ops[-1].end_point if items[j - 1].ops else pen
            next_approach = travel_cost(end_pt, nxt.ops[0].start_point) / base_speed if nxt.ops else 0.0
            group_end = targets[j] - next_approach
        else:
            group_end = w1
        group_work = sum(natural[ii:j])
        if j - ii > 1:
            # several items share this moment: each gets its share, but never more than its
            # natural drawing time, so the following item (often its label) is not kept waiting
            share = max(0.0, group_end - start_t) * natural[ii] / max(1e-6, group_work)
            slot_end = start_t + min(share, natural[ii] / base_speed)
        else:
            slot_end = group_end
        # natural work for this item, including pen travel
        p = pen
        work = 0.0
        for op in it.ops:
            work += op.cost + travel_cost(p, op.start_point)
            p = op.end_point
        if it.emphasis:
            work += 0.25
        slot = max(0.0, slot_end - start_t)
        speed = base_speed
        if work / speed > slot:
            # squeeze into the slot so the next spoken item is not delayed
            # (strokes stay visible: at most 10x faster and never under 0.2 s)
            needed = max(0.2, slot)
            speed = min(base_speed * 10.0, max(base_speed, work / needed))
        elif slot > 0 and work / speed < slot * 0.45 and j < len(items) and ii == j - 1:
            # lots of time before the next mention: draw a little more calmly
            # (never while other items of the same moment are still waiting)
            speed = max(base_speed * 0.8, work / (slot * 0.6))
        speeds.append(speed)
        t = start_t
        for oi, op in enumerate(it.ops):
            tc = travel_cost(pen, op.start_point) / speed
            if tc > 0:
                tl.travel.append(TravelEvent(pen, op.start_point, t, t + tc))
                t += tc
            d = op.cost / speed
            tl.ops.append(OpEvent(ii, oi, t, t + d))
            t += d
            pen = op.end_point
        if it.emphasis:
            t += 0.25 / speed
    tl.speed_factor = max(speeds) if speeds else base_speed
    tl.compressed = tl.speed_factor >= base_speed * 9.9
    tl.drawing_end = t
    # The previous scene stays on screen until just before this scene's first stroke, so the
    # board is never left blank; the transition runs into that first stroke.
    if tl.ops and tr > 0:
        first = min(e.t0 for e in tl.ops)
        tl.transition_end = max(start + 0.05, first)
        tl.transition_start = max(start, tl.transition_end - tr)
    tl.timing_source = "word_timestamps" if sources == {"word_timestamps"} else "estimated"
    return tl
