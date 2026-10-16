"""Read composed SVG scenes back into an ordered drawing program.

The renderer works from the SVG itself: path geometry decides drawing order
refinements (pen-travel minimisation for hatching) and drawing durations
(path length, turning complexity and importance).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .geometry import polyline_length

SVG_NS = "{http://www.w3.org/2000/svg}"
_CMD = re.compile(r"([MmLlHhVvCcQqZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


@dataclass
class DrawOp:
    kind: str  # stroke | glyph
    item_id: str
    points: list[tuple[float, float]] = field(default_factory=list)  # stroke polyline
    contours: list[list[tuple[float, float]]] = field(default_factory=list)  # glyph outlines
    color: str = "#202020"
    width: float = 6.0
    fill: str | None = None
    fill_opacity: float = 1.0
    closed: bool = False
    role: str = "line"
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    rtl: bool = False
    cost: float = 0.0  # seconds at speed 1.0
    start_point: tuple[float, float] = (0, 0)
    end_point: tuple[float, float] = (0, 0)


@dataclass
class DrawItem:
    id: str
    action: str
    obj: str
    order: int
    anchor_char: int
    emphasis: bool
    bbox: tuple[float, float, float, float]
    ops: list[DrawOp]

    @property
    def cost(self) -> float:
        return sum(op.cost for op in self.ops)


def parse_path(d: str, curve_steps: int = 8) -> list[list[tuple[float, float]]]:
    """Parse SVG path data into polylines (supports M L H V C Q Z, absolute and relative)."""
    tokens = [(m.group(1), m.group(2)) for m in _CMD.finditer(d)]
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = 0.0
    start = (0.0, 0.0)
    i = 0
    cmd = None

    def nums(k):
        nonlocal i
        vals = []
        while len(vals) < k and i < len(tokens) and tokens[i][1] is not None:
            vals.append(float(tokens[i][1]))
            i += 1
        if len(vals) < k:
            raise ValueError("truncated path data")
        return vals

    while i < len(tokens):
        if tokens[i][0]:
            cmd = tokens[i][0]
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append(start)
                    subpaths.append(cur)
                    cur = []
                x, y = start
                continue
        if cmd is None:
            raise ValueError("path data must start with a command")
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = nums(2)
            if rel:
                nx, ny = x + nx, y + ny
            if cur:
                subpaths.append(cur)
            cur = [(nx, ny)]
            x, y = start = (nx, ny)
            cmd = "l" if rel else "L"
        elif c == "L":
            nx, ny = nums(2)
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            cur.append((x, y))
        elif c == "H":
            (nx,) = nums(1)
            x = x + nx if rel else nx
            cur.append((x, y))
        elif c == "V":
            (ny,) = nums(1)
            y = y + ny if rel else ny
            cur.append((x, y))
        elif c in "CQ":
            k = 6 if c == "C" else 4
            v = nums(k)
            if rel:
                v = [v[j] + (x if j % 2 == 0 else y) for j in range(k)]
            p0 = (x, y)
            for s in range(1, curve_steps + 1):
                t = s / curve_steps
                mt = 1 - t
                if c == "C":
                    px = mt**3 * p0[0] + 3 * mt * mt * t * v[0] + 3 * mt * t * t * v[2] + t**3 * v[4]
                    py = mt**3 * p0[1] + 3 * mt * mt * t * v[1] + 3 * mt * t * t * v[3] + t**3 * v[5]
                else:
                    px = mt * mt * p0[0] + 2 * mt * t * v[0] + t * t * v[2]
                    py = mt * mt * p0[1] + 2 * mt * t * v[1] + t * t * v[3]
                cur.append((px, py))
            x, y = v[-2], v[-1]
        else:
            raise ValueError(f"unsupported path command {cmd}")
    if cur:
        subpaths.append(cur)
    return subpaths


def turning(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b, c in zip(points, points[1:], points[2:]):
        a1 = math.atan2(b[1] - a[1], b[0] - a[0])
        a2 = math.atan2(c[1] - b[1], c[0] - b[0])
        d = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
        total += d
    return total


def stroke_cost(points, role: str) -> float:
    length = polyline_length(points)
    speed = {"hatch": 5000.0, "detail": 1400.0}.get(role, 2000.0)  # px per second (design space)
    # Complex, curvy strokes are drawn more slowly than straight lines.
    complexity = 1.0 + min(0.8, turning(points) / 15.0)
    base = 0.03 if role != "hatch" else 0.01
    return base + length / speed * complexity


def glyph_cost(bbox) -> float:
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return 0.02 + (w + h * 0.3) / 1500.0


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def load_program(svg_text: str) -> list[DrawItem]:
    root = ET.fromstring(svg_text)
    items: list[DrawItem] = []
    for g in root.iter(f"{SVG_NS}g"):
        if "item" not in (g.get("class") or ""):
            continue
        iid = g.get("id")
        ops: list[DrawOp] = []
        for child in g:
            cls = child.get("class") or ""
            if child.tag == f"{SVG_NS}path" and "stroke" in cls:
                subs = parse_path(child.get("d", ""))
                role = cls.split()[-1] if len(cls.split()) > 1 else "line"
                for pts in subs:
                    if len(pts) < 2:
                        continue
                    ops.append(DrawOp(
                        "stroke", iid, points=pts, color=child.get("stroke", "#202020"), width=float(child.get("stroke-width", 6)),
                        fill=child.get("data-fill"), fill_opacity=float(child.get("data-fill-opacity", 1)),
                        closed=child.get("d", "").strip().endswith("Z"), role=role, bbox=_bbox(pts), cost=stroke_cost(pts, role),
                        start_point=pts[0], end_point=pts[-1]))
            elif child.tag == f"{SVG_NS}g" and "text" in cls:
                rtl = child.get("data-dir") == "rtl"
                for gp in child:
                    contours = [c for c in parse_path(gp.get("d", "")) if len(c) >= 2]
                    if not contours:
                        continue
                    bb = _bbox([p for c in contours for p in c])
                    sp = (bb[2], (bb[1] + bb[3]) / 2) if rtl else (bb[0], (bb[1] + bb[3]) / 2)
                    ep = (bb[0], (bb[1] + bb[3]) / 2) if rtl else (bb[2], (bb[1] + bb[3]) / 2)
                    ops.append(DrawOp("glyph", iid, contours=contours, color=gp.get("fill", "#202020"), bbox=bb, rtl=rtl, cost=glyph_cost(bb), start_point=sp, end_point=ep))
        ops = optimise_order(ops)
        bbox = tuple(float(v) for v in (g.get("data-bbox") or "0,0,0,0").split(","))
        items.append(DrawItem(iid, (g.get("class") or "").split()[-1], g.get("data-object", ""), int(g.get("data-order", 0)),
                              int(g.get("data-anchor-char", 0)), g.get("data-emphasis") == "1", bbox, ops))
    items.sort(key=lambda it: it.order)
    return items


def optimise_order(ops: list[DrawOp]) -> list[DrawOp]:
    """Keep the generator's natural order, but draw consecutive hatch lines
    boustrophedon-style so the pen does not jump back every line."""
    out: list[DrawOp] = []
    flip = False
    for op in ops:
        if op.kind == "stroke" and op.role == "hatch":
            if flip:
                op.points = list(reversed(op.points))
                op.start_point, op.end_point = op.points[0], op.points[-1]
            flip = not flip
        else:
            flip = False
        out.append(op)
    return out


def travel_cost(a: tuple[float, float], b: tuple[float, float]) -> float:
    d = math.dist(a, b)
    return 0.0 if d < 4 else 0.02 + d / 4000.0
