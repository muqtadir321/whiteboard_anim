"""Data charts drawn from real series (FRED CSV saved by preparation).

The chart is drawn by the engine rather than screenshotted, so the tooltip marker can sit
exactly on the date the narration mentions and glide between dates.
"""

from __future__ import annotations

import datetime as dt

import cairo

from .evidence import Chart
from .render import ease, rgb

LINE = "#2F6FB5"
PANEL = "#EEF2F7"
GRID = "#D3DBE6"


class ChartPainter:
    def __init__(self, chart: Chart, t_in: float, markers: list[tuple[float, str]] | None = None):
        self.chart = chart
        self.t_in = t_in
        self.markers = []  # (time, date, value)
        for tm, d in markers or []:
            hit = chart.nearest(d)
            if hit:
                self.markers.append((tm, hit[0], hit[1]))
        self.markers.sort(key=lambda m: m[0])
        pts = chart.points
        self.d0, self.d1 = pts[0][0], pts[-1][0]
        vals = [v for _, v in pts]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.08 or 1.0
        self.v0, self.v1 = lo - pad, hi + pad

    def add_marker(self, tm: float, date: str) -> bool:
        hit = self.chart.nearest(date)
        if hit:
            self.markers.append((tm, hit[0], hit[1]))
            self.markers.sort(key=lambda m: m[0])
        return hit is not None

    def plot_rect(self, region) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = region
        h = min(y1 - y0, 760)
        return x0 + 84, y0 + 96, x1 - 34, y0 + h - 104  # room below for year labels and the 26 px source line

    def xy(self, plot, d: dt.date, v: float) -> tuple[float, float]:
        px0, py0, px1, py1 = plot
        span = max(1, (self.d1 - self.d0).days)
        x = px0 + (px1 - px0) * (d - self.d0).days / span
        y = py1 - (py1 - py0) * (v - self.v0) / max(1e-9, self.v1 - self.v0)
        return x, y

    def paint(self, ctx: cairo.Context, region, t: float, s: float) -> None:
        x0, y0, x1, y1 = region
        h = min(y1 - y0, 760)
        plot = self.plot_rect(region)
        px0, py0, px1, py1 = plot
        ctx.save()
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        # panel + title band
        ctx.set_source_rgb(*rgb(PANEL))
        ctx.rectangle(x0 * s, y0 * s, (x1 - x0) * s, h * s)
        ctx.fill()
        ctx.set_source_rgb(1, 1, 1)
        ctx.rectangle(px0 * s, py0 * s, (px1 - px0) * s, (py1 - py0) * s)
        ctx.fill()
        ctx.set_source_rgb(0.15, 0.15, 0.15)
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(26 * s)
        title = self.chart.title if len(self.chart.title) <= 62 else self.chart.title[:60] + "…"
        ctx.move_to((x0 + 28) * s, (y0 + 52) * s)
        ctx.show_text(title)
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        # grid + value labels
        ctx.set_font_size(20 * s)
        for i in range(5):
            v = self.v0 + (self.v1 - self.v0) * (i + 0.5) / 5
            _, y = self.xy(plot, self.d0, v)
            ctx.set_source_rgb(*rgb(GRID))
            ctx.set_line_width(1.2 * s)
            ctx.move_to(px0 * s, y * s)
            ctx.line_to(px1 * s, y * s)
            ctx.stroke()
            ctx.set_source_rgb(0.4, 0.4, 0.4)
            ctx.move_to((x0 + 12) * s, (y + 7) * s)
            u = self.chart.units  # units on the axis (SRS: label units)
            ctx.show_text(f"{v:.1f}%" if u == "%" else (f"${v:,.0f}" if u == "$" and v >= 100 else (f"${v:.1f}" if u == "$" else f"{v:,.1f}")))
        # year ticks
        years = range(self.d0.year + 1, self.d1.year + 1)
        step = max(1, len(years) // 7)
        for yr in list(years)[::step]:
            x, _ = self.xy(plot, dt.date(yr, 1, 1), self.v0)
            ctx.set_source_rgb(0.4, 0.4, 0.4)
            ctx.move_to((x - 24) * s, (py1 + 32) * s)
            ctx.show_text(str(yr))
        # the line, drawn in over the first second
        p = ease((t - self.t_in - 0.2) / 1.0)
        pts = self.chart.points
        n = max(2, int(len(pts) * p))
        ctx.set_source_rgb(*rgb(LINE))
        ctx.set_line_width(3.2 * s)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        for i, (d, v) in enumerate(pts[:n]):
            x, y = self.xy(plot, d, v)
            (ctx.move_to if i == 0 else ctx.line_to)(x * s, y * s)
        ctx.stroke()
        # source
        ctx.set_source_rgb(0.35, 0.35, 0.35)
        ctx.set_font_size(26 * s)  # readable on a phone
        ctx.move_to((x0 + 28) * s, (y0 + h - 24) * s)
        ctx.show_text(f"Source: {self.chart.source} · series {self.chart.id}" if self.chart.source else "")
        self.paint_marker(ctx, plot, t, s)
        ctx.restore()

    def paint_marker(self, ctx, plot, t: float, s: float) -> None:
        active = [m for m in self.markers if m[0] <= t]
        if not active:
            return
        tm, d, v = active[-1]
        x, y = self.xy(plot, d, v)
        if len(active) > 1:  # glide from the previous date
            _, pd, pv = active[-2]
            e = ease((t - tm) / 0.6)
            px, py = self.xy(plot, pd, pv)
            x, y = px + (x - px) * e, py + (y - py) * e
        a = ease((t - tm) / 0.25)
        px0, py0, px1, py1 = plot
        ctx.set_source_rgba(0.25, 0.25, 0.25, a)
        ctx.set_line_width(1.6 * s)
        ctx.move_to(x * s, py0 * s)
        ctx.line_to(x * s, py1 * s)
        ctx.stroke()
        ctx.arc(x * s, y * s, 7 * s, 0, 6.2832)
        ctx.fill()
        label = f"{d:%a, %b} {d.day}, {d.year}: {v:.2f}{self.chart.units if self.chart.units == '%' else ''}"
        ctx.set_font_size(22 * s)
        ext = ctx.text_extents(label)
        bw, bh = ext.width / s + 28, 40
        bx = min(max(x - bw / 2, px0), px1 - bw)
        by = max(py0 + 6, y - 70)
        ctx.set_source_rgba(1, 1, 1, a)
        ctx.rectangle(bx * s, by * s, bw * s, bh * s)
        ctx.fill_preserve()
        ctx.set_source_rgba(0.55, 0.55, 0.55, a)
        ctx.set_line_width(1.4 * s)
        ctx.stroke()
        ctx.set_source_rgba(0.1, 0.1, 0.1, a)
        ctx.move_to((bx + 14) * s, (by + 28) * s)
        ctx.show_text(label)

