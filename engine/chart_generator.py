"""Charts and numerical illustrations: bar, line and pie charts, trend arrows,
percentage badges and before/after comparisons.

Charts only carry numeric labels that come from the narration. When the
narration describes a trend without numbers, the chart shows the shape of the
trend with unlabelled axes rather than inventing data.
"""

from __future__ import annotations

import math
import random

from .geometry import Element, Pen, TextSpec


def axes(pen: Pen, el: Element, x0: float, y0: float, x1: float, y1: float) -> None:
    el.add(pen.line((x0, y0), (x0, y1), width=1.1))
    el.add(pen.line((x0, y1), (x1, y1), width=1.1))
    el.add(pen.polyline([(x0 - 4, y0 + 7), (x0, y0), (x0 + 4, y0 + 7)], width=0.9))
    el.add(pen.polyline([(x1 - 7, y1 - 4), (x1, y1), (x1 - 7, y1 + 4)], width=0.9))


def bar_chart(rng: random.Random, values: list[float] | None = None, labels: list[str] | None = None, value_labels: list[str] | None = None,
              highlight: int | None = None, direction: str = "up", **_) -> Element:
    pen = Pen(rng, wobble=0.5, step=2.5)
    el = Element("bar_chart", 200, 160)
    if not values:
        values = [1, 1.6, 2.3, 3.2] if direction == "up" else [3.2, 2.4, 1.7, 1.0]
    n = len(values)
    top = max(values) or 1.0
    x0, y0, x1, y1 = 14, 22, 194, 140
    axes(pen, el, x0, y0 - 10, x1, y1)
    gap = (x1 - x0 - 10) / n
    bw = gap * 0.58
    for i, v in enumerate(values):
        h = max(4.0, (y1 - y0 - 6) * v / top)
        bx = x0 + 10 + i * gap + (gap - bw) / 2
        color = "accent" if (highlight is None and i == n - 1) or highlight == i else "ink"
        if direction == "down" and highlight is None and i == n - 1:
            color = "negative"
        el.add(pen.rect(bx, y1 - h, bx + bw, y1, width=1.0, color=color))
        el.strokes.extend(pen.hatch(bx + 2, y1 - h + 2, bx + bw - 2, y1 - 1, spacing=7, color=color))
        if value_labels and i < len(value_labels) and value_labels[i]:
            el.texts.append(TextSpec(value_labels[i], (bx - 14, y1 - h - 22, bx + bw + 14, y1 - h - 3), color=color, bold=True, size_hint=0.95))
        if labels and i < len(labels) and labels[i]:
            el.texts.append(TextSpec(labels[i], (bx - 16, y1 + 3, bx + bw + 16, y1 + 19), size_hint=0.9))
        el.anchors[f"bar{i}"] = (bx + bw / 2, y1 - h)
    el.anchors["peak"] = el.anchors[f"bar{max(range(n), key=lambda k: values[k])}"]
    return el


def line_chart(rng: random.Random, values: list[float] | None = None, direction: str = "up", end_label: str = "", start_label: str = "", **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.0)
    el = Element("line_chart", 200, 160)
    x0, y0, x1, y1 = 14, 18, 192, 140
    axes(pen, el, x0, y0 - 8, x1, y1)
    if not values:
        base = [0.15, 0.3, 0.25, 0.45, 0.42, 0.62, 0.58, 0.85]
        values = base if direction == "up" else list(reversed(base))
        if direction == "flat":
            values = [0.5, 0.55, 0.48, 0.52, 0.5, 0.53, 0.49, 0.51]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(values):
        x = x0 + 14 + (x1 - x0 - 30) * i / max(1, len(values) - 1)
        y = y1 - 12 - (y1 - y0 - 24) * (v - lo) / span
        pts.append((x, y))
    color = "positive" if direction == "up" else ("negative" if direction == "down" else "accent")
    el.add(pen.polyline(pts, width=1.4, color=color))
    for x, y in (pts[0], pts[-1]):
        el.add(pen.dot(x, y, 3.0, color=color))
    if start_label:
        x, y = pts[0]
        el.texts.append(TextSpec(start_label, (x - 10, y - 26, x + 50, y - 6), bold=True, size_hint=0.95, color="ink"))
    if end_label:
        x, y = pts[-1]
        el.texts.append(TextSpec(end_label, (x - 64, y - 30, x + 10, y - 8), bold=True, size_hint=0.95, color=color))
    el.anchors["end"] = pts[-1]
    el.anchors["start"] = pts[0]
    return el


