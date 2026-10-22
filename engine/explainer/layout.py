"""Explainer layout: scene plan + word timing + cues + evidence -> scenes and stages.

Board layout (design units, 1080 x 1920):
  TEXT_ZONE   narration words, written as they are spoken; numbers big in red/green
  STAGE_ZONE  evidence (headline cards, article page, chart, logos) or large icons
  overlay     small icon/figure over the evidence (bottom corner), like the reference style

An evidence stage starts at its cue word and stays for that sentence (animation.explainer.
evidence_hold: sentence, or paragraph), then leaves; in between, doodles matching the
narration are drawn line by line. Doodles that would overlap a screenshot wait until it
has gone, or sit small in the corner while it is up.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

import cairo

from ..illustration_composer import Style, build_visual
from ..numbers import CURRENCY_WORDS
from ..object_generator import OBJECTS
from ..text_shaper import shape_line
from .charts import ChartPainter
from .cues import Cue
from .emoji import EmojiLibrary
from .evidence import Evidence
from .render import CameraKey, ExScene, Highlight, Sprite, Stage, Word

TEXT_ZONE = (60.0, 120.0, 1020.0, 640.0)
STAGE_ZONE = (0.0, 670.0, 1080.0, 1800.0)
OVERLAY_BOX = (780.0, 1500.0, 1050.0, 1780.0)
INK = "#1E1E1E"
LOW_WORDS = {"low", "lower", "cheap", "cheaper", "only", "down", "fell", "dropped", "drop", "under", "below",
             "kam", "sasta", "sirf", "neeche", "gir"}
SKIP_ICONS: set[str] = set()
# Doodles only: diagrams and charts from the whiteboard planner read poorly as small pop-ins.
ICON_EXTRA = {"person", "worker", "boss", "student", "teacher", "police", "money_growth", "debt", "loss", "price_tag", "trend_arrow"}
SPECIFIC = {"phone_call", "handcuffs", "jail", "fraud_mask", "ai_chip", "bubble", "police", "rate_badge", "house", "bank", "lock",
            "money_bag", "piggy_bank", "price_tag", "trend_arrow", "money_growth", "loss", "debt"}
ICON_SWAP = {"people_group": "person", "pie_chart": "rate_badge", "percent_badge": "rate_badge", "percent": "rate_badge", "%": "rate_badge", "rate": "rate_badge"}
ICON_TINT = {  # main fill for icons that come without one
    "house": "#C98B5B", "bank": "#B9BEC5", "document": "#F3E7C9", "money_bag": "#8DB27A", "coin": "#E9B949",
    "coin_stack": "#E9B949", "banknote": "#9CCB86", "lock": "#4A4A4A", "building": "#9AA3AD", "shop": "#E7A86B",
    "car": "#6FA8DC", "calendar": "#F4F4F4", "briefcase": "#A47148", "piggy_bank": "#F2A7B8", "wallet": "#A47148",
    "phone": "#3D4A5C", "laptop": "#6FA8DC", "envelope": "#F3E7C9", "warning": "#F5C542", "clock": "#F4F4F4",
}
VIVID_FILL = 0.92
_EMOJI: list = []  # loaded once: [EmojiLibrary | None]


def emoji_library(log=print) -> EmojiLibrary | None:
    if not _EMOJI:
        _EMOJI.append(EmojiLibrary.load(log))
    return _EMOJI[0]


def is_doodle(name: str) -> bool:
    return bool(name) and name not in SKIP_ICONS and (name in OBJECTS or name in ICON_EXTRA)


@dataclass
class TimedCue:
    cue: Cue
    t: float
    paragraph_end: float
    sentence_end: float = 0.0


@dataclass
class Layout:
    scenes: list[ExScene]
    stages: list[Stage]
    warnings: list[str] = field(default_factory=list)
    highlights: dict = field(default_factory=lambda: {"requested": 0, "matched": 0})
    used_evidence: set = field(default_factory=set)
    missing_glyphs: set = field(default_factory=set)


# -- timing helpers -----------------------------------------------------------------
def word_times(scene: dict, start: float, end: float) -> list[tuple[float, float]]:
    tokens = scene["narration_text"].split()
    words = scene.get("words") or []
    if len(words) == len(tokens) and all("start" in w for w in words):
        return [(w["start"], w["end"]) for w in words]
    weights = [len(t) + 2 for t in tokens]
    total = sum(weights) or 1
    out, t = [], start
    for wgt in weights:
        d = (end - start) * wgt / total
        out.append((t, t + d))
        t += d
    return out


def time_cues(cues: list[Cue], sentences, paragraphs: list[int] | None, offset: float) -> list[TimedCue]:
    """Absolute time (with intro offset) of each cue's word and the end of its paragraph."""
    flat = []  # (start, sentence index)
    for si, ts in enumerate(sentences):
        n = len(ts.text.split())
        ws = ts.words if len(ts.words or []) == n else [{"start": ts.start + (ts.end - ts.start) * k / max(1, n)} for k in range(n)]
        flat.extend((w["start"], si) for w in ws)
    para_end: dict[int, float] = {}
    for si, ts in enumerate(sentences):
        p = paragraphs[si] if paragraphs and si < len(paragraphs) else si
        para_end[p] = max(para_end.get(p, 0.0), ts.end)
    out = []
    for c in cues:
        if not flat:
            break
        start, si = flat[min(c.word, len(flat) - 1)]
        p = paragraphs[si] if paragraphs and si < len(paragraphs) else si
        out.append(TimedCue(c, start + offset, para_end[p] + offset, sentences[si].end + offset))
    return sorted(out, key=lambda tc: tc.t)


