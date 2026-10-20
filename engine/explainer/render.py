"""Explainer frame renderer.

A frame is: white board -> active evidence stage (screenshot with camera moves and
highlighter sweeps, or a data chart) -> the scene's words (written as they are spoken)
-> pop-in sprites (icons, logos, figures). Everything is laid out in design units
(1080 x 1920) and scaled to the output size; frames stream to FFmpeg like the
whiteboard renderer, so encoding, captions files and QA are shared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cairo

from ..illustration_composer import DESIGN_W


def rgb(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def back_out(t: float) -> float:
    """Ease with a small overshoot (pop)."""
    t = max(0.0, min(1.0, t))
    s = 1.70158
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


@dataclass
class Word:
    contours: list[list[tuple[float, float]]]  # design coords
    color: str
    t: float  # starts being written
    dur: float
    bbox: tuple[float, float, float, float]
    text: str = ""


@dataclass
class Sprite:
    surf: cairo.ImageSurface  # rendered at output scale
    box: tuple[float, float, float, float]  # design coords
    t: float
    anim: str = "pop"  # pop | fade | none
    end: float | None = None
    name: str = ""


@dataclass
class Highlight:
    rects: list[tuple[float, float, float, float]]  # image pixels
    t0: float
    t1: float
    color: str


@dataclass
class CameraKey:
    t: float  # the camera reaches this view at t
    cx: float  # view centre in image pixels
    cy: float
    zoom: float  # 1 = image width fills the stage width
    move: float = 0.7  # seconds of movement before t


@dataclass
class Stage:
    """A persistent evidence picture shown in a region of the board."""
    kind: str  # news | article | chart | logos
    region: tuple[float, float, float, float]  # design coords
    t_in: float
    t_out: float
    image: cairo.ImageSurface | None = None
    highlights: list[Highlight] = field(default_factory=list)
    keys: list[CameraKey] = field(default_factory=list)
    painter: object = None  # chart painter: paint(ctx, region, t, scale)
    label: str = ""
    fit: str = "width"  # width (full bleed) | contain
    sprites: list[Sprite] = field(default_factory=list)  # logos
    id: str = ""
    source_url: str = ""

    def base_scale(self) -> float:
        x0, y0, x1, y1 = self.region
        if not self.image:
            return 1.0
        iw, ih = self.image.get_width(), self.image.get_height()
        if self.fit == "contain":
            return min((x1 - x0) / iw, (y1 - y0) / ih)
        return (x1 - x0) / iw

    def camera(self, t: float) -> tuple[float, float, float]:
        if not self.keys:
            iw, ih = self.image.get_width(), self.image.get_height()
            return iw / 2, ih / 2, 1.0
        prev = self.keys[0]
        for k in self.keys[1:]:
            if t >= k.t:
                prev = k
                continue
            if t > k.t - k.move:
                e = ease((t - (k.t - k.move)) / k.move)
                return prev.cx + (k.cx - prev.cx) * e, prev.cy + (k.cy - prev.cy) * e, prev.zoom + (k.zoom - prev.zoom) * e
            break
        return prev.cx, prev.cy, prev.zoom


@dataclass
class ExScene:
    scene_id: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)
    sprites: list[Sprite] = field(default_factory=list)
    text_bottom: float = 0.0


@dataclass
class ExStats:
    frames: int = 0
    caption_overflows: int = 0
    cursor_out_of_bounds: int = 0
    max_cursor_jump: float = 0.0
    sampled_ink: list = field(default_factory=list)


class ExplainerRenderer:
    def __init__(self, cfg: dict, width: int, height: int, lang: str):
        self.cfg = cfg
        self.width, self.height = width, height
        self.fps = cfg["video"]["fps"]
        self.scale = width / DESIGN_W
        self.bg = rgb(cfg["animation"]["background"])
        self.lang = lang
        self.captions = None  # explainer writes the narration on the board; no burned-in captions
        self.stats = ExStats()
        self.pop = cfg["animation"]["explainer"]["pop_seconds"]

    # -- painting ------------------------------------------------------------
    def paint_stage(self, ctx: cairo.Context, st: Stage, t: float) -> None:
        s = self.scale
        x0, y0, x1, y1 = st.region
        appear = ease((t - st.t_in) / 0.35)
        ctx.save()
        ctx.rectangle(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s)
        ctx.clip()
        if appear < 1:
            ctx.translate(0, (1 - appear) * 60 * s)
        if st.painter is not None:
            st.painter.paint(ctx, st.region, t, s)
        elif st.image is not None:
            cx, cy, z = st.camera(t)
            k = st.base_scale() * z * s  # image px -> output px
            ox = (x0 + x1) / 2 * s - cx * k
            oy = (y0 + y1) / 2 * s - cy * k
            ctx.save()
            ctx.translate(ox, oy)
            ctx.scale(k, k)
            ctx.set_source_surface(st.image, 0, 0)
            ctx.get_source().set_filter(cairo.FILTER_GOOD)
            ctx.paint_with_alpha(appear)
            for h in st.highlights:
                self.paint_highlight(ctx, h, t)
            ctx.restore()
        for sp in st.sprites:
            self.paint_sprite(ctx, sp, t)
        ctx.restore()
        if st.label and appear > 0.5:
            self.paint_label(ctx, st.label, st.region)

    def paint_highlight(self, ctx: cairo.Context, h: Highlight, t: float) -> None:
        if t < h.t0 or not h.rects:
            return
        total = sum(r[2] - r[0] for r in h.rects) or 1.0
        done = total * ease((t - h.t0) / max(1e-6, h.t1 - h.t0))
        ctx.save()
        ctx.set_operator(cairo.OPERATOR_MULTIPLY)
        r, g, b = rgb(h.color)
        ctx.set_source_rgba(r, g, b, 0.85)
        for x0, y0, x1, y1 in h.rects:
            w = min(x1 - x0, done)
            if w <= 0:
                break
            pad = (y1 - y0) * 0.12
            ctx.rectangle(x0 - pad, y0 - pad, w + 2 * pad, y1 - y0 + 2 * pad)
            ctx.fill()
            done -= x1 - x0
        ctx.restore()

    def paint_label(self, ctx: cairo.Context, text: str, region) -> None:
        s = self.scale
        ctx.save()
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(22 * s)
        ext = ctx.text_extents(text)
        x = region[2] * s - ext.width - 22 * s
        y = region[3] * s - 14 * s
        ctx.set_source_rgba(1, 1, 1, 0.85)
        ctx.rectangle(x - 8 * s, y - ext.height - 8 * s, ext.width + 16 * s, ext.height + 14 * s)
        ctx.fill()
        ctx.set_source_rgb(0.35, 0.35, 0.35)
        ctx.move_to(x, y)
        ctx.show_text(text)
        ctx.restore()

    def paint_word(self, ctx: cairo.Context, w: Word, t: float) -> None:
        if t < w.t:
            return
        s = self.scale
        p = (t - w.t) / max(1e-6, w.dur)
        ctx.save()
        if p < 1:  # written left to right
            x0, y0, x1, y1 = w.bbox
            ctx.rectangle((x0 - 4) * s, (y0 - 30) * s, (x1 - x0 + 8) * p * s, (y1 - y0 + 60) * s)
            ctx.clip()
        ctx.set_source_rgb(*rgb(w.color))
        for c in w.contours:
            if len(c) < 2:
                continue
            ctx.move_to(c[0][0] * s, c[0][1] * s)
            for x, y in c[1:]:
                ctx.line_to(x * s, y * s)
            ctx.close_path()
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        ctx.fill()
        ctx.restore()

    def paint_sprite(self, ctx: cairo.Context, sp: Sprite, t: float) -> None:
        if t < sp.t or (sp.end is not None and t >= sp.end):
            return
        s = self.scale
        x0, y0, x1, y1 = sp.box
        p = (t - sp.t) / self.pop
        k, alpha = 1.0, 1.0
        if sp.anim == "pop" and p < 1:
            k = 0.55 + 0.45 * back_out(p)
            alpha = min(1.0, p * 2.5)
        elif sp.anim == "fade" and p < 1:
            alpha = ease(p)
        cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
        sw, sh = sp.surf.get_width(), sp.surf.get_height()
        f = (x1 - x0) * s / sw
        ctx.save()
        ctx.translate(cx, cy)
        ctx.scale(k * f, k * f)
        ctx.set_source_surface(sp.surf, -sw / 2, -sh / 2)
        ctx.get_source().set_filter(cairo.FILTER_GOOD)
        ctx.paint_with_alpha(alpha)
        ctx.restore()

    def paint_frame(self, ctx: cairo.Context, scene: ExScene | None, stages: list[Stage], t: float) -> None:
        ctx.set_source_rgb(*self.bg)
        ctx.paint()
        for st in stages:
            if st.t_in <= t < st.t_out:
                self.paint_stage(ctx, st, t)
        if scene is None:
            return
        for w in scene.words:
            self.paint_word(ctx, w, t)
        for sp in scene.sprites:
            self.paint_sprite(ctx, sp, t)

    # -- frame loop ----------------------------------------------------------
    def render(self, scenes: tuple[list[ExScene], list[Stage]], total_duration: float, write_frame, sample_times=None) -> ExStats:
        ex_scenes, stages = scenes
        n_frames = int(math.ceil(total_duration * self.fps))
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        ctx = cairo.Context(frame)
        k = 0
        for n in range(n_frames):
            t = n / self.fps
            while k + 1 < len(ex_scenes) and ex_scenes[k + 1].start <= t:
                k += 1
            self.paint_frame(ctx, ex_scenes[k] if ex_scenes else None, stages, t)
            frame.flush()
            write_frame(frame.get_data())
            self.stats.frames += 1
        return self.stats

    def render_still(self, scenes: tuple[list[ExScene], list[Stage]], index: int, path: str) -> None:
        ex_scenes, stages = scenes
        sc = ex_scenes[index]
        t = sc.end - 0.05
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        self.paint_frame(cairo.Context(frame), sc, stages, t)
        frame.write_to_png(path)
