"""Conceptual diagrams: labelled boxes, timelines, cycles, decision trees,
supply-and-demand curves and process steps."""

from __future__ import annotations

import math
import random

from .geometry import Element, Pen, TextSpec


def label_box(rng: random.Random, text: str = "", color: str = "ink", shape: str = "rect", **_) -> Element:
    pen = Pen(rng, wobble=0.6, step=2.5)
    el = Element("label_box", 180, 70)
    if shape == "cloud":
        el.add(pen.ellipse(90, 35, 86, 32, color=color))
    elif shape == "underline":
        el.add(pen.line((8, 64), (172, 62), width=1.2, color=color))
    else:
        el.add(pen.rect(4, 4, 176, 66, r=10, color=color))
    el.texts.append(TextSpec(text, (14, 10, 166, 60), size_hint=0.6, bold=True, color="ink"))
    return el


def timeline(rng: random.Random, labels: list[str] | None = None, points: int = 3, **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.5)
    labels = labels or []
    n = max(2, min(5, len(labels) or points))
    el = Element("timeline", 220, 90)
    el.add(*pen.arrow((6, 45), (214, 45), head=10, width=1.2))
    for i in range(n):
        x = 22 + i * (180 / max(1, n - 1))
        el.add(pen.circle(x, 45, 6, color="accent", fill="accent", fill_opacity=0.5))
        if i < len(labels) and labels[i]:
            y0 = 6 if i % 2 == 0 else 58
            el.texts.append(TextSpec(labels[i], (x - 34, y0, x + 34, y0 + 26), size_hint=0.8, bold=True))
        el.anchors[f"p{i}"] = (x, 45)
    return el


def cycle(rng: random.Random, labels: list[str] | None = None, nodes: int = 4, **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.5)
    labels = labels or []
    n = max(3, min(5, len(labels) or nodes))
    el = Element("cycle", 180, 180)
    cx, cy, r = 90, 90, 64
    for i in range(n):
        a0 = -90 + 360 * i / n + 18
        a1 = -90 + 360 * (i + 1) / n - 18
        arc = pen.arc(cx, cy, r, a0, a1, width=1.1, color="accent")
        el.add(arc)
        tip = arc.points[-1]
        prev = arc.points[-4]
        ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
        el.add(pen.polyline([(tip[0] - 9 * math.cos(ang - 0.5), tip[1] - 9 * math.sin(ang - 0.5)), tip,
                             (tip[0] - 9 * math.cos(ang + 0.5), tip[1] - 9 * math.sin(ang + 0.5))], width=1.1, color="accent"))
        na = math.radians(-90 + 360 * i / n)
        nx, ny = cx + r * math.cos(na), cy + r * math.sin(na)
        el.add(pen.circle(nx, ny, 9, fill="accent", fill_opacity=0.25))
        if i < len(labels) and labels[i]:
            el.texts.append(TextSpec(labels[i], (nx - 40, ny - 34, nx + 40, ny - 12), size_hint=0.9, bold=True))
    return el


def decision_tree(rng: random.Random, question: str = "?", options: list[str] | None = None, **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.5)
    options = (options or ["", ""])[:3]
    el = Element("decision_tree", 220, 170)
    el.add(pen.polyline([(110, 4), (150, 34), (110, 64), (70, 34)], closed=True, color="accent"))
    el.texts.append(TextSpec(question, (80, 18, 140, 50), size_hint=0.9, bold=True))
    n = len(options)
    for i, opt in enumerate(options):
        x = 40 + i * (140 / max(1, n - 1)) if n > 1 else 110
        el.add(*pen.arrow((110, 66), (x, 112), head=9, width=1.0))
        el.add(pen.rect(x - 38, 116, x + 38, 162, r=6))
        if opt:
            el.texts.append(TextSpec(opt, (x - 34, 122, x + 34, 156), size_hint=0.7, bold=True))
    return el


def supply_demand(rng: random.Random, **_) -> Element:
    pen = Pen(rng, wobble=0.4, step=2.5)
    el = Element("supply_demand", 200, 170)
    el.add(pen.line((14, 8), (14, 150), width=1.1), pen.line((14, 150), (194, 150), width=1.1))
    el.add(pen.bezier((26, 140), (80, 110), (140, 60), (184, 20), width=1.4, color="accent"))
    el.add(pen.bezier((26, 20), (80, 60), (140, 110), (184, 140), width=1.4, color="negative"))
    el.add(pen.dot(105, 80, 4.0))
    el.add(pen.line((105, 80), (105, 150), width=0.5, color="muted"), pen.line((14, 80), (105, 80), width=0.5, color="muted"))
    el.texts.append(TextSpec("S", (172, 2, 196, 24), color="accent", bold=True))
    el.texts.append(TextSpec("D", (172, 128, 196, 150), color="negative", bold=True))
    return el


def steps(rng: random.Random, labels: list[str] | None = None, count: int = 3, **_) -> Element:
    """Numbered step boxes stacked vertically with connecting arrows."""
    pen = Pen(rng, wobble=0.5, step=2.5)
    labels = labels or []
    n = max(2, min(4, len(labels) or count))
    el = Element("steps", 200, 64 * n)
    for i in range(n):
        y = i * 64
        el.add(pen.circle(22, y + 24, 16, color="accent", fill="accent", fill_opacity=0.25))
        el.texts.append(TextSpec(str(i + 1), (10, y + 10, 34, y + 38), bold=True, lang="en"))
        el.add(pen.rect(46, y + 4, 196, y + 44, r=8))
        if i < len(labels) and labels[i]:
            el.texts.append(TextSpec(labels[i], (54, y + 8, 190, y + 40), size_hint=0.7))
        if i < n - 1:
            el.add(*pen.arrow((22, y + 42), (22, y + 62), head=6, width=0.9))
    return el


def speech_bubble(rng: random.Random, text: str = "", **_) -> Element:
    pen = Pen(rng, wobble=0.5, step=2.5)
    el = Element("speech_bubble", 170, 90)
    el.add(pen.polyline([(20, 4), (160, 4), (166, 10), (166, 60), (160, 66), (50, 66), (24, 86), (32, 66), (20, 66), (14, 60), (14, 10)], closed=True))
    if text:
        el.texts.append(TextSpec(text, (22, 10, 158, 60), size_hint=0.6))
    el.anchors["tail"] = (24, 86)
    return el


DIAGRAMS = {
    "label_box": label_box,
    "timeline": timeline,
    "cycle": cycle,
    "decision_tree": decision_tree,
    "supply_demand": supply_demand,
    "steps": steps,
    "speech_bubble": speech_bubble,
}
