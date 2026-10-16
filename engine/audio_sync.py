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

TRANSITION_SECONDS = {"none": 0.0, "slide": 0.45, "erase": 0.5, "fade": 0.4}


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
        return float(w.get("start", start)), "word_timestamps"
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

    return sorted(items, key=lambda it: (effective(it.id), it.order))


def build_timeline(scene: dict, items: list[DrawItem], cfg: dict, pen_rest: tuple[float, float]) -> SceneTimeline:
    start, end = float(scene["start_seconds"]), float(scene["end_seconds"])
    transition = scene.get("transition", "none")
    tr = min(TRANSITION_SECONDS.get(transition, 0.0), (end - start) * 0.2)
    tl = SceneTimeline(scene["scene_id"], start, end, transition, start + tr)
    tl.captions = caption_cues(scene)
    duration = end - start
    hold = min(0.9, max(0.25, duration * 0.18))
    w0 = start + tr + 0.05
    w1 = max(w0 + 0.3, end - hold)
    window = w1 - w0

    # Total work at nominal speed (drawing + pen travel).
    pen = pen_rest
    work = 0.0
    for it in items:
        for op in it.ops:
            work += op.cost + travel_cost(pen, op.start_point)
            pen = op.end_point
        if it.emphasis:
            work += 0.25
    speed = float(cfg["animation"]["drawing_speed"])
    if work / speed > window * 0.92:
        speed = work / (window * 0.92)
        tl.compressed = speed > 4 * float(cfg["animation"]["drawing_speed"])
    elif work / speed < window * 0.5:
        # Very short drawings in a long sentence: draw a little more slowly (natural pace).
        speed = max(speed * 0.6, work / (window * 0.6)) if work > 0 else speed
    tl.speed_factor = speed

    sources = set()
    t = w0
    pen = pen_rest
    remaining = work
    for ii, it in enumerate(items):
        item_work = sum(op.cost for op in it.ops) + (0.25 if it.emphasis else 0)
        anchor_t, src = char_to_time(scene, it.anchor_char)
        sources.add(src)
        latest = w1 - remaining / speed
        target = anchor_t - 0.25
        if target > t:
            t = min(target, max(t, latest))
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
        remaining -= item_work
    tl.drawing_end = t
    tl.timing_source = "word_timestamps" if sources == {"word_timestamps"} else "estimated"
    return tl
