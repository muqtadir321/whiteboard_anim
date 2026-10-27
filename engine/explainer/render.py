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

from ..hand_cursor import HandCursor
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
    pop: bool = False  # key amounts bounce in instead of being written
    underline: bool = False  # hand-drawn swoosh under the word once it is there
    end: float | None = None  # leaves the board (beat labels go with their drawing)
    rtl: bool = False  # written right to left (Urdu, Pashto)
    missing: list = field(default_factory=list)  # characters the font could not draw
    wobble: float = 0.0  # after popping in, a gentle scale pulse (hook words), as a fraction of the size
    phase: float = 0.0


@dataclass
class Sprite:
    surf: cairo.ImageSurface  # rendered at output scale
    box: tuple[float, float, float, float]  # design coords
    t: float
    anim: str = "pop"  # pop | fade | draw | none
    end: float | None = None
    name: str = ""
    # anim "draw": outlines drawn line by line, then filled (surf pixel coords: points, rgb, width, fill rgba)
    strokes: list = field(default_factory=list)
    draw: float = 0.0  # seconds the drawing takes
    float_amp: float = 0.0  # gentle up-and-down drift (design units) so the board never freezes
    phase: float = 0.0
    rock: float = 0.0  # degrees of side-to-side rocking about the feet (Mo on the hook card)


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
    source_ids: list[str] = field(default_factory=list)  # research package ledger ids
    exit: bool = True  # animate out (False when it stays to the end of the video)
    focus: tuple | None = None  # image box kept bright while the rest fades back (the headline card that matches)
    focus_t: float = 0.0

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
        # a marker tip that sits exactly where the current stroke is being drawn
        pen = cfg["animation"]["explainer"].get("pen_cursor", True) and cfg["animation"]["hand_cursor_enabled"]
        self.pen = HandCursor(self.scale * 0.62, "marker", cfg["animation"]["primary_stroke"]) if pen else None  # visible at phone size
        self._pen_at: tuple[float, float] | None = None
        ex = cfg["animation"]["explainer"]
        self.karaoke: list = []  # captions.Chunk list, set by the pipeline from the layout
        self.hook_end = 0.0  # the hook card is up until here (captions start after it)
        self.loop_seconds = float(ex.get("loop_end_seconds", 0.6)) if ex.get("loop_end", True) else 0.0
        self.motion = float(ex.get("board_motion", 0.018))  # slow push in/out of the whole board
        self.cap_highlight = rgb(ex["highlight_color"])

    # -- painting ------------------------------------------------------------
    def paint_stage(self, ctx: cairo.Context, st: Stage, t: float) -> None:
        s = self.scale
        x0, y0, x1, y1 = st.region
        a_in = ease((t - st.t_in) / 0.32)
        a_out = ease((st.t_out - t) / 0.28) if st.exit else 1.0
        alpha = min(a_in, a_out)
        k = 1.0 + 0.07 * (1 - a_in) - 0.04 * (1 - a_out)  # punch in, shrink away
        cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
        ctx.save()
        ctx.push_group()
        ctx.translate(cx, cy)
        ctx.scale(k, k)
        ctx.translate(-cx, -cy)
        ctx.rectangle(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s)
        ctx.clip()
        if st.painter is not None:
            st.painter.paint(ctx, st.region, t, s)
        elif st.image is not None:
            cam_x, cam_y, z = st.camera(t)
            f = st.base_scale() * z * s  # image px -> output px
            ctx.save()
            ctx.translate(cx - cam_x * f, cy - cam_y * f)
            ctx.scale(f, f)
            ctx.set_source_surface(st.image, 0, 0)
            ctx.get_source().set_filter(cairo.FILTER_GOOD)
            ctx.paint()
            for h in st.highlights:
                self.paint_highlight(ctx, h, t)
            if st.focus is not None and t > st.focus_t:
                fx0, fy0, fx1, fy1 = st.focus
                ctx.rectangle(0, 0, st.image.get_width(), st.image.get_height())
                ctx.rectangle(fx0 - 8, fy0 - 8, fx1 - fx0 + 16, fy1 - fy0 + 16)
                ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                ctx.set_source_rgba(1, 1, 1, 0.82 * ease((t - st.focus_t) / 0.35))
                ctx.fill()
                ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
            ctx.restore()
        for sp in st.sprites:
            self.paint_sprite(ctx, sp, t)
        ctx.pop_group_to_source()
        ctx.paint_with_alpha(alpha)
        ctx.restore()
        if st.label and alpha > 0.5:
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
        ctx.set_font_size(26 * s)  # readable on a phone
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

    def _glyphs(self, ctx: cairo.Context, w: Word) -> None:
        s = self.scale
        for c in w.contours:
            if len(c) < 2:
                continue
            ctx.move_to(c[0][0] * s, c[0][1] * s)
            for x, y in c[1:]:
                ctx.line_to(x * s, y * s)
            ctx.close_path()
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        ctx.fill()

    def paint_word(self, ctx: cairo.Context, w: Word, t: float) -> None:
        if t < w.t or (w.end is not None and t >= w.end):
            return
        s = self.scale
        age = t - w.t
        x0, y0, x1, y1 = w.bbox
        ctx.save()
        ctx.set_source_rgb(*rgb(w.color))
        if w.pop:
            p = age / 0.32
            k = 0.5 + 0.5 * back_out(p) if p < 1 else 1.0 + w.wobble * math.sin(2 * math.pi * 1.4 * age - w.phase)
            cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
            ctx.translate(cx, cy)
            ctx.scale(k, k)
            ctx.rotate(math.radians(-4) * max(0.0, 1 - p))
            ctx.translate(-cx, -cy)
        else:
            p = age / max(1e-6, w.dur)
            if p < 1:  # written left to right (Urdu: right to left)
                wdt = (x1 - x0 + 8) * p
                left = (x1 + 4 - wdt) if w.rtl else (x0 - 4)
                ctx.rectangle(left * s, (y0 - 30) * s, wdt * s, (y1 - y0 + 60) * s)
                ctx.clip()
                self._pen_at = ((left if w.rtl else left + wdt) * s, (y0 + y1) / 2 * s)
        self._glyphs(ctx, w)
        ctx.restore()
        if w.underline and age > 0.3:
            q = ease((age - 0.3) / 0.35)
            ctx.save()
            ctx.set_source_rgb(*rgb(w.color))
            ctx.set_line_width(7 * s)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            y = y1 + 16
            n = 24
            for i in range(int(n * q) + 1):
                u = i / n
                px = x0 + (x1 - x0) * u
                py = y + math.sin(u * math.pi) * 10 - u * 6
                (ctx.move_to if i == 0 else ctx.line_to)(px * s, py * s)
            ctx.stroke()
            ctx.restore()

    def _draw_strokes(self, ctx: cairo.Context, sp: Sprite, p: float) -> tuple[float, float] | None:
        """Doodle being drawn: strokes appear one after another, each filled once closed.
        Returns the point the pen has reached (surface pixels)."""
        if not hasattr(sp, "_lens"):
            sp._lens = [sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) for pts, *_ in sp.strokes]
        target = sum(sp._lens) * p
        done = 0.0
        tip = None
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        for (pts, color, width, fill), ln in zip(sp.strokes, sp._lens):
            if done >= target:
                break
            part = min(1.0, (target - done) / ln) if ln else 1.0
            done += ln
            ctx.move_to(*pts[0])
            if part >= 1.0:
                for pt in pts[1:]:
                    ctx.line_to(*pt)
                tip = pts[-1]
                if fill:
                    ctx.close_path()
                    ctx.set_source_rgba(*fill)
                    ctx.fill_preserve()
            else:
                left = ln * part
                for a, b in zip(pts, pts[1:]):
                    d = math.dist(a, b)
                    if d >= left:
                        f = left / d if d else 0
                        tip = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                        ctx.line_to(*tip)
                        break
                    ctx.line_to(*b)
                    left -= d
            ctx.set_source_rgb(*color)
            ctx.set_line_width(width)
            ctx.stroke()
        return tip

    def paint_sprite(self, ctx: cairo.Context, sp: Sprite, t: float) -> None:
        if t < sp.t or (sp.end is not None and t >= sp.end):
            return
        s = self.scale
        x0, y0, x1, y1 = sp.box
        age = t - sp.t
        out = ease((sp.end - t) / 0.22) if sp.end is not None else 1.0
        bob = sp.float_amp * math.sin(age * 2.3 + sp.phase) if sp.float_amp else 0.0
        cx, cy = (x0 + x1) / 2 * s, ((y0 + y1) / 2 + bob) * s
        sw, sh = sp.surf.get_width(), sp.surf.get_height()
        f = (x1 - x0) * s / sw
        if sp.anim == "draw" and sp.strokes and age < sp.draw:
            p = age / sp.draw
            ctx.save()
            ctx.translate(cx - sw / 2 * f, cy - sh / 2 * f)
            ctx.scale(f, f)
            tip = self._draw_strokes(ctx, sp, min(1.0, p / 0.85))
            if tip is not None and p < 0.85:
                self._pen_at = (cx - sw / 2 * f + tip[0] * f, cy - sh / 2 * f + tip[1] * f)
            if p > 0.85:  # labels and final colours settle in
                ctx.set_source_surface(sp.surf, 0, 0)
                ctx.paint_with_alpha((p - 0.85) / 0.15)
            ctx.restore()
            return
        p = age / self.pop
        k, alpha = 1.0, 1.0
        if sp.anim == "pop" and p < 1:
            k = 0.55 + 0.45 * back_out(p)
            alpha = min(1.0, p * 2.5)
        elif sp.anim == "fade" and p < 1:
            alpha = ease(p)
        k *= 0.8 + 0.2 * out
        alpha *= out
        ctx.save()
        if sp.rock:  # rock about the feet
            fy = y1 * s
            ctx.translate(cx, fy)
            ctx.rotate(math.radians(sp.rock * math.sin(age * 5.5 + sp.phase)))
            ctx.translate(-cx, -fy)
        ctx.translate(cx, cy)
        ctx.scale(k * f, k * f)
        ctx.set_source_surface(sp.surf, -sw / 2, -sh / 2)
        ctx.get_source().set_filter(cairo.FILTER_GOOD)
        ctx.paint_with_alpha(alpha)
        ctx.restore()

    def paint_caption(self, ctx: cairo.Context, t: float) -> None:
        """The caption chunk being spoken: white words with a thick black outline (readable on the board and on
        screenshots alike), the word being said in yellow."""
        if t < self.hook_end:
            return
        ch = next((c for c in reversed(self.karaoke) if c.t0 <= t < c.t1), None)
        if ch is None:
            return
        s = self.scale
        x0, y0, x1, y1 = ch.box
        k = 0.9 + 0.1 * back_out((t - ch.t0) / 0.14)
        cx, cy = (x0 + x1) / 2 * s, (y0 + y1) / 2 * s
        said = [w for w in ch.words if w.t0 <= t]
        cur = said[-1] if said else None
        size = (ch.words[0].bbox[3] - ch.words[0].bbox[1]) if ch.words else 90
        ctx.save()
        ctx.translate(cx, cy)
        ctx.scale(k, k)
        ctx.translate(-cx, -cy)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        for w in ch.words:
            for c in w.contours:
                if len(c) > 1:
                    ctx.move_to(c[0][0] * s, c[0][1] * s)
                    for x, y in c[1:]:
                        ctx.line_to(x * s, y * s)
                    ctx.close_path()
            ctx.set_source_rgb(0.05, 0.05, 0.05)
            ctx.set_line_width(size * 0.13 * s)
            ctx.stroke_preserve()
            ctx.set_source_rgb(*(self.cap_highlight if w is cur else (1.0, 1.0, 1.0)))
            ctx.fill()
        ctx.restore()

    def paint_frame(self, ctx: cairo.Context, scene: ExScene | None, stages: list[Stage], t: float) -> None:
        ctx.set_source_rgb(*self.bg)
        ctx.paint()
        self._pen_at = None
        ctx.save()
        if self.motion:  # the board breathes (about 2% over ten seconds): no frame is ever frozen
            k = 1.0 + self.motion * (0.5 - 0.5 * math.cos(2 * math.pi * t / 10.0))
            cx, cy = self.width / 2, self.height * 0.42
            ctx.translate(cx, cy)
            ctx.scale(k, k)
            ctx.translate(-cx, -cy)
        for st in stages:
            if st.t_in <= t < st.t_out:
                self.paint_stage(ctx, st, t)
        if scene is not None:
            for w in scene.words:
                self.paint_word(ctx, w, t)
            for sp in scene.sprites:
                self.paint_sprite(ctx, sp, t)
            if self.pen is not None and self._pen_at is not None:  # on top of the board, at the stroke's end
                x, y = self._pen_at
                self.pen.paint(ctx, x, y)
                if not (0 <= x <= self.width and 0 <= y <= self.height):
                    self.stats.cursor_out_of_bounds += 1
        ctx.restore()
        self.paint_caption(ctx, t)  # captions stay still while the board moves

    # -- frame loop ----------------------------------------------------------
    def render(self, scenes: tuple[list[ExScene], list[Stage]], total_duration: float, write_frame, sample_times=None) -> ExStats:
        ex_scenes, stages = scenes
        n_frames = int(math.ceil(total_duration * self.fps))
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        ctx = cairo.Context(frame)
        # loop ending: the last moments cross-fade into the hook frame, so a replay starts seamlessly
        loop_from = total_duration - self.loop_seconds if self.loop_seconds and self.hook_end > 0 else None
        hook_frame = None
        k = 0
        for n in range(n_frames):
            t = n / self.fps
            while k + 1 < len(ex_scenes) and ex_scenes[k + 1].start <= t:
                k += 1
            self.paint_frame(ctx, ex_scenes[k] if ex_scenes else None, stages, t)
            if loop_from is not None and t >= loop_from and ex_scenes:
                if hook_frame is None:
                    hook_frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
                    self.paint_frame(cairo.Context(hook_frame), ex_scenes[0], stages, max(0.0, self.hook_end - 0.3))
                ctx.set_source_surface(hook_frame, 0, 0)
                ctx.paint_with_alpha(ease((t - loop_from) / self.loop_seconds))
            frame.flush()
            write_frame(frame.get_data())
            self.stats.frames += 1
        return self.stats

    def render_at(self, scenes: tuple[list[ExScene], list[Stage]], t: float, path: str) -> None:
        """One frame at time t as a PNG (thumbnails)."""
        ex_scenes, stages = scenes
        sc = next((s for s in reversed(ex_scenes) if s.start <= t), ex_scenes[0] if ex_scenes else None)
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        self.paint_frame(cairo.Context(frame), sc, stages, t)
        frame.write_to_png(path)

    def render_still(self, scenes: tuple[list[ExScene], list[Stage]], index: int, path: str) -> None:
        ex_scenes, stages = scenes
        sc = ex_scenes[index]
        t = sc.end - 0.05
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        self.paint_frame(cairo.Context(frame), sc, stages, t)
        frame.write_to_png(path)
