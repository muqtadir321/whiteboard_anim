"""Caption rendering (shaped, bidi-aware, wrapped) and subtitle file output."""

from __future__ import annotations

import cairo

from . import RTL_CODES
from .text_shaper import LINE_HEIGHT, shape_line, wrap_text

CAPTION_FONT = 56.0
CAPTION_MIN_FONT = 40.0
MAX_LINES = 2


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


class CaptionRenderer:
    def __init__(self, lang: str, region: tuple[float, float, float, float], scale: float):
        self.lang = lang
        self.region = region  # design-space box (x0, y0, x1, y1)
        self.scale = scale
        self._cache: dict[str, tuple[cairo.ImageSurface, float, float]] = {}
        self.overflows: list[str] = []
        self.missing_chars: set[str] = set()

    def layout(self, text: str):
        x0, y0, x1, y1 = self.region
        max_w = (x1 - x0) - 40
        size = CAPTION_FONT
        while True:
            lines = wrap_text(text, size, self.lang, max_w, bold=True)
            shaped = [shape_line(line, size, self.lang, bold=True) for line in lines]
            if (len(shaped) <= MAX_LINES and all(s.width <= max_w for s in shaped)) or size <= CAPTION_MIN_FONT:
                break
            size *= 0.92
        overflow = len(shaped) > MAX_LINES or any(s.width > max_w for s in shaped)
        return shaped, size, overflow

    def surface_for(self, text: str):
        if text in self._cache:
            return self._cache[text]
        shaped, size, overflow = self.layout(text)
        if overflow:
            self.overflows.append(text)
        for s in shaped:
            self.missing_chars.update(s.missing_chars)
        lh = size * LINE_HEIGHT.get(self.lang, 1.3)
        pad_x, pad_y = 34.0, 18.0
        width = max((s.width for s in shaped), default=0) + 2 * pad_x
        height = lh * len(shaped) + 2 * pad_y
        sc = self.scale
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(2, int(width * sc) + 4), max(2, int(height * sc) + 4))
        ctx = cairo.Context(surf)
        ctx.scale(sc, sc)
        r = 22.0
        ctx.new_sub_path()
        ctx.arc(width - r, r, r, -1.5708, 0)
        ctx.arc(width - r, height - r, r, 0, 1.5708)
        ctx.arc(r, height - r, r, 1.5708, 3.1416)
        ctx.arc(r, r, r, 3.1416, 4.7124)
        ctx.close_path()
        ctx.set_source_rgba(0.07, 0.07, 0.09, 0.84)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        for i, line in enumerate(shaped):
            lx = (width - line.width) / 2
            baseline = pad_y + i * lh + (lh - line.height) / 2 + line.ascent
            for g in line.glyphs:
                for contour in g.contours():
                    if len(contour) < 2:
                        continue
                    ctx.move_to(lx + contour[0][0], baseline + contour[0][1])
                    for px, py in contour[1:]:
                        ctx.line_to(lx + px, baseline + py)
                    ctx.close_path()
            ctx.fill()
        result = (surf, width, height)
        self._cache[text] = result
        return result

    def paint(self, ctx: cairo.Context, text: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
        surf, w, h = self.surface_for(text)
        x0, y0, x1, y1 = self.region
        x = (x0 + x1) / 2 - w / 2
        y = y0 + ((y1 - y0) - h) / 2
        ctx.save()
        ctx.scale(1 / self.scale, 1 / self.scale)
        ctx.set_source_surface(surf, x * self.scale, y * self.scale)
        ctx.paint_with_alpha(alpha)
        ctx.restore()
        return (x, y, x + w, y + h)


def _ts(t: float, sep: str = ",") -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(cues, path: str, lang: str) -> None:
    rtl = lang in RTL_CODES
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(cues, 1):
            text = ("‫" + c.text + "‬") if rtl else c.text  # RTL embedding for players without bidi detection
            fh.write(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{text}\n\n")


def write_vtt(cues, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for c in cues:
            fh.write(f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{c.text}\n\n")
