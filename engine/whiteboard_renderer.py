"""Frame renderer: draws SVG strokes progressively with a moving hand cursor,
applies scene transitions and captions, and streams raw frames to FFmpeg.

Completed strokes are cached on a per-scene "board" surface so each frame only
re-draws the stroke that is currently in progress.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import cairo

from .audio_sync import SceneTimeline
from .caption_renderer import CaptionRenderer
from .hand_cursor import HandCursor
from .illustration_composer import CAPTION_REGION, DESIGN_W, DRAW_REGION
from .svg_draw_order import DrawItem, DrawOp


def _rgb(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


@dataclass
class RenderScene:
    timeline: SceneTimeline
    items: list[DrawItem]


@dataclass
class RenderStats:
    frames: int = 0
    cursor_out_of_bounds: int = 0
    max_cursor_jump: float = 0.0
    blank_frames_after_intro: int = 0
    caption_overflows: int = 0
    sampled_ink: list | None = None


class WhiteboardRenderer:
    def __init__(self, cfg: dict, width: int, height: int, lang: str):
        self.cfg = cfg
        self.width, self.height = width, height
        self.fps = int(cfg["video"]["fps"])
        self.scale = width / DESIGN_W
        self.bg = _rgb(cfg["animation"]["background"])
        self.hand = HandCursor(scale=cfg["animation"]["hand_cursor_scale"] * self.scale, style=cfg["animation"]["hand_cursor_style"],
                               marker_color=cfg["animation"]["primary_stroke"]) if cfg["animation"]["hand_cursor_enabled"] else None
        self.captions = CaptionRenderer(lang, CAPTION_REGION, self.scale) if cfg["video"]["captions_enabled"] else None
        self.progressive = cfg["animation"]["progressive_strokes"]
        self.stats = RenderStats(sampled_ink=[])
        self.rest = (DESIGN_W + 260, DRAW_REGION[3] + 700)  # hand rest position: fully off-screen, lower right

    # -- drawing helpers ---------------------------------------------------
    def _new_board(self) -> cairo.ImageSurface:
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        ctx = cairo.Context(surf)
        ctx.set_source_rgb(*self.bg)
        ctx.paint()
        return surf

    def _ctx(self, surf) -> cairo.Context:
        ctx = cairo.Context(surf)
        ctx.scale(self.scale, self.scale)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        return ctx

    @staticmethod
    def _poly(ctx, pts):
        ctx.move_to(*pts[0])
        for p in pts[1:]:
            ctx.line_to(*p)

    def draw_op(self, ctx: cairo.Context, op: DrawOp, progress: float = 1.0) -> tuple[float, float]:
        """Draw `op` up to `progress` (0..1). Returns the pen position."""
        if op.kind == "glyph":
            x0, y0, x1, y1 = op.bbox
            ctx.save()
            if progress < 1.0:
                w = (x1 - x0) * progress
                if op.rtl:
                    ctx.rectangle(x1 - w, y0 - 4, w + 4, (y1 - y0) + 8)
                else:
                    ctx.rectangle(x0 - 4, y0 - 4, w + 4, (y1 - y0) + 8)
                ctx.clip()
            for c in op.contours:
                self._poly(ctx, c)
                ctx.close_path()
            ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
            ctx.set_source_rgb(*_rgb(op.color))
            ctx.fill()
            ctx.restore()
            px = (x1 - (x1 - x0) * progress) if op.rtl else (x0 + (x1 - x0) * progress)
            py = (y0 + y1) / 2 + math.sin(progress * math.pi * 4) * (y1 - y0) * 0.22
            return (px, py)
        pts = op.points
        if progress >= 1.0:
            if op.fill:
                self._poly(ctx, pts)
                ctx.close_path()
                r, g, b = _rgb(op.fill)
                ctx.set_source_rgba(r, g, b, op.fill_opacity)
                ctx.fill()
            self._poly(ctx, pts)
            if op.closed:
                ctx.close_path()
            ctx.set_source_rgb(*_rgb(op.color))
            ctx.set_line_width(op.width)
            ctx.stroke()
            return pts[-1]
        # partial polyline by arc length
        total = 0.0
        seg = [0.0]
        for a, b in zip(pts, pts[1:]):
            total += math.dist(a, b)
            seg.append(total)
        target = total * max(0.0, progress)
        k = bisect.bisect_left(seg, target)
        k = max(1, min(len(pts) - 1, k))
        a, b = pts[k - 1], pts[k]
        span = seg[k] - seg[k - 1]
        f = (target - seg[k - 1]) / span if span > 0 else 1.0
        tip = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        part = pts[:k] + [tip]
        if len(part) >= 2:
            self._poly(ctx, part)
            ctx.set_source_rgb(*_rgb(op.color))
            ctx.set_line_width(op.width)
            ctx.stroke()
        return tip

    @staticmethod
    def _direction(op: DrawOp, progress: float) -> float:
        if op.kind == "glyph":
            return 0.0
        pts = op.points
        i = min(len(pts) - 2, max(0, int(progress * (len(pts) - 1))))
        a, b = pts[i], pts[i + 1]
        return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))

    # -- main loop ---------------------------------------------------------
    def render(self, scenes: list[RenderScene], total_duration: float, write_frame, sample_times: list[float] | None = None) -> RenderStats:
        n_frames = int(math.ceil(total_duration * self.fps))
        sample_frames = {int(t * self.fps) for t in (sample_times or [])}
        frame = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        fctx = cairo.Context(frame)
        board = self._new_board()
        bctx = self._ctx(board)
        prev_board = None
        scene_idx = -1
        done = 0  # ops completed on the current board
        flat_ops: list[tuple[DrawOp, float, float]] = []
        pen = self.rest
        last_active = -10.0
        smooth_angle = 0.0
        last_hand = None
        starts = [s.timeline.start for s in scenes]
        cues = [c for s in scenes for c in s.timeline.captions]
        cue_starts = [c.start for c in cues]
        for n in range(n_frames):
            t = n / self.fps
            k = max(0, bisect.bisect_right(starts, t) - 1) if scenes else -1
            if k != scene_idx and scenes:
                # flush any unfinished ops of the old scene so the outgoing board is complete
                for op, _, _ in flat_ops[done:]:
                    self.draw_op(bctx, op, 1.0)
                prev_board = board if scene_idx >= 0 else None
                board = self._new_board()
                bctx = self._ctx(board)
                scene_idx = k
                sc = scenes[k]
                flat_ops = [(sc.items[e.item_index].ops[e.op_index], e.t0, e.t1) for e in sc.timeline.ops]
                done = 0
            current = None
            if scenes:
                sc = scenes[scene_idx]
                while done < len(flat_ops) and flat_ops[done][2] <= t:
                    pen = self.draw_op(bctx, flat_ops[done][0], 1.0)
                    last_active = flat_ops[done][2]
                    done += 1
                if done < len(flat_ops) and flat_ops[done][1] <= t:
                    op, t0, t1 = flat_ops[done]
                    current = (op, (t - t0) / max(1e-6, t1 - t0) if self.progressive else 1.0)
            # Compose frame
            fctx.save()
            tl = scenes[scene_idx].timeline if scenes else None
            if tl and prev_board is not None and t < tl.transition_end and tl.transition != "none":
                t0 = getattr(tl, "transition_start", tl.start)
                e = _ease((t - t0) / max(1e-6, tl.transition_end - t0))
                fctx.set_source_rgb(*self.bg)
                fctx.paint()
                if tl.transition == "slide":
                    fctx.set_source_surface(prev_board, -e * self.width, 0)
                    fctx.paint()
                    fctx.set_source_surface(board, (1 - e) * self.width, 0)
                    fctx.paint()
                elif tl.transition == "erase":
                    fctx.set_source_surface(prev_board, 0, 0)
                    fctx.paint()
                    fctx.set_source_rgb(*self.bg)
                    fctx.rectangle(0, 0, self.width, e * self.height)
                    fctx.fill()
                else:  # fade
                    fctx.set_source_surface(board, 0, 0)
                    fctx.paint()
                    fctx.set_source_surface(prev_board, 0, 0)
                    fctx.paint_with_alpha(1 - e)
            else:
                fctx.set_source_surface(board, 0, 0)
                fctx.paint()
            fctx.restore()
            dctx = self._ctx(frame)
            hand_pos = None
            angle = 0.0
            if current:
                op, p = current
                hand_pos = self.draw_op(dctx, op, p)
                angle = self._direction(op, p)
                last_active = t
            elif tl:
                for tr in tl.travel:
                    if tr.t0 <= t < tr.t1:
                        e = _ease((t - tr.t0) / max(1e-6, tr.t1 - tr.t0))
                        hand_pos = (tr.p0[0] + (tr.p1[0] - tr.p0[0]) * e, tr.p0[1] + (tr.p1[1] - tr.p0[1]) * e)
                        last_active = t
                        break
            # Hand cursor: visible while drawing, glides to rest when idle.
            if self.hand:
                idle = t - last_active
                if hand_pos is None:
                    e = _ease((idle - 0.15) / 0.5)
                    hand_pos = (pen[0] + (self.rest[0] - pen[0]) * e, pen[1] + (self.rest[1] - pen[1]) * e)
                else:
                    pen = hand_pos
                # gentle tilt with the stroke direction, smoothed over frames
                target = max(-10.0, min(10.0, math.sin(math.radians(angle)) * 8.0))
                smooth_angle += (target - smooth_angle) * 0.25
                if last_hand is not None:
                    self.stats.max_cursor_jump = max(self.stats.max_cursor_jump, math.dist(last_hand, hand_pos))
                last_hand = hand_pos
                hx, hy = hand_pos
                if idle < 0.15 and not (DRAW_REGION[0] - 30 <= hx <= DRAW_REGION[2] + 30 and DRAW_REGION[1] - 30 <= hy <= DRAW_REGION[3] + 30):
                    self.stats.cursor_out_of_bounds += 1
                fctx.save()
                self.hand.paint(fctx, hx * self.scale, hy * self.scale, smooth_angle)
                fctx.restore()
            # Captions
            if self.captions and cues:
                ci = bisect.bisect_right(cue_starts, t) - 1
                if 0 <= ci < len(cues) and cues[ci].start <= t < cues[ci].end + 0.05:
                    self.captions.paint(fctx, cues[ci].text)
            frame.flush()
            if n in sample_frames:
                self.stats.sampled_ink.append((t, self._ink_ratio(board)))
            write_frame(frame.get_data())
            self.stats.frames += 1
        if self.captions:
            self.stats.caption_overflows = len(self.captions.overflows)
        return self.stats

    def _ink_ratio(self, surf: cairo.ImageSurface) -> float:
        """Fraction of sampled board pixels that differ from the background."""
        import numpy as np

        buf = np.frombuffer(surf.get_data(), dtype=np.uint8).reshape(self.height, surf.get_stride() // 4, 4)[:, : self.width, :3]
        sub = buf[:: 8, :: 8].astype(np.int16)
        bg = np.array([int(self.bg[2] * 255), int(self.bg[1] * 255), int(self.bg[0] * 255)], dtype=np.int16)
        diff = np.abs(sub - bg).sum(axis=2) > 60
        return float(diff.mean())

    def render_still(self, scene: RenderScene, path: str) -> None:
        """Fully drawn scene as PNG (used for previews and QA)."""
        board = self._new_board()
        ctx = self._ctx(board)
        for it in scene.items:
            for op in it.ops:
                self.draw_op(ctx, op, 1.0)
        board.write_to_png(path)
