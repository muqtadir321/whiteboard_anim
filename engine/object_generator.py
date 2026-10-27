"""Procedural whiteboard objects (money, buildings, devices, everyday items, symbols).

Each generator returns an Element in its own local box, with strokes in natural
drawing order (outline first, then details). All artwork is original and
programmatic, so no third-party image licences are involved.
"""

from __future__ import annotations

import math
import random
from typing import Callable

from .geometry import Element, Pen, Stroke, TextSpec


def _pen(rng: random.Random) -> Pen:
    return Pen(rng, wobble=0.5, step=2.5)


def coin(rng, symbol: str = "$", **_) -> Element:
    p = _pen(rng)
    el = Element("coin", 40, 40)
    el.add(p.circle(20, 20, 18, color="ink", fill="gold", fill_opacity=0.35))
    el.add(p.circle(20, 20, 13, width=0.6))
    el.texts.append(TextSpec(symbol, (10, 8, 30, 32), color="ink", bold=True, size_hint=0.8, lang="en"))
    return el


def coin_stack(rng, count: int = 4, symbol: str = "$", **_) -> Element:
    p = _pen(rng)
    count = max(2, min(7, int(count)))
    h = 12 + count * 9
    el = Element("coin_stack", 50, h)
    for i in range(count):
        y = h - 8 - i * 9
        el.add(p.ellipse(25, y, 22, 6, start=180, sweep=180, overshoot=0, fill="gold", fill_opacity=0.25))
        el.add(p.line((3, y), (3, y - 5), width=0.9), p.line((47, y), (47, y - 5), width=0.9))
    top = h - 8 - (count - 1) * 9 - 5
    el.add(p.ellipse(25, top, 22, 6, fill="gold", fill_opacity=0.35))
    el.texts.append(TextSpec(symbol, (18, top - 6, 32, top + 6), bold=True, size_hint=1.0, lang="en"))
    el.anchors["top"] = (25, top - 8)
    return el


def banknote(rng, symbol: str = "$", **_) -> Element:
    p = _pen(rng)
    el = Element("banknote", 80, 40)
    el.add(p.rect(2, 2, 78, 38, r=3, fill="positive", fill_opacity=0.15))
    el.add(p.rect(8, 7, 72, 33, r=2, width=0.5))
    el.add(p.circle(40, 20, 9, width=0.7))
    el.texts.append(TextSpec(symbol, (32, 11, 48, 29), bold=True, size_hint=0.9, lang="en"))
    return el