def pie_chart(rng: random.Random, percent: float = 50.0, label: str = "", **_) -> Element:
    pen = Pen(rng, wobble=0.3, step=2.0)
    el = Element("pie_chart", 160, 160)
    cx, cy, r = 80, 80, 70
    el.add(pen.circle(cx, cy, r, width=1.2))
    frac = max(0.0, min(1.0, percent / 100.0))
    a1 = -90 + 360 * frac
    if 0 < frac < 1:
        el.add(pen.line((cx, cy), (cx, cy - r), width=1.0))
        el.add(pen.line((cx, cy), (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1))), width=1.0))
    if frac > 0:
        # shade the slice with radial strokes
        steps = max(3, int(36 * frac))
        for i in range(1, steps):
            a = math.radians(-90 + 360 * frac * i / steps)
            el.add(pen.line((cx + 8 * math.cos(a), cy + 8 * math.sin(a)), (cx + (r - 6) * math.cos(a), cy + (r - 6) * math.sin(a)), width=0.5, color="accent", role="hatch"))
    if label:
        mid = math.radians(-90 + 180 * frac) if frac < 1 else math.radians(90)
        lx, ly = (cx + r * 0.5 * math.cos(mid), cy + r * 0.5 * math.sin(mid)) if frac < 0.5 or frac >= 1 else (cx - r * 0.45, cy + r * 0.35)
        el.texts.append(TextSpec(label, (lx - 34, ly - 16, lx + 34, ly + 16), bold=True, size_hint=0.95, color="ink"))
    return el


def trend_arrow(rng: random.Random, direction: str = "up", **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.5)
    el = Element(f"trend_{direction}", 120, 90)
    if direction == "up":
        pts = [(4, 82), (34, 52), (52, 64), (100, 14)]
        color = "positive"
    else:
        pts = [(4, 10), (34, 40), (52, 28), (100, 78)]
        color = "negative"
    el.add(pen.polyline(pts, width=2.0, color=color))
    tip = pts[-1]
    prev = pts[-2]
    ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
    h = 18
    el.add(pen.polyline([(tip[0] - h * math.cos(ang - 0.5), tip[1] - h * math.sin(ang - 0.5)), tip,
                         (tip[0] - h * math.cos(ang + 0.5), tip[1] - h * math.sin(ang + 0.5))], width=2.0, color=color))
    el.anchors["tip"] = tip
    return el


def percent_badge(rng: random.Random, text: str = "100%", color: str = "accent", **_) -> Element:
    pen = Pen(rng, wobble=0.6, step=2.5)
    el = Element("percent_badge", 140, 80)
    el.add(pen.ellipse(70, 40, 66, 36, color=color, width=1.3))
    el.texts.append(TextSpec(text, (16, 12, 124, 68), color=color, bold=True, size_hint=0.85))
    return el


def before_after(rng: random.Random, left: str = "", right: str = "", direction: str = "up", **_) -> Element:
    """Two framed panels connected by an arrow; labels come from the narration."""
    pen = Pen(rng, wobble=0.5, step=2.5)
    el = Element("before_after", 220, 110)
    el.add(pen.rect(4, 14, 90, 96, r=6))
    el.add(*pen.arrow((98, 55), (126, 55), head=10, width=1.4, color="accent"))
    el.add(pen.rect(134, 14, 216, 96, r=6, color="positive" if direction == "up" else "negative"))
    if left:
        el.texts.append(TextSpec(left, (10, 34, 84, 76), bold=True, size_hint=0.8))
    if right:
        el.texts.append(TextSpec(right, (140, 34, 210, 76), bold=True, size_hint=0.8, color="positive" if direction == "up" else "negative"))
    el.anchors["left_panel"] = (47, 55)
    el.anchors["right_panel"] = (175, 55)
    return el


def equation(rng: random.Random, text: str = "", **_) -> Element:
    pen = Pen(rng, wobble=0.5)
    el = Element("equation", 220, 70)
    el.texts.append(TextSpec(text, (6, 6, 214, 56), bold=True, size_hint=0.8))
    el.add(pen.line((10, 62), (210, 64), width=0.9, color="accent"))
    return el


CHARTS = {
    "bar_chart": bar_chart,
    "line_chart": line_chart,
    "pie_chart": pie_chart,
    "trend_arrow": trend_arrow,
    "percent_badge": percent_badge,
    "before_after": before_after,
    "equation": equation,
}