# -- drawing helpers ------------------------------------------------------------------
def element_surface(el, size_px: float, style: Style, lang: str, tint: str | None = None) -> tuple[cairo.ImageSurface, list]:
    """Rasterise a vector Element as a colourful doodle: bold fills, dark outlines.
    Also returns the strokes (surface pixels) so the renderer can draw it line by line."""
    w, h = max(1.0, el.width), max(1.0, el.height)
    k = size_px / max(w, h)
    pad = 12
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w * k) + 2 * pad, int(h * k) + 2 * pad)
    ctx = cairo.Context(surf)
    ctx.translate(pad, pad)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    has_fill = any(st.fill for st in el.strokes)
    tinted = False
    strokes = []
    for st in el.strokes:
        if len(st.points) < 2:
            continue
        ctx.move_to(st.points[0][0] * k, st.points[0][1] * k)
        for x, y in st.points[1:]:
            ctx.line_to(x * k, y * k)
        fill = st.fill
        if not fill and not has_fill and tint and st.closed and not tinted:
            fill, tinted = tint, True
        fill_rgba = None
        if fill:
            ctx.close_path()
            r, g, b = _rgb(style.color(fill))
            fill_rgba = (r, g, b, VIVID_FILL)
            ctx.set_source_rgba(*fill_rgba)
            ctx.fill_preserve()
        color = _rgb(style.color(st.color))
        width = max(2.5, st.width * size_px / 42)
        ctx.set_source_rgb(*color)
        ctx.set_line_width(width)
        ctx.stroke()
        strokes.append(([(x * k + pad, y * k + pad) for x, y in st.points], color, width, fill_rgba))
    for ts in el.texts:
        x0, y0, x1, y1 = (v * k for v in ts.box)
        size = (y1 - y0) * ts.size_hint
        line = shape_line(ts.text, size, ts.lang or lang, ts.bold)
        ox = x0 + ((x1 - x0) - line.width) / 2
        oy = y0 + ((y1 - y0) + line.ascent - line.descent) / 2
        ctx.set_source_rgb(*_rgb(style.color(ts.color)))
        for g in line.glyphs:
            for c in g.contours():
                if len(c) > 1:
                    ctx.move_to(ox + c[0][0], oy + c[0][1])
                    for x, y in c[1:]:
                        ctx.line_to(ox + x, oy + y)
                    ctx.close_path()
        ctx.fill()
    surf.flush()
    return surf, strokes


