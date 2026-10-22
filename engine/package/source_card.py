"""Source card: drawn when a page cannot be captured. Not a screenshot, and says so on the card.

It shows only verified metadata (publisher, headline, date, domain) and, when the page text was
retrieved, a short exact excerpt in quotation marks. Word boxes are recorded like a screenshot's,
so the renderer can highlight a quoted phrase on it.
"""

from __future__ import annotations

from pathlib import Path

import cairo

from .urls import short_url

W = 1640  # same width as article screenshots (820 CSS px x 2)
PAD = 72
EXCERPT_WORDS = 60


def _wrap(ctx: cairo.Context, text: str, width: float) -> list[list[str]]:
    lines, cur = [], []
    for word in text.split():
        trial = " ".join(cur + [word])
        if cur and ctx.text_extents(trial).x_advance > width:
            lines.append(cur)
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(cur)
    return lines


def excerpt_of(text: str, words: int = EXCERPT_WORDS) -> str:
    toks = text.split()
    return " ".join(toks[:words]) + (" …" if len(toks) > words else "")


def make_headline_cards(path: Path, items: list[dict], note: str = "") -> list[dict]:
    """Stacked headline cards (publisher, headline, date): our own layout of discovered results,
    labelled as such. Returns the items with "box" in image pixels."""
    cw = 1400
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))
    probe.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    probe.set_font_size(40)
    wrapped = [_wrap(probe, it.get("title", ""), cw - 80)[:3] for it in items]
    heights = [70 + len(w) * 52 + 60 for w in wrapped]
    h = 90 + sum(hh + 28 for hh in heights)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw + 80, h)
    c = cairo.Context(surf)
    c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(26)
    c.set_source_rgb(0.4, 0.4, 0.4)
    c.move_to(20, 50)
    c.show_text((note or "Headlines found during research").upper())
    y, out = 80, []
    for i, (it, lines, hh) in enumerate(zip(items, wrapped, heights)):
        x = 10 + (i % 2) * 60
        c.set_source_rgb(0.13, 0.13, 0.15)
        c.rectangle(x, y, cw, hh)
        c.fill()
        c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(28)
        c.set_source_rgb(0.75, 0.76, 0.78)
        c.move_to(x + 40, y + 50)
        c.show_text(it.get("publisher", "") or short_url(it.get("url", "")))
        c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        c.set_font_size(40)
        c.set_source_rgb(0.54, 0.71, 0.97)
        for k, ln in enumerate(lines):
            c.move_to(x + 40, y + 70 + (k + 1) * 52 - 10)
            c.show_text(" ".join(ln))
        c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(24)
        c.set_source_rgb(0.6, 0.62, 0.64)
        c.move_to(x + 40, y + hh - 22)
        c.show_text((it.get("published") or "")[:10])
        out.append({**it, "box": [x, y, x + cw, y + hh]})
        y += hh + 28
    path.parent.mkdir(parents=True, exist_ok=True)
    surf.write_to_png(str(path))
    return out


def make_card(path: Path, publisher: str, title: str, url: str, published: str = "", excerpt: str = "",
              kind_note: str = "Source card (page not captured)") -> dict:
    """Write the PNG; returns {"words": [[text, x0, y0, x1, y1], ...], "width", "height"} in image pixels."""
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))
    body_w = W - 2 * PAD
    probe.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    probe.set_font_size(64)
    title_lines = _wrap(probe, title or "(untitled)", body_w)[:5]
    probe.select_font_face("Serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    probe.set_font_size(44)
    ex_lines = _wrap(probe, f"“{excerpt}”", body_w)[:12] if excerpt else []
    h = PAD + 50 + 70 + len(title_lines) * 80 + 40 + (len(ex_lines) * 64 + 40 if ex_lines else 0) + 110 + PAD
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, h)
    c = cairo.Context(surf)
    c.set_source_rgb(0.985, 0.98, 0.965)
    c.paint()
    c.set_source_rgb(0.2, 0.2, 0.2)
    c.set_line_width(4)
    c.rectangle(10, 10, W - 20, h - 20)
    c.stroke()
    y = PAD + 30
    c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(30)
    c.set_source_rgb(0.45, 0.45, 0.45)
    c.move_to(PAD, y)
    c.show_text(kind_note.upper())
    y += 70
    c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    c.set_font_size(42)
    c.set_source_rgb(0.08, 0.25, 0.55)
    c.move_to(PAD, y)
    c.show_text((publisher or short_url(url)) + (f"  ·  {published[:10]}" if published else ""))
    words: list[list] = []

    def put(lines, size, lh, rgb, face, slant=cairo.FONT_SLANT_NORMAL, weight=cairo.FONT_WEIGHT_NORMAL):
        nonlocal y
        c.select_font_face(face, slant, weight)
        c.set_font_size(size)
        c.set_source_rgb(*rgb)
        asc = c.font_extents()[0]
        for line in lines:
            y += lh
            x = PAD
            for word in line:
                c.move_to(x, y)
                c.show_text(word)
                adv = c.text_extents(word).x_advance
                words.append([word.strip("“”"), round(x, 1), round(y - asc, 1), round(x + adv, 1), round(y + size * 0.25, 1)])
                x += c.text_extents(word + " ").x_advance

    y += 10
    put(title_lines, 64, 80, (0.1, 0.1, 0.1), "Sans", weight=cairo.FONT_WEIGHT_BOLD)
    y += 40
    if ex_lines:
        put(ex_lines, 44, 64, (0.18, 0.18, 0.18), "Serif")
        y += 40
    c.set_source_rgb(0.75, 0.75, 0.75)
    c.set_line_width(2)
    c.move_to(PAD, y + 20)
    c.line_to(W - PAD, y + 20)
    c.stroke()
    c.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(34)
    c.set_source_rgb(0.3, 0.3, 0.3)
    c.move_to(PAD, y + 80)
    c.show_text(short_url(url, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    surf.write_to_png(str(path))
    return {"words": [w for w in words if w[0]], "width": W, "height": h}
