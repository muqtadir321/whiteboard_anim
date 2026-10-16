"""Scene composer: scene plan -> laid-out vector illustration -> SVG document.

The composer resolves each planned action into concrete drawings (characters,
objects, charts, diagrams, handwritten text), positions them in layout slots
without collisions, connects related elements with arrows and writes the
result as an SVG file whose <path> elements carry drawing-order metadata.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from . import RTL_CODES
from .character_generator import make_character, make_group
from .chart_generator import CHARTS
from .diagram_generator import DIAGRAMS
from .geometry import Element, Pen, Stroke
from .object_generator import OBJECTS, make_object
from .text_shaper import fit_text, is_arabic_char

DESIGN_W, DESIGN_H = 1080.0, 1920.0
DRAW_REGION = (50.0, 170.0, 1030.0, 1440.0)
CAPTION_REGION = (40.0, 1490.0, 1040.0, 1800.0)
SAFE_MARGIN = 40.0

SLOTS = {
    "left": (0.0, 0.18, 0.47, 0.86), "right": (0.53, 0.18, 1.0, 0.86), "center": (0.2, 0.24, 0.8, 0.76),
    "upper": (0.03, 0.12, 0.97, 0.58), "lower": (0.12, 0.6, 0.88, 1.0), "top": (0.06, 0.0, 0.94, 0.12),
    "bottom": (0.06, 0.88, 0.94, 1.0), "top_left": (0.0, 0.0, 0.46, 0.34), "top_right": (0.54, 0.0, 1.0, 0.34),
    "bottom_left": (0.0, 0.62, 0.46, 1.0), "bottom_right": (0.54, 0.62, 1.0, 1.0), "center_left": (0.0, 0.32, 0.46, 0.68),
    "center_right": (0.54, 0.32, 1.0, 0.68), "lower_left": (0.0, 0.42, 0.5, 1.0), "lower_right": (0.5, 0.42, 1.0, 1.0),
    "upper_center": (0.28, 0.12, 0.72, 0.3), "full": (0.02, 0.04, 0.98, 0.96),
}
TEXT_SIZES = {"title": 120.0, "number": 96.0, "label": 64.0}
MAX_SIZE = {"person": 640, "people_group": 520, "chart": 99999, "object": 380}
CHART_NAMES = set(CHARTS) | set(DIAGRAMS) | {"money_growth"}


@dataclass
class Style:
    colors: dict[str, str]
    stroke_width: float

    @classmethod
    def from_config(cls, cfg: dict) -> "Style":
        a = cfg["animation"]
        return cls(
            colors={
                "ink": a["primary_stroke"], "accent": a["accent_color"], "positive": a["positive_color"], "negative": a["negative_color"],
                "muted": "#8A8A8A", "skin": "#F3D2B3", "gold": "#E0A526", "brown": "#8B5A2B", "pink": "#F2A7B8", "paper": "#F5F0E1",
                "background": a["background"],
            },
            stroke_width=float(a["stroke_width"]),
        )

    def color(self, role: str | None) -> str:
        if not role:
            return self.colors["ink"]
        if role.startswith("#"):
            return role
        return self.colors.get(role, self.colors["ink"])


@dataclass
class Glyphs:
    contours: list[list[tuple[float, float]]]
    order_key: float  # reveal ordering (x position in writing direction)


@dataclass
class TextBlock:
    text: str
    lang: str
    color: str
    glyphs: list[Glyphs]
    bbox: tuple[float, float, float, float]
    rtl: bool
    missing: list[str] = field(default_factory=list)


@dataclass
class Item:
    id: str
    action: str
    obj: str
    strokes: list[Stroke]
    texts: list[TextBlock]
    bbox: tuple[float, float, float, float]
    anchor_char: int
    emphasis: bool = False
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)
    held: bool = False  # drawn inside another item's hand


@dataclass
class ComposedScene:
    scene_id: str
    items: list[Item]
    width: float
    height: float
    warnings: list[str] = field(default_factory=list)
    missing_glyphs: list[str] = field(default_factory=list)


RTL_FLOW_SCENES = {"cause_effect", "process", "transaction", "problem_solution", "comparison"}


def mirror_position(pos: str) -> str:
    if "left" in pos:
        return pos.replace("left", "right")
    if "right" in pos:
        return pos.replace("right", "left")
    return pos


def _slot_box(name: str) -> tuple[float, float, float, float]:
    fx0, fy0, fx1, fy1 = SLOTS.get(name, SLOTS["center"])
    x0, y0, x1, y1 = DRAW_REGION
    w, h = x1 - x0, y1 - y0
    return (x0 + fx0 * w, y0 + fy0 * h, x0 + fx1 * w, y0 + fy1 * h)


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


# ---------------------------------------------------------------------------
# Visual vocabulary
# ---------------------------------------------------------------------------

def build_visual(name: str, rng: random.Random, params: dict | None, detail: float = 1.0) -> tuple[Element, str]:
    """Return (element, size_class)."""
    params = dict(params or {})
    if name in ("person", "worker", "boss", "student", "teacher"):
        pose = params.get("pose", "stand")
        expr = params.get("expression", "happy")
        if name == "worker":
            el = make_character(rng, pose=pose, expression=expr, hair="cap", accessory="bag")
        elif name == "boss":
            el = make_character(rng, pose=pose, expression=expr, hair=rng.choice(["short", "bun"]), accessory="tie")
        elif name == "student":
            el = make_character(rng, pose=pose, expression=expr, hair=rng.choice(["spiky", "long", "short"]), accessory="bag")
        elif name == "teacher":
            el = make_character(rng, pose=pose, expression=expr, hair=rng.choice(["short", "bun", "long"]), accessory="badge")
        else:
            el = make_character(rng, pose=pose, expression=expr, accessory=rng.choice([None, None, "badge", "tie"]))
        return el, "person"
    if name == "people_group":
        return make_group(rng, count=params.get("count", 3)), "people_group"
    if name == "money_growth":
        el = Element("money_growth", 150, 120)
        el.extend(make_object("coin_stack", rng, count=3), dx=0, dy=50, s=1.0)
        el.extend(make_object("coin_stack", rng, count=5), dx=52, dy=14, s=1.0)
        el.extend(CHARTS["trend_arrow"](rng, direction="up"), dx=70, dy=-2, s=0.6)
        return el, "object"
    if name == "debt":
        el = Element("debt", 110, 90)
        el.extend(make_object("banknote", rng), dx=15, dy=0)
        el.extend(make_object("chain", rng), dx=5, dy=48)
        return el, "object"
    if name == "loss":
        el = Element("loss", 140, 110)
        el.extend(make_object("coin_stack", rng, count=4), dx=0, dy=20)
        el.extend(CHARTS["trend_arrow"](rng, direction="down"), dx=58, dy=10, s=0.65)
        return el, "object"
    if name == "price_tag":
        pen = Pen(rng, wobble=0.5, step=2.5)
        el = Element("price_tag", 100, 60)
        el.add(pen.polyline([(20, 4), (96, 4), (96, 56), (20, 56), (2, 30)], closed=True, fill="gold", fill_opacity=0.3))
        el.add(pen.circle(18, 30, 4))
        from .geometry import TextSpec
        el.texts.append(TextSpec("$", (40, 10, 80, 50), bold=True, lang="en"))
        return el, "object"
    if name == "desk_work":
        el = Element("desk_work", 140, 130)
        el.extend(make_character(rng, pose="hold", expression="happy", seated=True), dx=0, dy=6)
        pen = Pen(rng, wobble=0.5, step=2.5)
        el.add(pen.line((40, 82), (140, 82), width=1.2), pen.line((128, 82), (128, 128)), pen.line((50, 82), (50, 128)))
        el.extend(make_object("laptop", rng), dx=74, dy=46, s=0.55)
        el.add(pen.polyline([(8, 76), (14, 128)], width=1.0), pen.line((8, 78), (36, 78), width=1.0))
        return el, "object"
    if name in CHARTS:
        return CHARTS[name](rng, **params), "chart"
    if name in DIAGRAMS:
        return DIAGRAMS[name](rng, **params), "chart"
    if name in OBJECTS:
        return make_object(name, rng, **params), "object"
    raise KeyError(f"Unknown visual {name!r}")


class Composer:
    def __init__(self, cfg: dict, lang: str):
        self.cfg = cfg
        self.style = Style.from_config(cfg)
        self.lang = lang
        self.detail = cfg.get("render", {}).get("detail", 1.0)

    # -- text ------------------------------------------------------------
    def make_text(self, text: str, box, lang: str | None, color: str, bold: bool, start_size: float, align: str = "center", max_lines: int = 2) -> TextBlock:
        lang = lang or self.lang
        if all(not c.isalpha() for c in text) or (lang in RTL_CODES and not any(is_arabic_char(c) for c in text)):
            # Numbers/symbols, or Latin labels inside RTL scenes, use the Latin handwriting font.
            lang = "en" if not any(c.isalpha() for c in text) else ("roman_urdu" if lang in RTL_CODES else lang)
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        lines, size, lh = fit_text(text, lang, bw, bh, start_size, min_size=min(28.0, start_size), bold=bold, max_lines=max_lines)
        total_h = lh * len(lines)
        rtl = lang in RTL_CODES
        glyphs: list[Glyphs] = []
        missing: list[str] = []
        top = y0 + (bh - total_h) / 2
        gx0, gy0, gx1, gy1 = 1e9, 1e9, -1e9, -1e9
        for li, line in enumerate(lines):
            if align == "left":
                lx = x0
            elif align == "right":
                lx = x1 - line.width
            else:
                lx = x0 + (bw - line.width) / 2
            baseline = top + li * lh + (lh - line.height) / 2 + line.ascent
            missing.extend(line.missing_chars)
            for g in line.glyphs:
                cs = []
                for c in g.contours():
                    pts = [(lx + px, baseline + py) for px, py in c]
                    if len(pts) >= 2:
                        cs.append(pts)
                        for px, py in pts:
                            gx0, gy0, gx1, gy1 = min(gx0, px), min(gy0, py), max(gx1, px), max(gy1, py)
                if cs:
                    gx = lx + g.x
                    # writing order: line by line, then along the writing direction
                    key = li * 1e5 + (-(gx) if rtl else gx)
                    glyphs.append(Glyphs(cs, key))
        glyphs.sort(key=lambda g: g.order_key)
        bbox = (gx0, gy0, gx1, gy1) if glyphs else (x0, y0, x0, y0)
        return TextBlock(text, lang, self.style.color(color), glyphs, bbox, rtl, missing)

    # -- placement -------------------------------------------------------
    def _fit(self, el: Element, box, size_class: str, scale: float) -> tuple[float, float, float]:
        x0, y0, x1, y1 = box
        bw, bh = (x1 - x0) * 0.92, (y1 - y0) * 0.92
        s = min(bw / el.width, bh / el.height)
        cap = MAX_SIZE.get(size_class, MAX_SIZE["object"])
        if size_class in ("person", "people_group"):
            s = min(s, cap / el.height)
        elif size_class == "object":
            s = min(s, cap / max(el.width, el.height))
        # Planner scales are relative importance; soften them so secondary items stay legible.
        s *= 0.45 + 0.55 * scale
        w, h = el.width * s, el.height * s
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if size_class in ("person", "people_group"):
            # people stand on the bottom of their slot
            return s, cx - w / 2, y1 - h - (y1 - y0) * 0.04
        return s, cx - w / 2, cy - h / 2

    def _resolve_collision(self, bbox, placed: list[Item], box) -> tuple[float, float, float]:
        """Return (dx, dy, extra_scale) that reduces overlap with already placed items."""
        best = (0.0, 0.0, 1.0)
        best_score = sum(_overlap(bbox, p.bbox) for p in placed)
        if best_score <= 0.05 * _area(bbox):
            return best
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        candidates = []
        for sc in (1.0, 0.85, 0.72, 0.6):
            for dx in (0, -0.15, 0.15, -0.3, 0.3):
                for dy in (0, -0.15, 0.15, -0.3, 0.3):
                    candidates.append((dx * w, dy * h, sc))
        rx0, ry0, rx1, ry1 = DRAW_REGION
        for dx, dy, sc in candidates:
            cx = (bbox[0] + bbox[2]) / 2 + dx
            cy = (bbox[1] + bbox[3]) / 2 + dy
            nb = (cx - w * sc / 2, cy - h * sc / 2, cx + w * sc / 2, cy + h * sc / 2)
            if nb[0] < rx0 - 10 or nb[2] > rx1 + 10 or nb[1] < ry0 - 10 or nb[3] > ry1 + 10:
                continue
            score = sum(_overlap(nb, p.bbox) for p in placed) + (1 - sc) * 0.15 * _area(bbox)
            if score < best_score:
                best_score, best = score, (dx, dy, sc)
        return best

    def _transform_element(self, el: Element, s: float, ox: float, oy: float, mirror: bool = False) -> tuple[list[Stroke], list, dict]:
        def tx(p):
            x, y = p
            if mirror:
                x = el.width - x
            return (ox + x * s, oy + y * s)

        strokes = [
            Stroke([tx(p) for p in st.points], self.style.color(st.color), st.width * self.style.stroke_width * (0.85 + 0.15 * min(1.5, s)), st.closed,
                   self.style.color(st.fill) if st.fill else None, st.fill_opacity, st.role)
            for st in el.strokes
            if self.detail >= 0.8 or st.role != "hatch" or el.strokes.index(st) % 2 == 0
        ]
        texts = []
        for t in el.texts:
            x0, y0, x1, y1 = t.box
            a, b = tx((x0, y0)), tx((x1, y1))
            box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            texts.append(self.make_text(t.text, box, t.lang, t.color, t.bold, (box[3] - box[1]) * t.size_hint, t.align, max_lines=2))
        anchors = {k: tx(v) for k, v in el.anchors.items()}
        return strokes, texts, anchors

    def compose(self, scene: dict) -> ComposedScene:
        seed = self.cfg["animation"]["seed"]
        h = hashlib.sha256(f"{seed}|{scene['scene_id']}|{scene['narration_text']}".encode()).hexdigest()
        rng = random.Random(int(h[:12], 16))
        items: list[Item] = []
        by_id: dict[str, Item] = {}
        elements: dict[str, tuple[Element, str]] = {}
        warnings: list[str] = []
        actions = sorted(scene["visual_actions"], key=lambda a: a["draw_order"])
        if self.lang in RTL_CODES and scene.get("scene_type") in RTL_FLOW_SCENES:
            # Right-to-left readers follow cause -> effect and step -> step from right to left.
            actions = [{**a, "position": mirror_position(a["position"])} for a in actions]
        # Drawings and charts first (they own the layout); text and connectors adapt to them.
        order = [a for a in actions if a["action"] in ("draw", "duplicate")] + [a for a in actions if a["action"] == "write"] + \
                [a for a in actions if a["action"] in ("connect", "highlight", "cross_out")]
        mirror_people = rng.random() < 0.5
        for a in order:
            kind = a["action"]
            try:
                if kind == "draw":
                    item = self._place_drawing(a, rng, items, by_id, elements, mirror_people)
                elif kind == "duplicate":
                    src = elements.get(a["source"])
                    if not src:
                        raise KeyError(f"duplicate source {a['source']} missing")
                    item = self._place_element(a, src[0], src[1], _slot_box(a["position"]), items, False)
                elif kind == "write":
                    item = self._place_text(a, items)
                elif kind == "connect":
                    item = self._connect(a, by_id, rng)
                else:
                    item = self._mark(a, by_id, rng)
            except KeyError as exc:
                warnings.append(f"{scene['scene_id']}/{a['id']}: {exc}")
                continue
            if item is None:
                continue
            items.append(item)
            by_id[a["id"]] = item
        # Restore narrative drawing order for animation.
        rank = {a["id"]: a["draw_order"] for a in actions}
        items.sort(key=lambda it: rank.get(it.id, 999))
        missing = sorted({c for it in items for t in it.texts for c in t.missing})
        return ComposedScene(scene["scene_id"], items, DESIGN_W, DESIGN_H, warnings, missing)

    def _place_drawing(self, a, rng, items, by_id, elements, mirror_people) -> Item:
        el, size_class = build_visual(a["object"], rng, a.get("params"), self.detail)
        elements[a["id"]] = (el, size_class)
        pos = a["position"]
        if pos.startswith("in_hand:"):
            holder = by_id.get(pos.split(":", 1)[1])
            if holder is None:
                raise KeyError("holder not placed")
            hand = holder.anchors.get("hand_right") or holder.anchors.get("center")
            hh = holder.bbox[3] - holder.bbox[1]
            target = hh * 0.30 * (a.get("scale", 0.45) / 0.45)
            s = target / max(el.width, el.height)
            ox = hand[0] - el.width * s / 2
            oy = hand[1] - el.height * s * 0.65
            strokes, texts, anchors = self._transform_element(el, s, ox, oy)
            bbox = (ox, oy, ox + el.width * s, oy + el.height * s)
            return Item(a["id"], "draw", a["object"], strokes, texts, bbox, a.get("anchor_char", 0), a.get("emphasis", False), anchors, held=True)
        mirror = False
        if size_class == "person":
            # Face toward the centre of the board.
            box = _slot_box(pos)
            cx = (box[0] + box[2]) / 2
            mirror = cx > DESIGN_W / 2
        return self._place_element(a, el, size_class, _slot_box(pos), items, mirror)

    def _place_element(self, a, el, size_class, box, items, mirror) -> Item:
        s, ox, oy = self._fit(el, box, size_class, a.get("scale", 1.0))
        bbox = (ox, oy, ox + el.width * s, oy + el.height * s)
        dx, dy, sc = self._resolve_collision(bbox, items, box)
        if (dx, dy, sc) != (0.0, 0.0, 1.0):
            cx, cy = (bbox[0] + bbox[2]) / 2 + dx, (bbox[1] + bbox[3]) / 2 + dy
            s *= sc
            ox, oy = cx - el.width * s / 2, cy - el.height * s / 2
            bbox = (ox, oy, ox + el.width * s, oy + el.height * s)
        bbox = self._clamp(bbox)
        ox, oy = bbox[0], bbox[1]
        strokes, texts, anchors = self._transform_element(el, s, ox, oy, mirror)
        return Item(a["id"], a["action"], a.get("object") or el.name, strokes, texts, bbox, a.get("anchor_char", 0), a.get("emphasis", False), anchors)

    def _clamp(self, bbox):
        rx0, ry0, rx1, ry1 = DRAW_REGION
        dx = max(0.0, rx0 - bbox[0]) - max(0.0, bbox[2] - rx1)
        dy = max(0.0, ry0 - bbox[1]) - max(0.0, bbox[3] - ry1)
        return (bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy)

    def _place_text(self, a, items) -> Item:
        box = _slot_box(a["position"])
        style = a.get("style", "label")
        size = TEXT_SIZES.get(style, 64.0)
        # Shrink the box away from existing drawings if they overlap heavily.
        best_box = box
        best = sum(_overlap(box, p.bbox) for p in items)
        if best > 0.2 * _area(box):
            for alt in self._text_alternatives(a["position"]):
                ab = _slot_box(alt)
                ov = sum(_overlap(ab, p.bbox) for p in items) + sum(_overlap(ab, p.bbox) for p in items if p.action == "write") * 2
                if ov < best:
                    best, best_box = ov, ab
        tb = self.make_text(a["text"], best_box, None, a.get("color", "ink"), style != "label", min(size, (best_box[3] - best_box[1]) * 0.8))
        # If the text still collides, nudge it vertically inside the drawing region.
        bbox = tb.bbox
        if any(_overlap(bbox, p.bbox) > 0.3 * _area(bbox) for p in items):
            for dy in (60, -60, 120, -120, 180, -180):
                nb = (bbox[0], bbox[1] + dy, bbox[2], bbox[3] + dy)
                if nb[1] >= DRAW_REGION[1] and nb[3] <= DRAW_REGION[3] and not any(_overlap(nb, p.bbox) > 0.3 * _area(nb) for p in items):
                    tb = self._shift_text(tb, 0, dy)
                    break
        return Item(a["id"], "write", "text", [], [tb], tb.bbox, a.get("anchor_char", 0), a.get("emphasis", False))

    @staticmethod
    def _text_alternatives(pos: str) -> list[str]:
        table = {
            "top": ["upper_center", "bottom", "center"], "bottom": ["lower", "top", "center"], "upper_center": ["top", "center", "bottom"],
            "top_left": ["center_left", "bottom_left", "top"], "top_right": ["center_right", "bottom_right", "top"],
            "bottom_left": ["center_left", "bottom", "top_left"], "bottom_right": ["center_right", "bottom", "top_right"],
            "center": ["upper_center", "bottom", "top"], "center_left": ["top_left", "bottom_left"], "center_right": ["top_right", "bottom_right"],
            "lower": ["bottom", "center"],
        }
        return table.get(pos, ["top", "bottom", "center"])

    @staticmethod
    def _shift_text(tb: TextBlock, dx: float, dy: float) -> TextBlock:
        glyphs = [Glyphs([[(x + dx, y + dy) for x, y in c] for c in g.contours], g.order_key) for g in tb.glyphs]
        b = tb.bbox
        return TextBlock(tb.text, tb.lang, tb.color, glyphs, (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy), tb.rtl, tb.missing)

    def _connect(self, a, by_id, rng) -> Item | None:
        src, dst = by_id.get(a["source"]), by_id.get(a["target"])
        if not src or not dst:
            raise KeyError("connect endpoints missing")
        if _overlap(src.bbox, dst.bbox) > 0.5 * min(_area(src.bbox), _area(dst.bbox)):
            return None  # e.g. object held in hand: an arrow would be meaningless
        p0 = self._edge_point(src.bbox, dst.bbox)
        p1 = self._edge_point(dst.bbox, src.bbox)
        if math.dist(p0, p1) < 30:
            return None
        pen = Pen(rng, wobble=1.0, step=4.0)
        curve = rng.choice([-0.18, 0.18, 0.1, -0.1])
        strokes = pen.arrow(p0, p1, head=34, curve=curve, color=a.get("color", "accent"), width=1.0)
        strokes = [Stroke(s.points, self.style.color(s.color), self.style.stroke_width * 1.1, False) for s in strokes]
        xs = [p[0] for s in strokes for p in s.points]
        ys = [p[1] for s in strokes for p in s.points]
        return Item(a["id"], "connect", "arrow", strokes, [], (min(xs), min(ys), max(xs), max(ys)), a.get("anchor_char", 0))

    @staticmethod
    def _edge_point(a, b, pad: float = 12.0):
        acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
        bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        dx, dy = bcx - acx, bcy - acy
        hw, hh = (a[2] - a[0]) / 2 + pad, (a[3] - a[1]) / 2 + pad
        if dx == 0 and dy == 0:
            return (acx, acy)
        t = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
        return (acx + dx * t, acy + dy * t)

    def _mark(self, a, by_id, rng) -> Item:
        tgt = by_id.get(a["target"])
        if not tgt:
            raise KeyError("mark target missing")
        pen = Pen(rng, wobble=2.0, step=5.0)
        x0, y0, x1, y1 = tgt.bbox
        color = self.style.color(a.get("color", "accent"))
        if a["action"] == "highlight":
            st = pen.ellipse((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + 30, (y1 - y0) / 2 + 30)
            strokes = [Stroke(st.points, color, self.style.stroke_width * 0.9, True)]
        else:
            strokes = [Stroke(pen.line((x0, y0), (x1, y1)).points, color, self.style.stroke_width * 1.3),
                       Stroke(pen.line((x1, y0), (x0, y1)).points, color, self.style.stroke_width * 1.3)]
        xs = [p[0] for s in strokes for p in s.points]
        ys = [p[1] for s in strokes for p in s.points]
        return Item(a["id"], a["action"], a["action"], strokes, [], (min(xs), min(ys), max(xs), max(ys)), a.get("anchor_char", 0))


# ---------------------------------------------------------------------------
# SVG output
# ---------------------------------------------------------------------------

def _d(points, closed=False) -> str:
    if not points:
        return ""
    parts = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    parts.extend(f"L{x:.1f} {y:.1f}" for x, y in points[1:])
    if closed:
        parts.append("Z")
    return " ".join(parts)


def to_svg(composed: ComposedScene, style: Style) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(composed.width)}" height="{int(composed.height)}" viewBox="0 0 {int(composed.width)} {int(composed.height)}" data-scene="{composed.scene_id}">',
        f'<rect x="0" y="0" width="{int(composed.width)}" height="{int(composed.height)}" fill="{style.colors["background"]}"/>',
    ]
    for order, it in enumerate(composed.items, 1):
        bb = ",".join(f"{v:.1f}" for v in it.bbox)
        out.append(f'<g id="{it.id}" class="item {it.action}" data-object="{escape(it.obj)}" data-order="{order}" data-anchor-char="{it.anchor_char}" data-emphasis="{int(it.emphasis)}" data-bbox="{bb}">')
        for i, st in enumerate(it.strokes):
            attrs = f'stroke="{st.color}" stroke-width="{st.width:.2f}" fill="none" stroke-linecap="round" stroke-linejoin="round"'
            extra = f' data-fill="{st.fill}" data-fill-opacity="{st.fill_opacity:.2f}"' if st.fill else ""
            out.append(f'<path class="stroke {st.role}" data-index="{i}" d="{_d(st.points, st.closed)}" {attrs}{extra}/>')
        for t in it.texts:
            tbb = ",".join(f"{v:.1f}" for v in t.bbox)
            out.append(f'<g class="text" data-text="{escape(t.text, {chr(34): "&quot;"})}" data-lang="{t.lang}" data-dir="{"rtl" if t.rtl else "ltr"}" data-bbox="{tbb}">')
            for gi, g in enumerate(t.glyphs):
                d = " ".join(_d(c, True) for c in g.contours)
                out.append(f'<path class="glyph" data-index="{gi}" d="{d}" fill="{t.color}" fill-rule="nonzero"/>')
            out.append("</g>")
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)
