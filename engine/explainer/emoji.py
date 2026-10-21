"""Colourful drawings for any spoken word: Microsoft Fluent Emoji (flat style, MIT licence).

The whole set (about 1,500 drawings without skin-tone variants) comes as one Iconify JSON file
from the npm CDN and is cached. `EmojiLibrary.match` finds the drawing whose name means the
same as a spoken word or phrase (multilingual embeddings, so "تیل" finds "oil drum"), and
`render` rasterises it with cairo and returns its outlines so the renderer can draw it line
by line like the hand-made doodles.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import cairo
import numpy as np

from .semantic import cache_dir

VERSION = "1.2.6"
URL = f"https://cdn.jsdelivr.net/npm/@iconify-json/fluent-emoji-flat@{VERSION}/icons.json"
LICENSE = "Fluent Emoji by Microsoft, MIT licence (github.com/microsoft/fluentui-emoji)"
# features this small renderer does not draw: such icons are left out
UNSUPPORTED = re.compile(r"Gradient|<filter|<use|<clipPath|<mask|transform=|stroke=|<text|<image")
TONES = ("-light", "-medium-light", "-medium", "-medium-dark", "-dark")
# drawings that rarely say anything about a sentence (symbols, letters, zodiac, clocks, ...)
SKIP_NAME = re.compile(r"button|symbol|sign$|^keycap|^regional|^input|flag-for|oclock|thirty$|face\b|^facepalm|^hundred-points$|^(aries|taurus|gemini|cancer|leo|virgo|"
                       r"libra|scorpio|sagittarius|capricorn|aquarius|pisces|ophiuchus)$|^family-|^couple|^kiss|^people-holding|"
                       r"^(man|woman|person)-(and|with)-|-(man|woman)$|^(man|woman)-")
OUTLINE = (0.13, 0.13, 0.15)


def download(timeout: float = 60) -> Path:
    path = cache_dir() / f"fluent-emoji-flat-{VERSION}.json"
    if not path.exists():
        import urllib.request

        req = urllib.request.Request(URL, headers={"User-Agent": "whiteboard-anim"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # ~9 MB: read it all
            data = resp.read()
        json.loads(data)  # refuse to cache a broken download
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


class EmojiLibrary:
    def __init__(self, icons: dict[str, str], size: float = 32.0):
        self.icons = icons  # name -> svg body
        self.size = size
        self.names = sorted(icons)
        self._vecs = None

    @classmethod
    def from_json(cls, data: dict) -> "EmojiLibrary":
        icons = {}
        raw = data.get("icons", {})
        for name, v in raw.items():
            body = v.get("body", "")
            if UNSUPPORTED.search(body) or SKIP_NAME.search(name):
                continue
            if any(name.endswith(t) and name[: -len(t)] in raw for t in TONES):
                continue
            icons[name] = body
        return cls(icons, float(data.get("width", 32)))

    @classmethod
    def load(cls, log=None) -> "EmojiLibrary | None":
        try:
            return cls.from_json(json.loads(download().read_text(encoding="utf-8")))
        except Exception as exc:
            if log:
                log(f"emoji drawings unavailable ({type(exc).__name__}: {exc})")
            return None

    @staticmethod
    def label(name: str) -> str:
        return name.replace("-", " ")

    def vectors(self, embedder):
        if self._vecs is None:
            self._vecs = embedder.embed_cached([self.label(n) for n in self.names], f"emoji-{VERSION}")
        return self._vecs

    def hubness(self, embedder, k: int = 10):
        """How close each drawing name is to everything: the mean of its k best matches among all words
        used in the drawing names (a fixed, neutral vocabulary). Used for the CSLS correction, so
        "kaaba" or "dna" do not win for any word that the model cannot place."""
        if getattr(self, "_hub", None) is None:
            vocab = sorted({w for n in self.names for w in n.split("-") if len(w) > 2})
            sim = embedder.embed_cached(vocab, f"emoji-vocab-{VERSION}") @ self.vectors(embedder).T
            self._hub = np.sort(sim, axis=0)[-min(k, len(vocab)):, :].mean(axis=0)
        return self._hub

    def scores(self, queries: list[str], embedder, k: int = 10):
        """(cosine, CSLS) of each query against every drawing name."""
        sim = embedder.embed(queries) @ self.vectors(embedder).T
        rq = np.sort(sim, axis=1)[:, -k:].mean(axis=1, keepdims=True)
        return sim, 2 * sim - rq - self.hubness(embedder, k)[None, :]

    def match(self, phrases: list[str], embedder) -> list[tuple[str, float, float]]:
        """For each phrase: (best drawing, its similarity, margin over the phrase's typical match)."""
        if not phrases:
            return []
        q = embedder.embed(phrases)
        sim = q @ self.vectors(embedder).T
        best = sim.argmax(axis=1)
        typical = sim.mean(axis=1)
        return [(self.names[j], float(sim[i, j]), float(sim[i, j] - typical[i])) for i, j in enumerate(best)]

    def render(self, name: str, size_px: float) -> tuple[cairo.ImageSurface, list]:
        return render_svg(self.icons[name], self.size, size_px)


# -- tiny SVG renderer -------------------------------------------------------------------
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def path_polylines(d: str, step: float = 0.6) -> list[tuple[list[tuple[float, float]], bool]]:
    """SVG path data -> polylines (closed flag), all commands incl. arcs; `step` = point spacing in user units."""
    out: list[tuple[list[tuple[float, float]], bool]] = []
    toks = _TOKEN.findall(d)
    i, cmd = 0, ""
    x = y = sx = sy = 0.0
    cur: list[tuple[float, float]] = []
    last_ctrl = None  # (kind, x, y) for S/T reflection

    def flush(closed=False):
        nonlocal cur
        if len(cur) > 1:
            out.append((cur, closed))
        cur = []

    def nums(n):
        nonlocal i
        vals = []
        for _ in range(n):
            if i >= len(toks) or toks[i].isalpha():
                raise ValueError("truncated path")
            vals.append(float(toks[i]))
            i += 1
        return vals

    def curve(pts, n):
        seg = []
        for k in range(1, n + 1):
            t = k / n
            if len(pts) == 4:
                (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
                a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                seg.append((a * x0 + b * x1 + c * x2 + e * x3, a * y0 + b * y1 + c * y2 + e * y3))
            else:
                (x0, y0), (x1, y1), (x2, y2) = pts
                a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                seg.append((a * x0 + b * x1 + c * x2, a * y0 + b * y1 + c * y2))
        return seg

    def steps(*pts):
        ln = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
        return max(2, min(48, int(ln / step) + 1))

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((sx, sy))
                flush(True)
                x, y = sx, sy
                cur = [(x, y)]
                last_ctrl = None
                continue
        elif not cmd:
            raise ValueError("path must start with a command")
        rel, c = cmd.islower(), cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            px, py = nums(2)
            flush()
            x, y = sx, sy = ox + px, oy + py
            cur = [(x, y)]
            cmd = "l" if rel else "L"
            last_ctrl = None
        elif c == "L":
            px, py = nums(2)
            x, y = ox + px, oy + py
            cur.append((x, y))
            last_ctrl = None
        elif c == "H":
            (px,) = nums(1)
            x = (x + px) if rel else px
            cur.append((x, y))
            last_ctrl = None
        elif c == "V":
            (py,) = nums(1)
            y = (y + py) if rel else py
            cur.append((x, y))
            last_ctrl = None
        elif c in "CS":
            if c == "C":
                x1, y1, x2, y2, px, py = nums(6)
                c1 = (ox + x1, oy + y1)
            else:
                x2, y2, px, py = nums(4)
                c1 = (2 * x - last_ctrl[1], 2 * y - last_ctrl[2]) if last_ctrl and last_ctrl[0] == "C" else (x, y)
            c2, end = (ox + x2, oy + y2), (ox + px, oy + py)
            if not cur:
                cur = [(x, y)]
            cur += curve([(x, y), c1, c2, end], steps((x, y), c1, c2, end))
            last_ctrl = ("C", *c2)
            x, y = end
        elif c in "QT":
            if c == "Q":
                x1, y1, px, py = nums(4)
                c1 = (ox + x1, oy + y1)
            else:
                px, py = nums(2)
                c1 = (2 * x - last_ctrl[1], 2 * y - last_ctrl[2]) if last_ctrl and last_ctrl[0] == "Q" else (x, y)
            end = (ox + px, oy + py)
            if not cur:
                cur = [(x, y)]
            cur += curve([(x, y), c1, end], steps((x, y), c1, end))
            last_ctrl = ("Q", *c1)
            x, y = end
        elif c == "A":
            rx, ry, rot = nums(3)
            # a flag is one digit and may be glued to what follows: "011" = 0, 1, 1 and "1.5" = 1, .5
            flags = []
            while len(flags) < 2:
                if i >= len(toks) or toks[i][0] not in "01":
                    raise ValueError("bad arc flags")
                flags.append(int(toks[i][0]))
                if len(toks[i]) > 1:
                    toks[i] = toks[i][1:]
                else:
                    i += 1
            px, py = nums(2)
            end = (ox + px, oy + py)
            if not cur:
                cur = [(x, y)]
            cur += arc_points((x, y), rx, ry, rot, flags[0], flags[1], end, step)
            x, y = end
            last_ctrl = None
        else:
            raise ValueError(f"unsupported path command {cmd}")
    flush()
    return out


def arc_points(p0, rx, ry, rot, large, sweep, p1, step):
    """Elliptical arc (SVG endpoint form) sampled as points, excluding the start point."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    phi = math.radians(rot)
    cphi, sphi = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p, y1p = cphi * dx + sphi * dy, -sphi * dx + cphi * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cphi * cxp - sphi * cyp + (p0[0] + p1[0]) / 2
    cy = sphi * cxp + cphi * cyp + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(2, min(64, int(abs(dt) * max(rx, ry) / step) + 1))
    pts = []
    for k in range(1, n + 1):
        t = t1 + dt * k / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        pts.append((cphi * ex - sphi * ey + cx, sphi * ex + cphi * ey + cy))
    pts[-1] = p1
    return pts


def _color(value: str | None):
    if not value or value == "none":
        return None
    v = value.strip()
    if v.startswith("#"):
        v = v[1:]
        if len(v) == 3:
            v = "".join(ch * 2 for ch in v)
        if len(v) == 6:
            return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))
    named = {"white": (1, 1, 1), "black": (0, 0, 0), "red": (1, 0, 0)}
    return named.get(v.lower())


def shapes(body: str) -> list[tuple[list, tuple, float, bool]]:
    """Filled shapes in drawing order: (polylines, rgb, opacity, even-odd)."""
    root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>')
    out = []

    def walk(el, fill, opacity, evenodd):
        tag = el.tag.split("}")[-1]
        fill = el.get("fill", fill)
        opacity *= float(el.get("opacity", 1)) * float(el.get("fill-opacity", 1))
        evenodd = el.get("fill-rule", el.get("clip-rule", "evenodd" if evenodd else "nonzero")) == "evenodd"
        polys = None
        if tag == "path":
            polys = [p for p, _ in path_polylines(el.get("d", ""))]
        elif tag in ("circle", "ellipse"):
            cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
            rx = float(el.get("r", el.get("rx", 0)))
            ry = float(el.get("r", el.get("ry", 0)))
            n = max(12, int(max(rx, ry) * 6))
            polys = [[(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]]
        elif tag == "rect":
            x0, y0 = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = float(el.get("width", 0)), float(el.get("height", 0))
            polys = [[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0)]]
        if polys:
            rgb = _color(fill if fill is not None else "#000")
            if rgb is not None:
                out.append((polys, rgb, opacity, evenodd))
        for child in el:
            walk(child, fill, opacity, evenodd)

    for child in root:
        walk(child, None, 1.0, False)
    return out


def render_svg(body: str, view: float, size_px: float) -> tuple[cairo.ImageSurface, list]:
    """Rasterise an icon body; also return draw-on strokes in the renderer's format
    (points in surface px, outline rgb, width, fill rgba or None)."""
    pad = 12
    k = size_px / view
    n = int(math.ceil(size_px)) + 2 * pad
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, n, n)
    ctx = cairo.Context(surf)
    ctx.translate(pad, pad)
    strokes = []
    width = max(2.5, size_px / 110)
    for polys, rgb, op, evenodd in shapes(body):
        ctx.new_path()
        for pts in polys:
            ctx.move_to(pts[0][0] * k, pts[0][1] * k)
            for px, py in pts[1:]:
                ctx.line_to(px * k, py * k)
            ctx.close_path()
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD if evenodd else cairo.FILL_RULE_WINDING)
        ctx.set_source_rgba(*rgb, op)
        ctx.fill()
        single = len(polys) == 1
        for pts in polys:
            scaled = [(px * k + pad, py * k + pad) for px, py in pts]
            if len(scaled) > 1 and math.dist(scaled[0], scaled[-1]) + sum(math.dist(a, b) for a, b in zip(scaled, scaled[1:])) > 6:
                strokes.append((scaled, OUTLINE, width, (*rgb, op) if single else None))
    surf.flush()
    return surf, strokes