def _rgb(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def png_surface(path) -> cairo.ImageSurface:
    return cairo.ImageSurface.create_from_png(str(path))


def fit_box(sw: float, sh: float, box) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    k = min((x1 - x0) / sw, (y1 - y0) / sh)
    w, h = sw * k, sh * k
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


# -- builder --------------------------------------------------------------------------
class ExplainerLayout:
    def __init__(self, cfg: dict, lang: str, evidence: Evidence | None, scale: float):
        self.cfg = cfg
        self.ex = cfg["animation"]["explainer"]
        self.lang = lang
        self.evidence = evidence or Evidence(root=None)
        self.scale = scale  # output px per design unit
        self.style = Style.from_config(cfg)
        self.warnings: list[str] = []
        self.missing_glyphs: set[str] = set()
        self.text_spans: list[tuple[float, float, float]] = []  # (start, end, text bottom) per scene

    # words ----------------------------------------------------------------------
    @staticmethod
    def board_tokens(scene: dict, times: list[tuple[float, float]]):
        """Words as written on the board: amounts collapse to their display form
        ("seven percent" -> "7%") and are flagged for the big number style."""
        text = scene["narration_text"]
        tokens = text.split()
        big = [q for q in scene.get("quantities", [])
               if (q.get("kind") in ("percent", "currency", "multiplier", "ratio")
                   or (q.get("kind") == "count" and (any(ch.isdigit() for ch in q.get("source", "")) or q.get("value", 0) >= 1000)))
               and not (q.get("kind") == "currency" and q.get("value") == 1)]  # "one dollar" is not a figure worth showing
        starts, pos = [], 0
        for tok in tokens:
            pos = text.find(tok, pos)
            starts.append(pos)
            pos += len(tok)
        out, flags, out_times = [], [], []
        i = 0
        while i < len(tokens):
            q = next((q for q in big if q["span"][0] < starts[i] + len(tokens[i]) and starts[i] < q["span"][1]), None)
            if q is None:
                out.append(tokens[i])
                flags.append(False)
                out_times.append(times[min(i, len(times) - 1)])
                i += 1
                continue
            j = i
            while j + 1 < len(tokens) and starts[j + 1] < q["span"][1]:
                j += 1
            # "one trillion dollars" -> "$1 trillion": the spoken currency word is already in the display
            if q.get("kind") == "currency" and j + 1 < len(tokens) and tokens[j + 1].rstrip(".,!?;:").lower() in CURRENCY_WORDS:
                j += 1
            tail = tokens[j][len(tokens[j].rstrip(".,!?;:")):]
            out.append(q["display"] + tail)
            flags.append(True)
            out_times.append((times[min(i, len(times) - 1)][0], times[min(j, len(times) - 1)][1]))
            i = j + 1
        return out, flags, out_times

    def layout_words(self, scene: dict, times: list[tuple[float, float]]) -> tuple[list[Word], float]:
        tokens, is_num, times = self.board_tokens(scene, times)
        colors = []
        for i, tok in enumerate(tokens):
            if not is_num[i]:
                colors.append(INK)
                continue
            before = {t.lower().strip(",.") for t in tokens[max(0, i - 3):i]}
            colors.append(self.ex["low_number_color"] if before & LOW_WORDS else self.ex["number_color"])
        if not self.ex["write_narration"]:  # no subtitles: only the key amounts pop up on the board
            keep = [i for i, n in enumerate(is_num) if n]
            tokens, is_num, times, colors = ([seq[i] for i in keep] for seq in (tokens, is_num, times, colors))
            tokens = [t.rstrip(".,!?;:،۔؟") for t in tokens]  # an amount on its own needs no punctuation
            if not tokens:
                return [], TEXT_ZONE[1]
        x0, y0, x1, y1 = TEXT_ZONE
        k = 1.0
        while True:
            sizes = [(self.ex["number_size"] if n else self.ex["text_size"]) * k for n in is_num]
            shaped = [shape_line(tok, sz, self.lang) for tok, sz in zip(tokens, sizes)]
            for i, sl in enumerate(shaped):
                if sl.width > x1 - x0:  # one word wider than the board ("Rs 4,057.7 crore"): shrink just that word
                    sizes[i] *= (x1 - x0) * 0.98 / sl.width
                    shaped[i] = shape_line(tokens[i], sizes[i], self.lang)
            for sl in shaped:
                self.missing_glyphs.update(sl.missing_chars)
            lines, cur, cur_w = [], [], 0.0
            for i, sl in enumerate(shaped):
                space = sizes[i] * 0.28
                w = sl.width + (space if cur else 0)
                if cur and cur_w + w > x1 - x0:
                    lines.append(cur)
                    cur, cur_w = [], 0.0
                    w = sl.width
                cur.append(i)
                cur_w += w
            if cur:
                lines.append(cur)
            heights = [max(sizes[i] for i in ln) * 1.12 for ln in lines]
            if sum(heights) <= y1 - y0 or k <= 0.5:
                break
            k *= 0.9
        words: list[Word] = []
        y = y0
        for ln, lh in zip(lines, heights):
            widths = [shaped[i].width for i in ln]
            gaps = [sizes[i] * 0.28 for i in ln[1:]]
            x = x0 + ((x1 - x0) - sum(widths) - sum(gaps)) / 2
            base = y + lh * 0.8
            for j, i in enumerate(ln):
                sl = shaped[i]
                contours = [[(x + px, base + py) for px, py in c] for g in sl.glyphs for c in g.contours()]
                t0, t1 = times[i] if i < len(times) else times[-1]
                big = is_num[i] and not self.ex["write_narration"]
                words.append(Word(contours, colors[i], t0, max(0.08, min(0.3, (t1 - t0) * 0.8)),
                                  (x, base - sl.ascent, x + sl.width, base + sl.descent), tokens[i], pop=big, underline=big))
                x += sl.width + (gaps[j] if j < len(gaps) else 0)
            y += lh
        if words and not self.ex["write_narration"]:
            y += 40  # room for the underline swoosh above any screenshot
        return words, y

    # icons ----------------------------------------------------------------------
    def icon_sprite(self, name: str, params: dict, box, t: float, seed: str) -> Sprite | None:
        name = ICON_SWAP.get(name, name)
        lib = emoji_library() if name not in OBJECTS and name not in ICON_EXTRA else None
        if lib is not None and name in lib.icons:
            size = max(box[2] - box[0], box[3] - box[1]) * self.scale
            surf, strokes = lib.render(name, size)
            draw = min(1.2, 0.5 + 0.025 * len(strokes))
            return Sprite(surf, fit_box(surf.get_width(), surf.get_height(), box), t, "draw", name=name, strokes=strokes, draw=draw,
                          float_amp=6.0, phase=random.Random(seed).uniform(0, 6.28))
        if name == "rate_badge":
            params = {}
        try:
            el, _ = build_visual(name, random.Random(seed), params)
        except (KeyError, TypeError):
            self.warnings.append(f"unknown icon {name!r}")
            return None
        size = max(box[2] - box[0], box[3] - box[1]) * self.scale
        surf, strokes = element_surface(el, size, self.style, self.lang, ICON_TINT.get(name))
        draw = min(1.1, 0.45 + 0.03 * len(strokes))
        rng = random.Random(seed)
        return Sprite(surf, fit_box(surf.get_width(), surf.get_height(), box), t, "draw", name=name, strokes=strokes, draw=draw,
                      float_amp=6.0, phase=rng.uniform(0, 6.28))

    def arrow_sprite(self, a: Sprite, b: Sprite, t: float) -> Sprite:
        """Hand-drawn arrow from one doodle to the next (cause -> effect)."""
        from ..geometry import Element, Pen

        def centre(sp):
            return (sp.box[0] + sp.box[2]) / 2, (sp.box[1] + sp.box[3]) / 2

        (ax, ay), (bx, by) = centre(a), centre(b)
        # start/end a little outside each doodle
        def edge(cx, cy, box, tx, ty):
            hw, hh = (box[2] - box[0]) / 2 * 0.8, (box[3] - box[1]) / 2 * 0.8
            dx, dy = tx - cx, ty - cy
            f = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
            return cx + dx * f, cy + dy * f

        p0, p1 = edge(ax, ay, a.box, bx, by), edge(bx, by, b.box, ax, ay)
        x0, y0 = min(p0[0], p1[0]) - 30, min(p0[1], p1[1]) - 30
        w, h = abs(p1[0] - p0[0]) + 60, abs(p1[1] - p0[1]) + 60
        el = Element("arrow", w, h)
        line_w = 336 / max(w, h)  # about 8 px on screen whatever the arrow's length
        el.add(*Pen(random.Random(int(ax + by)), wobble=0.6).arrow((p0[0] - x0, p0[1] - y0), (p1[0] - x0, p1[1] - y0), head=26, curve=0.15, width=line_w))
        surf, strokes = element_surface(el, max(w, h) * self.scale, self.style, self.lang)
        return Sprite(surf, fit_box(surf.get_width(), surf.get_height(), (x0, y0, x0 + w, y0 + h)), t, "draw", name="arrow",
                      strokes=strokes, draw=0.45)

    def scene_icons(self, scene: dict, times, s0: float, s1: float, forced: list[tuple[float, str]], text_bottom: float,
                    stages: list[Stage]) -> list[Sprite]:
        text = scene["narration_text"]
        tokens = text.split()
        char_word, pos = [], 0
        for i, tok in enumerate(tokens):
            pos = text.find(tok, pos)
            char_word.append((pos, i))
            pos += len(tok)

        def t_at(ch):
            idx = 0
            for c, i in char_word:
                if c <= ch:
                    idx = i
            return times[idx][0] if times else s0

        picks: list[tuple[str, dict, float]] = [(ICON_SWAP.get(n, n), {}, t) for t, n in forced]
        for a in sorted(scene["visual_actions"], key=lambda a: a["draw_order"]):
            name = ICON_SWAP.get(a.get("object", ""), a.get("object", ""))
            if a["action"] == "draw" and is_doodle(name) and all(name != p[0] for p in picks):
                picks.append((name, a.get("params") or {}, t_at(a.get("anchor_char", 0))))
        if not picks:  # never a blank board: someone thinking it over
            picks.append(("person", {"pose": "think"}, times[0][0] if times else s0))
        # drawings tagged on a spoken word first, then the doodles that say the most about this topic
        # (a bubble beats a generic laptop); shown in the order they are said
        n_forced = len(forced)
        order = {id(p): (0 if k < n_forced else 1 if p[0] in SPECIFIC else 2) for k, p in enumerate(picks)}
        picks = sorted(picks, key=lambda p: order[id(p)])[: int(self.ex["max_icons"])]
        picks.sort(key=lambda p: p[2])

        def stage_at(t):
            return next((st for st in stages if st.t_in <= t < st.t_out), None)

        free, overlay = [], None
        for name, params, t in picks:
            t = max(s0, t)
            st = stage_at(t)
            if st is not None and st.t_out < s1 - 0.6:
                gone = st.t_out
                t = gone - 0.25  # start drawing while the screenshot shrinks away
                st = stage_at(gone + 0.01)
            if st is None:
                free.append((name, params, t))
            elif overlay is None:
                overlay = (name, params, t)
        out: list[Sprite] = []
        if overlay:
            sp = self.icon_sprite(overlay[0], overlay[1], OVERLAY_BOX, overlay[2], f"{scene['scene_id']}:{overlay[0]}")
            if sp:
                out.append(sp)
        top = max(300.0, text_bottom + 60)
        bottom = 1760.0
        avail = bottom - top
        if len(free) == 1:
            sz = min(640.0, avail)
            boxes = [(540 - sz / 2, top + (avail - sz) / 2, 540 + sz / 2, top + (avail + sz) / 2)]
        elif len(free) == 2:
            sz = min(470.0, avail * 0.62)
            boxes = [(60.0, top + 20, 60 + sz, top + 20 + sz), (1020 - sz, bottom - sz, 1020.0, bottom)]
        else:
            big, small = min(470.0, avail * 0.5), min(380.0, avail * 0.4)
            boxes = [(540 - big / 2, top, 540 + big / 2, top + big), (50.0, bottom - small, 50 + small, bottom),
                     (1030 - small, bottom - small, 1030.0, bottom)]
        drawn: list[Sprite] = []
        for (name, params, t), box in zip(free, boxes):
            sp = self.icon_sprite(name, params, box, t, f"{scene['scene_id']}:{name}")
            if sp:
                # leave before a screenshot arrives
                sp.end = next((st.t_in for st in sorted(stages, key=lambda x: x.t_in) if t < st.t_in < s1), None)
                drawn.append(sp)
        for a, b in zip(drawn, drawn[1:]):
            arrow = self.arrow_sprite(a, b, max(a.t, b.t) + 0.15)
            ends = [e for e in (a.end, b.end) if e is not None]
            arrow.end = min(ends) if ends else None
            if arrow.end is None or arrow.end > arrow.t + 0.3:
                drawn.append(arrow)
        return out + drawn

    # stages ---------------------------------------------------------------------
    def build_stages(self, timed: list[TimedCue], end_time: float, warnings: list[str], layout: Layout,
                     narration_end: float | None = None) -> list[Stage]:
        ev = self.evidence
        stages: list[Stage] = []
        cur: Stage | None = None

        def start(st: Stage) -> Stage:
            nonlocal cur
            if cur is not None:
                cur.t_out = min(cur.t_out, st.t_in)
            stages.append(st)
            cur = st
            return st

        for tc in timed:
            hold = tc.paragraph_end if self.ex["evidence_hold"] == "paragraph" else tc.sentence_end
            c, t, t_out = tc.cue, tc.t, max(hold + 0.35, tc.t + float(self.ex["min_evidence_seconds"]))
            if c.kind == "icon":
                continue
            if c.kind == "news":
                if not ev.news_image:
                    warnings.append("[news] cue but no headline cards were captured")
                    continue
                img = png_surface(ev.news_image)
                st = start(Stage("news", self.region_for(t, t_out), t, t_out, image=img, fit="contain", id="news",
                                 label="Headlines found during research" if self.ex["source_label"] else "",
                                 source_ids=[c["source_id"] for c in ev.news_cards if c.get("source_id")]))
                cx, cy, _ = self.top_view(st)  # cards hang from the top of the stage, then a slow push-in
                st.keys = [CameraKey(t, cx, cy, 1.0), CameraKey(t_out, cx, cy / 1.07, 1.07, move=max(0.5, t_out - t))]
                layout.used_evidence.add("news")
            elif c.kind == "article":
                art = ev.articles.get(c.ref)
                if not art:
                    warnings.append(f"[article {c.ref}] cue but article {c.ref} was not captured")
                    continue
                if cur is None or cur.id != f"article:{c.ref}" or t >= cur.t_out + 1.2:
                    img = png_surface(art.image)
                    st = start(Stage("article", self.region_for(t, t_out), t, t_out, image=img, id=f"article:{c.ref}",
                                     label=self.article_label(art) if self.ex["source_label"] else "",
                                     source_url=art.url, source_ids=[art.source_id] if art.source_id else []))
                    st.keys = [CameraKey(t, *self.top_view(st))]
                    layout.used_evidence.add(f"article:{c.ref}")
                st = cur
                st.t_out = max(st.t_out, t_out)
                if c.quote:
                    layout.highlights["requested"] += 1
                    rects = art.find_quote(c.quote)
                    if rects:
                        layout.highlights["matched"] += 1
                        n = len(st.highlights)
                        color = self.ex["highlight_alt_color"] if n % 2 else self.ex["highlight_color"]
                        dur = min(2.2, 0.35 + 0.09 * len(c.quote.split()))
                        st.highlights.append(Highlight(rects, t + 0.45, t + 0.45 + dur, color))
                        if art.capture != "source_card":  # a card already fits the stage: zooming would crop its text
                            st.keys.append(CameraKey(t + 0.4, *self.focus_view(st, rects)))
                    else:
                        warnings.append(f"quote not found on article {c.ref}: {c.quote[:60]!r}")
            elif c.kind == "chart":
                chart = ev.charts.get(c.ref)
                if not chart:
                    warnings.append(f"[chart {c.ref}] cue but the series was not downloaded")
                    continue
                if cur is None or cur.id != f"chart:{c.ref}" or t >= cur.t_out + 1.2:
                    start(Stage("chart", self.region_for(t, t_out), t, t_out, painter=ChartPainter(chart, t), id=f"chart:{c.ref}",
                                source_url=chart.url, source_ids=[chart.source_id] if chart.source_id else []))
                    layout.used_evidence.add(f"chart:{c.ref}")
                cur.t_out = max(cur.t_out, t_out)
                if c.date and not cur.painter.add_marker(t + 0.2, c.date):
                    warnings.append(f"chart {c.ref}: no data near {c.date}")
            elif c.kind == "logo":
                lg = ev.logo(c.ref)
                if not lg:
                    warnings.append(f"[logo {c.ref}] cue but no logo was found")
                    continue
                if cur is None or cur.kind != "logos" or t >= cur.t_out + 1.2:
                    start(Stage("logos", self.region_for(t, t_out), t, t_out, id="logos",
                                label="Logos shown for identification only" if self.ex["source_label"] else ""))
                cur.t_out = max(cur.t_out, t_out)
                cur.sprites.append(Sprite(png_surface(lg["path"]), (0, 0, 1, 1), t, "pop", name=lg["name"]))
                layout.used_evidence.add(f"logo:{lg['name']}")
        if narration_end is None:
            narration_end = max((max(tc.sentence_end, tc.paragraph_end) for tc in timed), default=0.0)
        for st in stages:
            # evidence still up when the narration ends stays through the outro
            if st.t_out >= narration_end - 1e-3:
                st.t_out, st.exit = end_time, False
            else:
                st.t_out = min(st.t_out, end_time)
            if st.kind == "logos":
                self.arrange_logos(st)
        return stages

    @staticmethod
    def article_label(art) -> str:
        """Publisher, date and domain; a drawn source card says it is not a screenshot."""
        from ..package.urls import short_url

        parts = [art.publisher or short_url(art.url, 30)]
        if art.published:
            parts.append(art.published[:10])
        if art.publisher and art.url:
            parts.append(short_url(art.url, 30).split("/")[0])
        return ("Source card: " if art.capture == "source_card" else "Source: ") + " · ".join(parts)

    def keep_board_busy(self, layout: Layout) -> None:
        """Never a blank board between sentences: a scene's drawings stay until the next scene's first
        drawing starts (then cross-fade). After a screenshot leaves, the next drawing starts at once and
        is drawn more slowly, so it is finished on the word that names it."""
        stages = layout.stages

        def stage_at(t):
            return next((st for st in stages if st.t_in <= t < st.t_out), None)

        def next_stage_in(t0, t1):
            return min((st.t_in for st in stages if t0 < st.t_in < t1), default=None)

        carry: list[Sprite] = []
        for sc in layout.scenes:
            drawn = sorted((sp for sp in sc.sprites if sp.anim == "draw"), key=lambda sp: sp.t)
            first = min((sp.t for sp in sc.sprites), default=None)
            st0 = stage_at(sc.start)
            if carry and st0 is None and first is not None and first > sc.start + 0.05:
                until = min(first + 0.3, next_stage_in(sc.start, sc.end) or sc.end)
                sc.sprites = [replace(sp, t=sc.start - 5.0, end=until) for sp in carry] + sc.sprites
            elif drawn:
                free_from = st0.t_out - 0.25 if st0 is not None else sc.start  # overlaps the screenshot's exit
                lead = drawn[0]
                if lead.t > free_from + 0.2 and stage_at(free_from + 0.3) is None:
                    finish = lead.t + lead.draw
                    lead.t = free_from
                    lead.draw = min(max(lead.draw, finish - free_from), 2.5)
            carry = [replace(sp, end=None) for sp in sc.sprites
                     if sp.t < sc.end and (sp.end is None or sp.end >= sc.end - 0.01) and stage_at(sc.end - 0.01) is None]

    def region_for(self, t0: float, t1: float) -> tuple[float, float, float, float]:
        """Stage region below the tallest text block shown while the stage is visible."""
        bottoms = [b for s0, s1, b in self.text_spans if s0 < t1 and t0 < s1]
        top = min(900.0, max(260.0, max(bottoms, default=STAGE_ZONE[1] - 50) + 50))
        return (STAGE_ZONE[0], top, STAGE_ZONE[2], STAGE_ZONE[3])

    def top_view(self, st: Stage) -> tuple[float, float, float]:
        iw = st.image.get_width()
        x0, y0, x1, y1 = st.region
        view_h = (y1 - y0) / st.base_scale()
        return iw / 2, view_h / 2, 1.0

    def focus_view(self, st: Stage, rects) -> tuple[float, float, float]:
        iw, ih = st.image.get_width(), st.image.get_height()
        x0, y0, x1, y1 = st.region
        bx0, by0 = min(r[0] for r in rects), min(r[1] for r in rects)
        bx1, by1 = max(r[2] for r in rects), max(r[3] for r in rects)
        z = max(1.0, min(float(self.ex["max_zoom"]), 0.92 * iw / max(1.0, bx1 - bx0)))
        half_w = iw / 2 / z
        half_h = (y1 - y0) / st.base_scale() / 2 / z
        cx = min(max((bx0 + bx1) / 2, half_w), iw - half_w)
        # keep the highlighted lines in the upper third so overlays below do not hide them
        cy = min(max((by0 + by1) / 2 + half_h * 0.3, half_h), max(half_h, ih - half_h))
        return cx, cy, z

    def arrange_logos(self, st: Stage) -> None:
        x0, y0, x1, y1 = st.region
        n = len(st.sprites)
        slot_h = min(320.0, (y1 - y0 - 80) / max(1, n))
        top = y0 + 40
        for i, sp in enumerate(st.sprites):
            box = (x0 + 120, top + i * slot_h + 16, x1 - 120, top + (i + 1) * slot_h - 16)
            sp.box = fit_box(sp.surf.get_width(), sp.surf.get_height(), box)

    # all ------------------------------------------------------------------------
    def build(self, plan: dict, timed: list[TimedCue], offset: float, total: float) -> Layout:
        layout = Layout([], [], self.warnings)
        n = len(plan["scenes"])
        texts = []
        for i, scene in enumerate(plan["scenes"]):
            s0 = 0.0 if i == 0 else scene["start_seconds"] + offset
            s1 = total if i == n - 1 else scene["end_seconds"] + offset
            times = [(a + offset, b + offset) for a, b in word_times(scene, scene["start_seconds"], scene["end_seconds"])]
            words, bottom = self.layout_words(scene, times)
            texts.append((scene, s0, s1, times, words, bottom))
            self.text_spans.append((s0, s1, bottom))
        spoken_end = max((sc["end_seconds"] for sc in plan["scenes"]), default=0.0) + offset
        layout.stages = self.build_stages(timed, total, layout.warnings, layout, narration_end=spoken_end)
        icon_cues = [(tc.t, tc.cue.ref) for tc in timed if tc.cue.kind == "icon"]
        for scene, s0, s1, times, words, bottom in texts:
            forced = [(t, name) for t, name in icon_cues if s0 <= t < s1]
            sprites = self.scene_icons(scene, times, s0, s1, forced, bottom, layout.stages)
            layout.scenes.append(ExScene(scene["scene_id"], s0, s1, words, sprites, bottom))
        self.keep_board_busy(layout)
        layout.missing_glyphs = self.missing_glyphs
        return layout
