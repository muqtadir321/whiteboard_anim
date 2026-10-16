"""Unicode text shaping, bidirectional layout and glyph outlines.

Text is shaped with HarfBuzz (uharfbuzz) and converted to vector outlines with
fontTools, so English, Roman Urdu, Urdu (Nastaliq) and Pashto (Naskh) all go
through the same vector pipeline used for drawings. Arabic-derived scripts are
never "reversed" character by character: HarfBuzz performs contextual shaping
and the simplified bidi algorithm below orders runs visually, keeping Latin
numbers and abbreviations left-to-right inside right-to-left sentences.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

from .config import REPO_ROOT

FONT_DIR = REPO_ROOT / "assets" / "fonts"

FONT_FILES = {
    "latin": "Kalam-Regular.ttf",
    "latin_bold": "Kalam-Bold.ttf",
    "nastaliq": "NotoNastaliqUrdu.ttf",
    "naskh": "NotoNaskhArabic.ttf",
}

# Per-language font choice for right-to-left runs and visual size correction.
RTL_FONT = {"ur": "nastaliq", "ps": "naskh"}
SIZE_SCALE = {"latin": 1.0, "latin_bold": 1.0, "nastaliq": 1.0, "naskh": 1.05}
LINE_HEIGHT = {"en": 1.25, "roman_urdu": 1.25, "ur": 1.95, "ps": 1.6, "ps_latn": 1.25}


def is_arabic_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x0600 <= cp <= 0x06FF
        or 0x0750 <= cp <= 0x077F
        or 0x08A0 <= cp <= 0x08FF
        or 0xFB50 <= cp <= 0xFDFF
        or 0xFE70 <= cp <= 0xFEFF
    )


def char_class(ch: str) -> str:
    """Return a simplified bidi class: R, L, EN (number), N (neutral)."""
    if is_arabic_char(ch):
        cp = ord(ch)
        if 0x0660 <= cp <= 0x0669 or 0x06F0 <= cp <= 0x06F9:
            return "EN"  # Arabic-Indic / Extended digits are laid out LTR as numbers
        if ch in "٪٫٬":
            return "EN"
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat.startswith("M"):
            return "R"
        return "N"
    if ch.isdigit():
        return "EN"
    cat = unicodedata.category(ch)
    if cat.startswith("L"):
        return "L"
    if cat.startswith("M"):
        return "NSM"
    return "N"


@dataclass
class Run:
    text: str
    rtl: bool
    start: int  # logical character offset


def bidi_runs(text: str, base_rtl: bool) -> list[Run]:
    """Split `text` into directional runs and return them in *visual* order.

    This is a deliberately small subset of UAX #9 covering the cases needed for
    captions and labels: Arabic-script words, embedded Latin words and numbers
    (with attached %, currency symbols, separators) and neutral punctuation.
    """
    if not text:
        return []
    classes = [char_class(c) for c in text]
    # Non-spacing marks take the class of the preceding character.
    for i, c in enumerate(classes):
        if c == "NSM":
            classes[i] = classes[i - 1] if i else ("R" if base_rtl else "L")
    # Number-attached symbols (%, $, decimal points between digits) behave like numbers.
    for i, ch in enumerate(text):
        if classes[i] == "N" and ch in "%$€£.,:/+-×x":
            prev_num = i > 0 and classes[i - 1] == "EN"
            next_num = i + 1 < len(text) and classes[i + 1] == "EN"
            if (ch in "%" and prev_num) or (ch in "$€£" and next_num) or (ch in ".,:/" and prev_num and next_num):
                classes[i] = "EN"
    # Resolve strong direction per character: R -> rtl, L/EN -> ltr
    # (in an RTL paragraph numbers are LTR islands; in LTR everything non-R is LTR).
    resolved: list[bool | None] = []
    for c in classes:
        if c == "R":
            resolved.append(True)
        elif c in ("L", "EN"):
            resolved.append(False)
        else:
            resolved.append(None)
    # Neutrals: same direction on both sides -> that direction, otherwise base.
    n = len(text)
    i = 0
    while i < n:
        if resolved[i] is None:
            j = i
            while j < n and resolved[j] is None:
                j += 1
            before = resolved[i - 1] if i > 0 else base_rtl
            after = resolved[j] if j < n else base_rtl
            fill = before if before == after else base_rtl
            for k in range(i, j):
                resolved[k] = fill
            i = j
        else:
            i += 1
    runs: list[Run] = []
    start = 0
    for i in range(1, n + 1):
        if i == n or resolved[i] != resolved[start]:
            runs.append(Run(text[start:i], bool(resolved[start]), start))
            start = i
    if base_rtl:
        runs.reverse()
    return runs


class _FlattenPen(BasePen):
    """Collects glyph contours as flattened polylines (font units, y up)."""

    def __init__(self, glyphset, steps: int = 6):
        super().__init__(glyphset)
        self.contours: list[list[tuple[float, float]]] = []
        self._cur: list[tuple[float, float]] = []
        self.steps = steps

    def _moveTo(self, pt):
        if self._cur:
            self.contours.append(self._cur)
        self._cur = [pt]

    def _lineTo(self, pt):
        self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        p0 = self._cur[-1]
        for s in range(1, self.steps + 1):
            t = s / self.steps
            mt = 1 - t
            x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
            y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
            self._cur.append((x, y))

    def _qCurveToOne(self, p1, p2):
        p0 = self._cur[-1]
        for s in range(1, self.steps + 1):
            t = s / self.steps
            mt = 1 - t
            x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
            y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
            self._cur.append((x, y))

    def _closePath(self):
        if self._cur:
            self.contours.append(self._cur)
        self._cur = []

    _endPath = _closePath


class LoadedFont:
    def __init__(self, key: str, path: Path):
        self.key = key
        self.path = path
        data = path.read_bytes()
        self.hb_face = hb.Face(data)
        self.hb_font = hb.Font(self.hb_face)
        self.tt = TTFont(str(path), lazy=True)
        self.upem = self.tt["head"].unitsPerEm
        self.glyph_order = self.tt.getGlyphOrder()
        self.glyphset = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        hhea = self.tt["hhea"]
        self.ascent = hhea.ascent
        self.descent = hhea.descent
        self._outline_cache: dict[int, list[list[tuple[float, float]]]] = {}

    def has_char(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def outline(self, gid: int) -> list[list[tuple[float, float]]]:
        if gid not in self._outline_cache:
            pen = _FlattenPen(self.glyphset)
            name = self.glyph_order[gid]
            self.glyphset[name].draw(pen)
            if pen._cur:
                pen.contours.append(pen._cur)
            self._outline_cache[gid] = pen.contours
        return self._outline_cache[gid]


@lru_cache(maxsize=None)
def get_font(key: str) -> LoadedFont:
    path = FONT_DIR / FONT_FILES[key]
    if not path.exists():
        raise FileNotFoundError(f"Required font missing: {path}")
    return LoadedFont(key, path)


@dataclass
class Glyph:
    font: str
    gid: int
    x: float  # pen position in pixels (left edge origin)
    y: float  # baseline offset in pixels (positive = down)
    scale: float  # pixels per font unit
    cluster: int  # logical character index into the source text
    missing: bool = False

    def contours(self) -> list[list[tuple[float, float]]]:
        """Glyph contours in pixel space relative to the line origin (y down)."""
        f = get_font(self.font)
        out = []
        for contour in f.outline(self.gid):
            out.append([(self.x + px * self.scale, self.y - py * self.scale) for px, py in contour])
        return out


@dataclass
class ShapedLine:
    text: str
    glyphs: list[Glyph]
    width: float
    ascent: float
    descent: float
    rtl: bool
    missing_chars: list[str] = field(default_factory=list)

    @property
    def height(self) -> float:
        return self.ascent + self.descent


def font_for_run(run: Run, lang: str, bold: bool = False) -> str:
    if run.rtl or any(is_arabic_char(c) for c in run.text):
        return RTL_FONT.get(lang, "naskh")
    return "latin_bold" if bold else "latin"


def _shape_run(run: Run, font_key: str, size_px: float, x0: float, lang: str):
    f = get_font(font_key)
    size = size_px * SIZE_SCALE[font_key]
    buf = hb.Buffer()
    buf.add_str(run.text)
    buf.guess_segment_properties()
    buf.direction = "rtl" if run.rtl else "ltr"
    if font_key in ("nastaliq", "naskh"):
        buf.script = "Arab"
        buf.language = "ur" if lang == "ur" else ("ps" if lang == "ps" else "ar")
    hb.shape(f.hb_font, buf, {"kern": True, "liga": True})
    scale = size / f.upem
    glyphs = []
    missing = []
    x = x0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        ch_index = run.start + info.cluster
        is_missing = info.codepoint == 0
        src_char = run.text[info.cluster] if info.cluster < len(run.text) else ""
        if is_missing and src_char and not src_char.isspace() and unicodedata.category(src_char)[0] not in "CZ":
            missing.append(src_char)
        glyphs.append(
            Glyph(
                font=font_key,
                gid=info.codepoint,
                x=x + pos.x_offset * scale,
                y=-pos.y_offset * scale,
                scale=scale,
                cluster=ch_index,
                missing=is_missing,
            )
        )
        x += pos.x_advance * scale
    return glyphs, x - x0, f.ascent * scale, -f.descent * scale, missing


def shape_line(text: str, size_px: float, lang: str, bold: bool = False, base_rtl: bool | None = None) -> ShapedLine:
    if base_rtl is None:
        base_rtl = lang in ("ur", "ps") and any(is_arabic_char(c) for c in text)
    glyphs: list[Glyph] = []
    x = 0.0
    asc = desc = 0.0
    missing: list[str] = []
    for run in bidi_runs(text, base_rtl):
        key = font_for_run(run, lang, bold)
        # Characters the chosen font lacks fall back to the other script font.
        g, w, a, d, miss = _shape_run(run, key, size_px, x, lang)
        if miss:
            alt = "naskh" if key != "naskh" else "latin"
            g2, w2, a2, d2, miss2 = _shape_run(run, alt, size_px, x, lang)
            if len(miss2) < len(miss):
                g, w, a, d, miss = g2, w2, a2, d2, miss2
        glyphs.extend(g)
        missing.extend(miss)
        x += w
        asc, desc = max(asc, a), max(desc, d)
    return ShapedLine(text, glyphs, x, asc, desc, base_rtl, missing)


def measure(text: str, size_px: float, lang: str, bold: bool = False) -> float:
    return shape_line(text, size_px, lang, bold).width


def wrap_text(text: str, size_px: float, lang: str, max_width: float, bold: bool = False) -> list[str]:
    """Greedy word wrap using real shaped widths."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        if measure(candidate, size_px, lang, bold) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_text(
    text: str, lang: str, max_width: float, max_height: float, start_size: float, min_size: float = 18, bold: bool = False, max_lines: int = 3
) -> tuple[list[ShapedLine], float, float]:
    """Find the largest size at which `text` fits the box. Returns (lines, size, line_height)."""
    size = start_size
    while True:
        wrapped = wrap_text(text, size, lang, max_width, bold)
        shaped = [shape_line(line, size, lang, bold) for line in wrapped]
        lh = size * LINE_HEIGHT.get(lang, 1.3)
        total_h = lh * len(shaped)
        widest = max((s.width for s in shaped), default=0)
        if (len(shaped) <= max_lines and total_h <= max_height and widest <= max_width) or size <= min_size:
            return shaped, size, lh
        size = max(min_size, size * 0.9)
