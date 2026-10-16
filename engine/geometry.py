"""Vector primitives used by all illustration generators.

Every drawable is an ordered list of Strokes (polylines in local coordinates).
Generators emit strokes in natural drawing order; the composer scales them into
the canvas and writes them as SVG paths, which the renderer then draws
progressively. Small deterministic wobble gives a hand-drawn feel.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass
class Stroke:
    points: list[Point]
    color: str = "ink"  # ink | accent | positive | negative | muted | skin
    width: float = 1.0  # multiple of the configured stroke width
    closed: bool = False
    fill: str | None = None  # colour role filled once the outline is complete
    fill_opacity: float = 1.0
    role: str = "line"  # line | detail | hatch

    def length(self) -> float:
        return polyline_length(self.points)

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class TextSpec:
    text: str
    box: tuple[float, float, float, float]  # local x0, y0, x1, y1
    color: str = "ink"
    bold: bool = False
    size_hint: float = 1.0  # fraction of box height used as the starting font size
    lang: str | None = None  # None -> scene language (labels always use scene language unless numeric)
    align: str = "center"


@dataclass
class Element:
    name: str
    width: float
    height: float
    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextSpec] = field(default_factory=list)
    anchors: dict[str, Point] = field(default_factory=dict)

    def add(self, *strokes: Stroke) -> "Element":
        self.strokes.extend(strokes)
        return self

    def extend(self, other: "Element", dx: float = 0, dy: float = 0, s: float = 1.0) -> None:
        for st in other.strokes:
            self.strokes.append(
                Stroke([(dx + x * s, dy + y * s) for x, y in st.points], st.color, st.width, st.closed, st.fill, st.fill_opacity, st.role)
            )
        for t in other.texts:
            x0, y0, x1, y1 = t.box
            self.texts.append(TextSpec(t.text, (dx + x0 * s, dy + y0 * s, dx + x1 * s, dy + y1 * s), t.color, t.bold, t.size_hint, t.lang, t.align))
        for k, (x, y) in other.anchors.items():
            self.anchors.setdefault(k, (dx + x * s, dy + y * s))


def polyline_length(points: list[Point]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


class Pen:
    """Hand-drawn primitive factory with deterministic jitter."""

    def __init__(self, rng: random.Random, wobble: float = 0.6, step: float = 3.0):
        self.rng = rng
        self.wobble = wobble
        self.step = step

    # -- helpers ---------------------------------------------------------
    def _jitter(self, pts: list[Point], amount: float | None = None) -> list[Point]:
        a = self.wobble if amount is None else amount
        if a <= 0 or len(pts) < 3:
            return pts
        # Low-frequency noise: two random sine components along the stroke.
        f1, f2 = self.rng.uniform(0.5, 1.5), self.rng.uniform(2.0, 3.5)
        p1, p2 = self.rng.uniform(0, 6.28), self.rng.uniform(0, 6.28)
        n = len(pts)
        out = []
        for i, (x, y) in enumerate(pts):
            t = i / (n - 1)
            if i == 0 or i == n - 1:
                env = 0.25
            else:
                env = 1.0
            # normal direction
            j = min(n - 1, i + 1)
            k = max(0, i - 1)
            dx, dy = pts[j][0] - pts[k][0], pts[j][1] - pts[k][1]
            ln = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / ln, dx / ln
            off = a * env * (0.7 * math.sin(f1 * t * 6.28 + p1) + 0.3 * math.sin(f2 * t * 6.28 + p2))
            out.append((x + nx * off, y + ny * off))
        return out

    def _sample_line(self, p0: Point, p1: Point) -> list[Point]:
        d = math.dist(p0, p1)
        n = max(2, int(d / self.step) + 1)
        return [(p0[0] + (p1[0] - p0[0]) * i / (n - 1), p0[1] + (p1[1] - p0[1]) * i / (n - 1)) for i in range(n)]

    # -- primitives ------------------------------------------------------
    def line(self, p0: Point, p1: Point, **kw) -> Stroke:
        return Stroke(self._jitter(self._sample_line(p0, p1)), **kw)

    def polyline(self, pts: list[Point], closed: bool = False, **kw) -> Stroke:
        seq = list(pts) + ([pts[0]] if closed else [])
        out: list[Point] = []
        for a, b in zip(seq, seq[1:]):
            seg = self._sample_line(a, b)
            out.extend(seg if not out else seg[1:])
        return Stroke(self._jitter(out, self.wobble * 0.6), closed=closed, **kw)

    def rect(self, x0: float, y0: float, x1: float, y1: float, r: float = 0.0, **kw) -> Stroke:
        if r <= 0:
            return self.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True, **kw)
        pts: list[Point] = []
        corners = [(x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)]
        pts.append((x0 + r, y0))
        for cx, cy, a0 in corners:
            for i in range(0, 7):
                a = math.radians(a0 + 90 * i / 6)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        pts.append((x0 + r, y0))
        out: list[Point] = []
        for a, b in zip(pts, pts[1:]):
            seg = self._sample_line(a, b)
            out.extend(seg if not out else seg[1:])
        return Stroke(self._jitter(out, self.wobble * 0.6), closed=True, **kw)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, start: float | None = None, sweep: float = 360.0, overshoot: float = 12.0, **kw) -> Stroke:
        if start is None:
            start = self.rng.uniform(-120, -60)
        total = sweep + (overshoot if sweep >= 360 else 0)
        circ = math.pi * (rx + ry) * total / 360.0
        n = max(12, int(circ / self.step))
        pts = []
        for i in range(n + 1):
            a = math.radians(start + total * i / n)
            # radius drifts slightly so the overshoot doesn't overlap exactly
            drift = 1.0 + 0.03 * (i / n) if sweep >= 360 else 1.0
            pts.append((cx + rx * drift * math.cos(a), cy + ry * drift * math.sin(a)))
        return Stroke(self._jitter(pts, self.wobble * 0.5), closed=sweep >= 360, **kw)

    def circle(self, cx: float, cy: float, r: float, **kw) -> Stroke:
        return self.ellipse(cx, cy, r, r, **kw)

    def dot(self, cx: float, cy: float, r: float = 1.6, **kw) -> Stroke:
        pts = [(cx + r * math.cos(a / 8 * 6.283), cy + r * math.sin(a / 8 * 6.283)) for a in range(9)]
        kw.setdefault("fill", kw.get("color", "ink"))
        return Stroke(pts, closed=True, role="detail", **kw)

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float, **kw) -> Stroke:
        return self.ellipse(cx, cy, r, r, start=a0, sweep=a1 - a0, overshoot=0, **kw)

    def bezier(self, p0: Point, p1: Point, p2: Point, p3: Point | None = None, **kw) -> Stroke:
        pts = []
        approx = math.dist(p0, p1) + math.dist(p1, p2) + (math.dist(p2, p3) if p3 else 0)
        n = max(8, int(approx / self.step))
        for i in range(n + 1):
            t = i / n
            mt = 1 - t
            if p3 is None:
                x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
                y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
            else:
                x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
                y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
            pts.append((x, y))
        return Stroke(self._jitter(pts, self.wobble * 0.5), **kw)

    def arrow(self, p0: Point, p1: Point, head: float = 10.0, curve: float = 0.0, **kw) -> list[Stroke]:
        if curve:
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            ctrl = (mx - dy * curve, my + dx * curve)
            shaft = self.bezier(p0, ctrl, p1, **kw)
            tail = shaft.points[-4] if len(shaft.points) > 4 else p0
        else:
            shaft = self.line(p0, p1, **kw)
            tail = p0
        ang = math.atan2(p1[1] - tail[1], p1[0] - tail[0])
        h1 = (p1[0] - head * math.cos(ang - 0.45), p1[1] - head * math.sin(ang - 0.45))
        h2 = (p1[0] - head * math.cos(ang + 0.45), p1[1] - head * math.sin(ang + 0.45))
        return [shaft, self.polyline([h1, p1, h2], **kw)]

    def hatch(self, x0: float, y0: float, x1: float, y1: float, spacing: float = 8.0, angle: float = 45.0, **kw) -> list[Stroke]:
        """Diagonal hatch lines clipped to a rectangle (used to shade bars)."""
        out = []
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return out
        run = h / math.tan(math.radians(angle))
        kw.setdefault("role", "hatch")
        kw.setdefault("width", 0.55)
        c = -run
        while c < w:
            t0 = max(0.0, -c / run)
            t1 = min(1.0, (w - c) / run)
            if t1 > t0:
                a = (x0 + c + t0 * run, y1 - t0 * h)
                b = (x0 + c + t1 * run, y1 - t1 * h)
                if math.dist(a, b) > 2:
                    out.append(Stroke(self._sample_line(a, b), **kw))
            c += spacing
        return out