def wallet(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("wallet", 80, 56)
    el.add(p.rect(2, 10, 78, 54, r=6, fill="brown", fill_opacity=0.25))
    el.add(p.polyline([(6, 10), (14, 2), (60, 2), (66, 10)], width=0.8))
    el.add(p.rect(54, 24, 80, 40, r=4, width=0.9))
    el.add(p.dot(62, 32, 2.2))
    el.anchors["top"] = (40, 2)
    return el


def money_bag(rng, symbol: str = "$", **_) -> Element:
    p = _pen(rng)
    el = Element("money_bag", 70, 80)
    el.add(p.bezier((24, 22), (-10, 50), (8, 80), (35, 79), fill=None))
    el.add(p.bezier((35, 79), (62, 80), (80, 50), (46, 22)))
    el.add(p.polyline([(24, 22), (18, 8), (28, 13), (35, 4), (42, 13), (52, 8), (46, 22)]))
    el.add(p.line((22, 22), (48, 22), width=1.2, color="brown"))
    el.texts.append(TextSpec(symbol, (22, 38, 48, 70), bold=True, size_hint=0.85, lang="en"))
    return el


def piggy_bank(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("piggy_bank", 90, 66)
    el.add(p.ellipse(45, 34, 36, 25, fill="pink", fill_opacity=0.3))
    el.add(p.ellipse(82, 32, 6, 8))
    el.add(p.dot(80, 30, 1.2), p.dot(84, 30, 1.2))
    el.add(p.polyline([(58, 12), (64, 2), (68, 14)]))
    el.add(p.dot(64, 24, 1.8))
    el.add(p.line((22, 55), (22, 64), width=1.3), p.line((36, 57), (36, 65), width=1.3), p.line((56, 57), (56, 65), width=1.3), p.line((68, 54), (68, 63), width=1.3))
    el.add(p.line((38, 10), (52, 10), width=1.4))
    el.add(p.bezier((9, 34), (2, 30), (4, 24), (8, 26), width=0.8))
    return el


def bank(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("bank", 100, 90)
    el.add(p.polyline([(4, 26), (50, 2), (96, 26)], closed=True, fill="accent", fill_opacity=0.15))
    el.add(p.line((8, 30), (92, 30)))
    for x in (18, 38, 62, 82):
        el.add(p.line((x - 4, 34), (x - 4, 76), width=0.9), p.line((x + 4, 34), (x + 4, 76), width=0.9))
    el.add(p.rect(4, 78, 96, 86))
    el.texts.append(TextSpec("$", (42, 9, 58, 25), bold=True, size_hint=0.9, lang="en"))
    return el


def house(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("house", 90, 84)
    el.add(p.polyline([(2, 38), (45, 2), (88, 38)], fill=None))
    el.add(p.rect(10, 36, 80, 82, fill="brown", fill_opacity=0.12))
    el.add(p.rect(38, 54, 54, 82, width=0.9))
    el.add(p.rect(18, 46, 32, 60, width=0.8), p.line((25, 46), (25, 60), width=0.5), p.line((18, 53), (32, 53), width=0.5))
    el.add(p.rect(62, 12, 72, 28, width=0.8))
    return el


def building(rng, floors: int = 5, **_) -> Element:
    p = _pen(rng)
    floors = max(3, min(8, int(floors)))
    h = 20 + floors * 14
    el = Element("building", 60, h)
    el.add(p.rect(4, 4, 56, h - 2, fill="accent", fill_opacity=0.1))
    for f in range(floors - 1):
        y = 12 + f * 14
        for x in (12, 26, 40):
            el.add(p.rect(x, y, x + 8, y + 8, width=0.6))
    el.add(p.rect(24, h - 18, 36, h - 2, width=0.8))
    return el


def shop(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("shop", 100, 90)
    el.add(p.rect(8, 30, 92, 88))
    # striped awning
    el.add(p.polyline([(2, 30), (10, 12), (90, 12), (98, 30)], closed=True, color="negative", fill="negative", fill_opacity=0.25))
    for i in range(1, 6):
        x = 10 + i * 13.3
        el.add(p.line((x, 12), (x + 1.5, 30), width=0.6, color="negative"))
    el.add(p.rect(16, 42, 50, 70, width=0.9), p.rect(60, 46, 82, 88, width=0.9))
    el.add(p.dot(64, 68, 1.5))
    el.add(p.rect(24, 2, 76, 12, width=0.7))
    el.texts.append(TextSpec("SHOP", (26, 3, 74, 11), size_hint=0.95, lang="en"))
    return el


def factory(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("factory", 100, 80)
    el.add(p.polyline([(2, 78), (2, 40), (26, 26), (26, 40), (50, 26), (50, 40), (74, 26), (74, 78)], fill=None))
    el.add(p.line((2, 78), (98, 78)))
    el.add(p.rect(80, 12, 92, 78))
    for i in range(3):
        el.add(p.circle(88 + i * 4, 8 - i * 4, 3 + i, width=0.6, color="muted"))
    for x in (10, 34, 58):
        el.add(p.rect(x, 52, x + 10, 62, width=0.7))
    return el


def phone(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("phone", 44, 80)
    el.add(p.rect(2, 2, 42, 78, r=6, fill="ink", fill_opacity=0.06))
    el.add(p.rect(6, 12, 38, 64, r=1, width=0.7, fill="accent", fill_opacity=0.12))
    el.add(p.line((17, 7), (27, 7), width=0.6), p.circle(22, 71, 3, width=0.6))
    return el


def laptop(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("laptop", 100, 66)
    el.add(p.rect(14, 2, 86, 50, r=3))
    el.add(p.rect(19, 7, 81, 45, width=0.6, fill="accent", fill_opacity=0.12))
    el.add(p.polyline([(4, 62), (14, 50), (86, 50), (96, 62)], closed=True, fill="ink", fill_opacity=0.05))
    el.add(p.line((42, 57), (58, 57), width=0.6))
    return el


def computer(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("computer", 90, 80)
    el.add(p.rect(4, 2, 86, 56, r=3))
    el.add(p.rect(10, 8, 80, 50, width=0.6, fill="accent", fill_opacity=0.1))
    el.add(p.polyline([(38, 56), (34, 72), (56, 72), (52, 56)]))
    el.add(p.line((26, 76), (64, 76)))
    return el


def book(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("book", 90, 64)
    el.add(p.bezier((45, 10), (30, 2), (10, 4), (2, 8)), p.line((2, 8), (2, 58)), p.bezier((2, 58), (14, 54), (32, 54), (45, 62)))
    el.add(p.bezier((45, 10), (60, 2), (80, 4), (88, 8)), p.line((88, 8), (88, 58)), p.bezier((88, 58), (76, 54), (58, 54), (45, 62)))
    el.add(p.line((45, 10), (45, 62), width=0.8))
    for y in (20, 30, 40):
        el.add(p.line((10, y), (36, y + 2), width=0.4, color="muted"), p.line((54, y + 2), (80, y), width=0.4, color="muted"))
    return el


def document(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("document", 60, 76)
    el.add(p.polyline([(2, 2), (44, 2), (58, 16), (58, 74), (2, 74)], closed=True, fill="paper", fill_opacity=0.5))
    el.add(p.polyline([(44, 2), (44, 16), (58, 16)], width=0.7))
    for i, y in enumerate((26, 36, 46, 56, 64)):
        el.add(p.line((10, y), (50 - (i % 2) * 10, y), width=0.5, color="muted"))
    return el


def car(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("car", 110, 56)
    el.add(p.polyline([(4, 40), (6, 26), (24, 24), (36, 8), (74, 8), (88, 24), (104, 28), (106, 40)], fill=None))
    el.add(p.line((4, 40), (20, 40)), p.line((40, 40), (76, 40)), p.line((96, 40), (106, 40)))
    el.add(p.circle(30, 42, 10, fill="ink", fill_opacity=0.15), p.circle(86, 42, 10, fill="ink", fill_opacity=0.15))
    el.add(p.polyline([(40, 12), (54, 12), (54, 24), (30, 24)], width=0.7), p.polyline([(60, 12), (72, 12), (82, 24), (60, 24)], width=0.7))
    return el


def truck(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("truck", 120, 64)
    el.add(p.rect(2, 4, 76, 48, fill="accent", fill_opacity=0.12))
    el.add(p.polyline([(76, 16), (100, 16), (116, 32), (116, 48), (76, 48)]))
    el.add(p.polyline([(82, 20), (98, 20), (108, 32), (82, 32)], width=0.7))
    el.add(p.circle(22, 52, 9), p.circle(96, 52, 9))
    return el


def bicycle(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("bicycle", 100, 60)
    el.add(p.circle(22, 40, 18), p.circle(78, 40, 18))
    el.add(p.polyline([(22, 40), (44, 40), (66, 18), (36, 18), (22, 40)], width=0.9))
    el.add(p.polyline([(44, 40), (36, 12)], width=0.9), p.line((30, 12), (42, 12)))
    el.add(p.polyline([(78, 40), (66, 12), (74, 8)], width=0.9))
    return el


def clock(rng, hour: float = 3, **_) -> Element:
    p = _pen(rng)
    el = Element("clock", 70, 70)
    el.add(p.circle(35, 35, 32, fill="paper", fill_opacity=0.4))
    for i in range(12):
        a = math.radians(i * 30)
        el.add(p.line((35 + 26 * math.sin(a), 35 - 26 * math.cos(a)), (35 + 29 * math.sin(a), 35 - 29 * math.cos(a)), width=0.5))
    ha = math.radians((hour % 12) * 30)
    el.add(p.line((35, 35), (35 + 15 * math.sin(ha), 35 - 15 * math.cos(ha)), width=1.2))
    el.add(p.line((35, 35), (35, 12), width=0.9, color="accent"))
    return el


def calendar(rng, label: str = "", **_) -> Element:
    p = _pen(rng)
    el = Element("calendar", 70, 70)
    el.add(p.rect(2, 8, 68, 68, r=3))
    el.add(p.rect(2, 8, 68, 22, r=3, color="negative", fill="negative", fill_opacity=0.3))
    el.add(p.line((18, 2), (18, 14), width=1.1), p.line((52, 2), (52, 14), width=1.1))
    if label:
        el.texts.append(TextSpec(label, (8, 28, 62, 64), bold=True, size_hint=0.6, lang="en"))
    else:
        for r in range(3):
            for c in range(4):
                el.add(p.rect(9 + c * 14, 28 + r * 13, 17 + c * 14, 35 + r * 13, width=0.4))
    return el


def lightbulb(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("lightbulb", 60, 84)
    el.add(p.bezier((20, 54), (2, 40), (6, 4), (30, 4)), p.bezier((30, 4), (54, 4), (58, 40), (40, 54)))
    el.add(p.rect(20, 54, 40, 70, r=2, fill="muted", fill_opacity=0.2))
    el.add(p.line((20, 60), (40, 60), width=0.5), p.line((20, 65), (40, 65), width=0.5), p.line((26, 74), (34, 74)))
    el.add(p.polyline([(24, 52), (26, 30), (30, 36), (34, 30), (36, 52)], width=0.6, color="gold"))
    for a in (-60, -30, 0, 30, 60):
        r = math.radians(a - 90)
        el.add(p.line((30 + 34 * math.cos(r), 30 + 34 * math.sin(r)), (30 + 42 * math.cos(r), 30 + 42 * math.sin(r)), width=0.7, color="gold"))
    return el


def gear(rng, teeth: int = 8, **_) -> Element:
    p = _pen(rng)
    el = Element("gear", 70, 70)
    pts = []
    n = teeth * 4
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        r = 32 if (i % 4) in (0, 1) else 25
        pts.append((35 + r * math.cos(a), 35 + r * math.sin(a)))
    el.add(p.polyline(pts, closed=True, fill="muted", fill_opacity=0.15))
    el.add(p.circle(35, 35, 9))
    return el


def cart(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("cart", 90, 70)
    el.add(p.polyline([(2, 6), (16, 6), (26, 46), (78, 46), (86, 16), (20, 16)]))
    el.add(p.circle(32, 60, 6), p.circle(72, 60, 6))
    el.add(p.line((24, 30), (82, 30), width=0.5))
    return el


def tree(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("tree", 70, 90)
    el.add(p.polyline([(30, 88), (32, 54), (28, 40)], width=1.3, color="brown"), p.polyline([(42, 88), (40, 54), (44, 40)], width=1.3, color="brown"))
    el.add(p.bezier((10, 50), (-6, 30), (14, 2), (35, 6), color="positive"), p.bezier((35, 6), (60, 2), (76, 30), (60, 50), color="positive"))
    el.add(p.bezier((10, 50), (24, 62), (46, 62), (60, 50), color="positive"))
    return el


def plant(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("plant", 60, 80)
    el.add(p.polyline([(14, 52), (46, 52), (40, 78), (20, 78)], closed=True, color="brown", fill="brown", fill_opacity=0.2))
    el.add(p.bezier((30, 52), (30, 36), (30, 24), (30, 10), color="positive"))
    el.add(p.bezier((30, 34), (14, 32), (6, 20), (8, 14), color="positive"), p.bezier((8, 14), (22, 14), (30, 26), (30, 34), color="positive"))
    el.add(p.bezier((30, 24), (44, 22), (52, 10), (50, 4), color="positive"), p.bezier((50, 4), (38, 4), (30, 14), (30, 24), color="positive"))
    return el


def heart(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("heart", 70, 62)
    el.add(p.bezier((35, 60), (-10, 30), (6, -6), (35, 16), color="negative", fill="negative", fill_opacity=0.3))
    el.add(p.bezier((35, 16), (64, -6), (80, 30), (35, 60), color="negative"))
    return el


def warning(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("warning", 80, 70)
    el.add(p.polyline([(40, 2), (78, 68), (2, 68)], closed=True, color="negative", fill="gold", fill_opacity=0.3))
    el.add(p.line((40, 22), (40, 48), width=1.6), p.dot(40, 57, 2.6))
    return el


def target(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("target", 80, 80)
    el.add(p.circle(40, 40, 36, color="negative"), p.circle(40, 40, 24), p.circle(40, 40, 11, color="negative", fill="negative", fill_opacity=0.4))
    el.add(*p.arrow((78, 4), (44, 36), head=9, width=1.0))
    return el


def lock(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("lock", 60, 76)
    el.add(p.arc(30, 30, 17, 180, 360, width=1.3))
    el.add(p.line((13, 30), (13, 34)), p.line((47, 30), (47, 34)))
    el.add(p.rect(4, 34, 56, 74, r=4, fill="gold", fill_opacity=0.3))
    el.add(p.circle(30, 50, 4), p.line((30, 54), (30, 63)))
    return el


def rate_badge(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("rate_badge", 80, 80)
    el.add(p.circle(40, 40, 36, fill="#F08A3C", fill_opacity=0.9))
    el.add(p.line((26, 56), (54, 24), width=1.6, color="paper"))
    el.add(p.circle(28, 28, 6, width=1.3, color="paper"), p.circle(52, 52, 6, width=1.3, color="paper"))
    return el


def jail(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("jail", 90, 100)
    el.add(p.rect(4, 4, 86, 96, r=3, fill="#9AA0A6", fill_opacity=0.9))
    for x in (20, 36, 54, 70):
        el.add(p.line((x, 6), (x, 94), width=1.4))
    el.add(p.line((6, 50), (84, 50), width=1.2))
    return el


def handcuffs(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("handcuffs", 110, 60)
    el.add(p.circle(24, 34, 20, width=1.4, fill="#C8CCD2", fill_opacity=0.9), p.circle(24, 34, 11, width=1.0, fill="paper"))
    el.add(p.circle(86, 34, 20, width=1.4, fill="#C8CCD2", fill_opacity=0.9), p.circle(86, 34, 11, width=1.0, fill="paper"))
    el.add(p.polyline([(44, 30), (52, 26), (58, 30), (66, 26)], width=1.2))
    return el


def phone_call(rng, **_) -> Element:
    """Smartphone on a video call: a face on screen and ringing waves."""
    p = _pen(rng)
    el = Element("phone_call", 110, 100)
    el.add(p.rect(18, 4, 62, 96, r=7, fill="#3D4A5C", fill_opacity=0.95))
    el.add(p.rect(23, 12, 57, 82, r=2, fill="#BFE3FF", fill_opacity=0.95))
    el.add(p.circle(40, 38, 9, width=1.0, fill="skin"), p.arc(40, 62, 14, 200, 340, width=1.0))
    el.add(p.arc(66, 40, 12, -50, 50, width=1.2, color="negative"), p.arc(66, 40, 22, -45, 45, width=1.2, color="negative"))
    return el


def fraud_mask(rng, **_) -> Element:
    """Robber's eye mask: scams and fraudsters."""
    p = _pen(rng)
    el = Element("fraud_mask", 120, 60)
    el.add(p.polyline([(4, 24), (30, 10), (60, 18), (90, 10), (116, 24), (104, 46), (78, 50), (60, 38), (42, 50), (16, 46)],
                      closed=True, fill="#2B2B2B", fill_opacity=0.95))
    el.add(p.ellipse(34, 30, 11, 7, width=1.0, fill="paper"), p.ellipse(86, 30, 11, 7, width=1.0, fill="paper"))
    return el


def ai_chip(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("ai_chip", 100, 100)
    for i in range(5):
        v = 22 + i * 14
        el.add(p.line((v, 4), (v, 18), width=1.0), p.line((v, 82), (v, 96), width=1.0),
               p.line((4, v), (18, v), width=1.0), p.line((82, v), (96, v), width=1.0))
    el.add(p.rect(18, 18, 82, 82, r=6, fill="#6C5CE7", fill_opacity=0.92))
    el.texts.append(TextSpec("AI", (28, 30, 72, 70), color="paper", bold=True, size_hint=0.85, lang="en"))
    return el


def bubble(rng, **_) -> Element:
    """A price bubble about to pop."""
    p = _pen(rng)
    el = Element("bubble", 100, 100)
    el.add(p.circle(48, 52, 40, width=1.3, fill="#AEE1F9", fill_opacity=0.75))
    el.add(p.arc(48, 52, 30, 200, 250, width=1.4, color="paper"))
    el.add(p.circle(88, 16, 8, width=1.0, fill="#AEE1F9", fill_opacity=0.75), p.circle(12, 14, 5, width=0.9))
    return el


def key(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("key", 90, 40)
    el.add(p.circle(18, 20, 15, fill="gold", fill_opacity=0.3), p.circle(18, 20, 5, width=0.6))
    el.add(p.polyline([(33, 20), (86, 20), (86, 30)]), p.line((72, 20), (72, 28)))
    return el


def globe(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("globe", 80, 80)
    el.add(p.circle(40, 40, 36, color="accent", fill="accent", fill_opacity=0.1))
    el.add(p.ellipse(40, 40, 14, 36, width=0.6, color="accent"), p.line((4, 40), (76, 40), width=0.6, color="accent"))
    el.add(p.bezier((10, 22), (30, 28), (50, 28), (70, 22), width=0.5, color="accent"), p.bezier((10, 58), (30, 52), (50, 52), (70, 58), width=0.5, color="accent"))
    return el


def graduation_cap(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("graduation_cap", 90, 60)
    el.add(p.polyline([(45, 2), (88, 18), (45, 34), (2, 18)], closed=True, fill="ink", fill_opacity=0.25))
    el.add(p.polyline([(20, 26), (20, 44), (45, 52), (70, 44), (70, 26)]))
    el.add(p.polyline([(80, 22), (80, 46)], width=0.7), p.dot(80, 48, 2))
    return el


def school(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("school", 100, 92)
    el.add(p.rect(6, 40, 94, 90), p.polyline([(2, 42), (50, 18), (98, 42)]))
    el.add(p.line((50, 18), (50, 2)), p.polyline([(50, 2), (66, 6), (50, 10)], color="negative", fill="negative", fill_opacity=0.4))
    el.add(p.rect(42, 64, 58, 90, width=0.9))
    for x in (14, 70):
        el.add(p.rect(x, 52, x + 16, 66, width=0.7))
    el.add(p.circle(50, 34, 6, width=0.7))
    return el


def hospital(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("hospital", 90, 90)
    el.add(p.rect(4, 14, 86, 88, fill="paper", fill_opacity=0.3))
    el.add(p.polyline([(38, 22), (52, 22), (52, 32), (62, 32), (62, 46), (52, 46), (52, 56), (38, 56), (38, 46), (28, 46), (28, 32), (38, 32)], closed=True, color="negative", fill="negative", fill_opacity=0.4))
    el.add(p.rect(36, 66, 54, 88, width=0.8))
    return el


def food(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("food", 90, 60)
    el.add(p.ellipse(45, 46, 42, 10))
    el.add(p.bezier((10, 44), (14, 12), (76, 12), (80, 44), fill="brown", fill_opacity=0.15))
    for x in (30, 45, 60):
        el.add(p.bezier((x, 12), (x - 4, 6), (x + 4, 4), (x, -2), width=0.5, color="muted"))
    return el


def water_drop(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("water_drop", 50, 70)
    el.add(p.bezier((25, 2), (10, 26), (2, 38), (4, 48), color="accent"), p.bezier((4, 48), (6, 72), (44, 72), (46, 48), color="accent", fill="accent", fill_opacity=0.25))
    el.add(p.bezier((46, 48), (48, 38), (40, 26), (25, 2), color="accent"))
    return el


def scale_balance(rng, tilt: float = 0.0, left_label: str = "", right_label: str = "", **_) -> Element:
    p = _pen(rng)
    el = Element("scale", 120, 100)
    el.add(p.line((60, 10), (60, 92)), p.line((36, 94), (84, 94), width=1.3))
    dy = 10 * tilt
    el.add(p.line((14, 20 + dy), (106, 20 - dy), width=1.1))
    for x, yy in ((14, 20 + dy), (106, 20 - dy)):
        el.add(p.line((x, yy), (x - 12, yy + 30), width=0.6), p.line((x, yy), (x + 12, yy + 30), width=0.6))
        el.add(p.arc(x, yy + 30, 14, 0, 180, width=1.0))
    el.add(p.circle(60, 10, 3.5))
    if left_label:
        el.texts.append(TextSpec(left_label, (-6, 48 + dy, 34, 64 + dy), size_hint=0.9))
    if right_label:
        el.texts.append(TextSpec(right_label, (86, 48 - dy, 126, 64 - dy), size_hint=0.9))
    return el


def trophy(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("trophy", 80, 84)
    el.add(p.bezier((18, 4), (18, 40), (30, 50), (40, 50), color="gold", fill="gold", fill_opacity=0.35), p.bezier((40, 50), (50, 50), (62, 40), (62, 4), color="gold"))
    el.add(p.line((18, 4), (62, 4), color="gold"))
    el.add(p.arc(14, 18, 8, 90, 270, width=0.8, color="gold"), p.arc(66, 18, 8, -90, 90, width=0.8, color="gold"))
    el.add(p.polyline([(36, 50), (34, 66), (46, 66), (44, 50)], color="gold"), p.rect(24, 66, 56, 80, fill="brown", fill_opacity=0.3))
    return el


def question_mark(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("question", 50, 80)
    el.add(p.bezier((8, 22), (8, 2), (44, 0), (42, 22), width=1.6, color="accent"), p.bezier((42, 22), (40, 36), (25, 36), (25, 52), width=1.6, color="accent"))
    el.add(p.dot(25, 66, 3.2, color="accent"))
    return el


def check_mark(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("check", 70, 60)
    el.add(p.polyline([(4, 30), (24, 54), (66, 4)], width=2.0, color="positive"))
    return el


def cross_mark(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("cross", 60, 60)
    el.add(p.line((6, 6), (54, 54), width=2.0, color="negative"), p.line((54, 6), (6, 54), width=2.0, color="negative"))
    return el


def star(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("star", 70, 68)
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        r = 34 if i % 2 == 0 else 14
        pts.append((35 + r * math.cos(a), 36 + r * math.sin(a)))
    el.add(p.polyline(pts, closed=True, color="gold", fill="gold", fill_opacity=0.35))
    return el


def cloud(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("cloud", 100, 60)
    el.add(p.bezier((14, 56), (-4, 54), (0, 30), (20, 30)), p.bezier((20, 30), (20, 6), (54, 0), (60, 20)), p.bezier((60, 20), (80, 8), (100, 26), (88, 40)))
    el.add(p.bezier((88, 40), (104, 50), (92, 58), (84, 56)), p.line((84, 56), (14, 56)))
    return el


def sun(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("sun", 80, 80)
    el.add(p.circle(40, 40, 18, color="gold", fill="gold", fill_opacity=0.35))
    for i in range(10):
        a = math.radians(i * 36)
        el.add(p.line((40 + 24 * math.cos(a), 40 + 24 * math.sin(a)), (40 + 36 * math.cos(a), 40 + 36 * math.sin(a)), width=0.8, color="gold"))
    return el


def rocket(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("rocket", 60, 100)
    el.add(p.bezier((30, 2), (8, 22), (14, 56), (16, 70)), p.line((16, 70), (44, 70)), p.bezier((44, 70), (46, 56), (52, 22), (30, 2)))
    el.add(p.circle(30, 32, 7, color="accent"))
    el.add(p.polyline([(16, 52), (4, 74), (16, 70)]), p.polyline([(44, 52), (56, 74), (44, 70)]))
    el.add(p.polyline([(22, 72), (30, 96), (38, 72)], color="negative", fill="gold", fill_opacity=0.4))
    return el


def magnifier(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("magnifier", 80, 80)
    el.add(p.circle(32, 32, 26, fill="accent", fill_opacity=0.08), p.line((51, 51), (76, 76), width=2.2))
    return el


def briefcase(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("briefcase", 90, 70)
    el.add(p.rect(2, 16, 88, 68, r=4, fill="brown", fill_opacity=0.25), p.rect(32, 4, 58, 16, r=3))
    el.add(p.line((2, 36), (88, 36), width=0.6), p.rect(40, 32, 50, 42, width=0.7))
    return el


def envelope(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("envelope", 90, 60)
    el.add(p.rect(2, 2, 88, 58, fill="paper", fill_opacity=0.3), p.polyline([(2, 2), (45, 34), (88, 2)], width=0.9))
    return el


def map_pin(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("map_pin", 50, 76)
    el.add(p.bezier((25, 74), (0, 40), (0, 2), (25, 2), color="negative", fill="negative", fill_opacity=0.3), p.bezier((25, 2), (50, 2), (50, 40), (25, 74), color="negative"))
    el.add(p.circle(25, 24, 8))
    return el


def pill(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("pill", 80, 40)
    el.add(p.rect(2, 4, 78, 36, r=16, fill="negative", fill_opacity=0.2), p.line((40, 4), (40, 36)))
    return el


def battery(rng, level: float = 0.6, **_) -> Element:
    p = _pen(rng)
    el = Element("battery", 90, 44)
    el.add(p.rect(2, 2, 80, 42, r=4), p.rect(80, 14, 88, 30, r=1))
    w = 6 + 68 * max(0.05, min(1.0, level))
    el.add(p.rect(6, 6, w, 38, r=2, color="positive", fill="positive", fill_opacity=0.4))
    return el


def chart_icon(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("chart_icon", 80, 70)
    el.add(p.polyline([(4, 2), (4, 66), (78, 66)]))
    for i, h in enumerate((18, 30, 44)):
        el.add(p.rect(14 + i * 20, 66 - h, 26 + i * 20, 66, width=0.8, color="accent", fill="accent", fill_opacity=0.3))
    return el


def box(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("box", 80, 70)
    el.add(p.polyline([(4, 20), (40, 6), (76, 20), (40, 34)], closed=True, fill="brown", fill_opacity=0.25))
    el.add(p.polyline([(4, 20), (4, 54), (40, 68), (76, 54), (76, 20)]), p.line((40, 34), (40, 68)))
    return el


def handshake(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("handshake", 100, 50)
    el.add(p.polyline([(2, 18), (28, 18), (44, 10), (58, 20)], width=1.1), p.polyline([(98, 18), (72, 18), (56, 30), (40, 30)], width=1.1))
    el.add(p.bezier((40, 30), (46, 40), (60, 40), (66, 30), width=0.9), p.line((2, 34), (28, 34)), p.line((98, 34), (76, 34)))
    return el


def chain_link(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("chain", 100, 40)
    el.add(p.rect(2, 8, 44, 32, r=12), p.rect(30, 8, 72, 32, r=12, color="accent"), p.rect(58, 8, 98, 32, r=12))
    return el


def thought_bubble(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("thought_bubble", 100, 70)
    el.extend(cloud(rng), dx=0, dy=0)
    el.add(p.circle(18, 64, 4, width=0.7))
    return el


def shield(rng, **_) -> Element:
    """Insurance cover: a shield with a tick."""
    p = _pen(rng)
    el = Element("shield", 70, 82)
    el.add(p.polyline([(35, 2), (66, 12), (64, 44), (35, 80), (6, 44), (4, 12)], closed=True, color="accent", fill="accent", fill_opacity=0.25))
    el.add(p.polyline([(35, 10), (58, 18), (56, 42), (35, 70)], width=0.6, color="accent"))
    el.add(p.polyline([(20, 40), (31, 52), (52, 26)], width=1.8, color="positive"))
    return el


def umbrella(rng, **_) -> Element:
    p = _pen(rng)
    el = Element("umbrella", 100, 100)
    el.add(p.arc(50, 48, 46, 180, 360, width=1.2, color="negative", fill="negative", fill_opacity=0.3))
    for x0, x1 in ((4, 34.7), (34.7, 65.3), (65.3, 96)):
        el.add(p.bezier((x0, 48), (x0 + 8, 40), (x1 - 8, 40), (x1, 48), width=0.9, color="negative"))
    el.add(p.bezier((50, 2), (40, 14), (36, 30), (34.7, 48), width=0.6), p.bezier((50, 2), (60, 14), (64, 30), (65.3, 48), width=0.6))
    el.add(p.line((50, 48), (50, 88), width=1.1), p.arc(42, 88, 8, 0, 180, width=1.1))
    return el


_CAR_BODY = [(4, 40), (6, 26), (24, 24), (36, 8), (74, 8), (88, 24), (104, 28), (106, 40)]


def car_crash(rng, **_) -> Element:
    """Two cars meeting head-on with an impact burst."""
    p = _pen(rng)
    el = Element("car_crash", 200, 100)
    el.extend(car(rng), dx=0, dy=40, s=0.8)
    # Second car, mirrored and dented at the front.
    body = [(200 - x * 0.8, 40 + y * 0.8) for x, y in _CAR_BODY]
    body[-2] = (body[-2][0] + 4, body[-2][1] + 4)
    el.add(p.polyline(body, color="negative"))
    el.add(p.line((200 - 4 * 0.8, 72), (200 - 20 * 0.8, 72)), p.line((200 - 40 * 0.8, 72), (200 - 76 * 0.8, 72)), p.line((200 - 96 * 0.8, 72), (113, 72)))
    for win in ([(40, 12), (54, 12), (54, 24), (30, 24)], [(60, 12), (72, 12), (82, 24), (60, 24)]):
        el.add(p.polyline([(200 - x * 0.8, 40 + y * 0.8) for x, y in win], width=0.7))
    el.add(p.circle(200 - 30 * 0.8, 73.6, 8, fill="ink", fill_opacity=0.15), p.circle(200 - 86 * 0.8, 73.6, 8, fill="ink", fill_opacity=0.15))
    burst = []
    for i in range(16):
        r = 24 if i % 2 == 0 else 10
        a = math.radians(i * 22.5 - 90)
        burst.append((100 + r * math.cos(a), 52 + r * math.sin(a)))
    el.add(p.polyline(burst, closed=True, color="negative", fill="gold", fill_opacity=0.6))
    el.add(p.line((60, 10), (70, 22), width=0.7, color="muted"), p.line((100, 2), (100, 20), width=0.7, color="muted"), p.line((140, 10), (130, 22), width=0.7, color="muted"))
    return el


def _rotated_rect(cx: float, cy: float, w: float, h: float, deg: float) -> list[tuple[float, float]]:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def injury(rng, **_) -> Element:
    """Crossed sticking plasters: an injury."""
    p = _pen(rng)
    el = Element("injury", 90, 90)
    for deg in (45, -45):
        el.add(p.polyline(_rotated_rect(45, 45, 84, 26, deg), closed=True, fill="#F2C9A0", fill_opacity=0.8))
    el.add(p.polyline(_rotated_rect(45, 45, 26, 26, 45), closed=True, width=0.7, fill="paper", fill_opacity=0.6))
    for dx, dy in ((-5, 0), (5, 0), (0, -5), (0, 5)):
        el.add(p.dot(45 + dx, 45 + dy, 1.4))
    return el


def gauge(rng, level: float = 0.85, **_) -> Element:
    """A dial with a red zone: a limit, minimum or maximum."""
    p = _pen(rng)
    el = Element("gauge", 100, 62)
    el.add(p.arc(50, 56, 46, 180, 360, width=1.2))
    el.add(p.arc(50, 56, 46, 315, 360, width=3.0, color="negative"))
    for deg in range(180, 361, 30):
        a = math.radians(deg)
        el.add(p.line((50 + 38 * math.cos(a), 56 + 38 * math.sin(a)), (50 + 44 * math.cos(a), 56 + 44 * math.sin(a)), width=0.7))
    a = math.radians(180 + 180 * max(0.0, min(1.0, level)))
    el.add(p.line((50, 56), (50 + 34 * math.cos(a), 56 + 34 * math.sin(a)), width=1.6, color="negative"), p.dot(50, 56, 3.2))
    return el


OBJECTS: dict[str, Callable[..., Element]] = {
    "coin": coin, "coin_stack": coin_stack, "banknote": banknote, "wallet": wallet, "money_bag": money_bag,
    "piggy_bank": piggy_bank, "bank": bank, "house": house, "building": building, "shop": shop, "factory": factory,
    "phone": phone, "laptop": laptop, "computer": computer, "book": book, "document": document, "car": car,
    "truck": truck, "bicycle": bicycle, "clock": clock, "calendar": calendar, "lightbulb": lightbulb, "gear": gear,
    "cart": cart, "tree": tree, "plant": plant, "heart": heart, "warning": warning, "target": target, "lock": lock,
    "rate_badge": rate_badge, "jail": jail, "handcuffs": handcuffs, "phone_call": phone_call, "fraud_mask": fraud_mask,
    "ai_chip": ai_chip, "bubble": bubble, "key": key, "globe": globe, "graduation_cap": graduation_cap, "school": school, "hospital": hospital,
    "food": food, "water_drop": water_drop, "scale": scale_balance, "trophy": trophy, "question": question_mark,
    "check": check_mark, "cross": cross_mark, "star": star, "cloud": cloud, "sun": sun, "rocket": rocket,
    "magnifier": magnifier, "briefcase": briefcase, "envelope": envelope, "map_pin": map_pin, "pill": pill,
    "battery": battery, "chart_icon": chart_icon, "box": box, "handshake": handshake, "chain": chain_link,
    "shield": shield, "umbrella": umbrella, "car_crash": car_crash, "injury": injury, "gauge": gauge,
}


def make_object(name: str, rng: random.Random, **params) -> Element:
    if name not in OBJECTS:
        raise KeyError(f"Unknown object {name!r}")
    el = OBJECTS[name](rng, **params)
    el.anchors.setdefault("center", (el.width / 2, el.height / 2))
    el.anchors.setdefault("top", (el.width / 2, 0))
    return el
