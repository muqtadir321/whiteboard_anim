"""Procedurally drawn drawing-hand (or marker-tip) cursor.

The cursor is vector-drawn with cairo once and cached as an image surface. The
pen tip sits at a known offset so the renderer can place it exactly on the
current drawing position. Rotation follows the stroke direction slightly.
"""

from __future__ import annotations

import math

import cairo


def _hex(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


class HandCursor:
    """A right hand holding a marker; the hand sits below-right of the pen tip."""

    def __init__(self, scale: float = 1.0, style: str = "hand", marker_color: str = "#202020"):
        self.style = style
        self.scale = scale
        s = scale
        self.w, self.h = int(380 * s), int(360 * s)
        self.tip = (int(60 * s), int(80 * s))  # pen tip location inside the surface
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        ctx = cairo.Context(self.surface)
        ctx.translate(*self.tip)
        ctx.scale(s, s)
        self._draw(ctx, marker_color)

    def _draw(self, ctx: cairo.Context, marker_color: str) -> None:
        ink = (0.13, 0.13, 0.13)
        skin = (0.95, 0.80, 0.66)
        ctx.rotate(math.radians(38))  # local +x points from the tip down-right along the marker
        ctx.set_line_width(3.0)

        def shape(fill, path):
            path()
            ctx.set_source_rgb(*fill)
            ctx.fill_preserve()
            ctx.set_source_rgb(*ink)
            ctx.stroke()

        if self.style != "marker":
            # Sleeve and forearm behind everything
            shape((0.18, 0.35, 0.62), lambda: (ctx.move_to(175, -46), ctx.line_to(330, -58), ctx.line_to(330, 62), ctx.line_to(170, 56), ctx.close_path()))
            # Palm / back of the hand
            shape(skin, lambda: (ctx.move_to(70, -30), ctx.curve_to(110, -62, 170, -58, 190, -28),
                                 ctx.curve_to(204, 10, 186, 58, 140, 62), ctx.curve_to(100, 64, 72, 40, 62, 20), ctx.close_path()))
        # Marker: tip, barrel and cap
        ctx.move_to(0, 0)
        ctx.line_to(24, -8)
        ctx.line_to(24, 8)
        ctx.close_path()
        ctx.set_source_rgb(*_hex(marker_color))
        ctx.fill()
        shape((0.97, 0.97, 0.97), lambda: ctx.rectangle(24, -12, 150, 24))
        ctx.rectangle(130, -13, 44, 26)
        ctx.set_source_rgb(*_hex(marker_color))
        ctx.fill()
        if self.style == "marker":
            return
        # Index finger resting on top of the barrel, thumb underneath
        shape(skin, lambda: (ctx.move_to(118, -34), ctx.curve_to(90, -34, 56, -26, 42, -16), ctx.curve_to(34, -10, 38, -2, 48, -4),
                             ctx.curve_to(70, -8, 100, -10, 124, -12), ctx.close_path()))
        shape(skin, lambda: (ctx.move_to(112, 30), ctx.curve_to(88, 30, 62, 24, 52, 14), ctx.curve_to(46, 6, 54, 0, 62, 4),
                             ctx.curve_to(80, 10, 100, 12, 120, 12), ctx.close_path()))
        ctx.set_line_width(2.2)
        for k in range(3):
            ctx.move_to(150 + k * 14, -44 + k * 2)
            ctx.curve_to(156 + k * 14, -38, 157 + k * 14, -32, 154 + k * 14, -26)
            ctx.stroke()

    def paint(self, ctx: cairo.Context, x: float, y: float, angle_deg: float = 0.0, alpha: float = 1.0) -> None:
        if alpha <= 0.01:
            return
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(math.radians(angle_deg))
        ctx.set_source_surface(self.surface, -self.tip[0], -self.tip[1])
        ctx.paint_with_alpha(alpha)
        ctx.restore()
