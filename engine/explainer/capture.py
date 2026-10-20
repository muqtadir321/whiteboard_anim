"""Screenshots with headless Chromium (Playwright): article pages and headline cards.

Article pages are captured from the headline down, with every word's box recorded, so the
renderer can sweep a highlighter over exactly the words the narration quotes. Pages that
are blocked (bot checks, paywalls, consent walls that cannot be closed) are skipped.

EXPLAINER_BROWSER_CHANNEL=chrome uses an installed Google Chrome instead of Playwright's
Chromium (always with a fresh temporary profile).
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

VIEWPORT_W = 820  # CSS px: narrow pages keep text large on a vertical video
DPR = 2
MAX_CAPTURE_H = 3000  # CSS px below the headline
BLOCK_MARKERS = ("access denied", "just a moment", "attention required", "verify you are human", "are you a robot", "captcha",
                 "subscribe to continue", "please enable javascript", "403 forbidden", "page not found")
CONSENT = re.compile(r"^(accept|accept all|i accept|agree|i agree|allow all|got it|ok|okay|continue|close|no thanks|reject all)$", re.I)

WORDS_JS = """
([top, height, maxWords]) => {
  const root = document.querySelector('article') || document.querySelector('main') || document.body;
  const ok = n => {
    const p = n.parentElement;
    if (!p || !/\\S/.test(n.nodeValue)) return NodeFilter.FILTER_REJECT;
    const st = getComputedStyle(p);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) return NodeFilter.FILTER_REJECT;
    if (p.closest('script,style,noscript,nav,footer,aside,button,figcaption')) return NodeFilter.FILTER_REJECT;
    return NodeFilter.FILTER_ACCEPT;
  };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: ok});
  const range = document.createRange();
  const out = [];
  let n;
  while ((n = walker.nextNode())) {
    const re = /\\S+/g; let m;
    while ((m = re.exec(n.nodeValue))) {
      range.setStart(n, m.index); range.setEnd(n, m.index + m[0].length);
      const r = range.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const y = r.top + window.scrollY;
      if (y < top || r.bottom + window.scrollY > top + height) continue;
      out.push([m[0], r.left + window.scrollX, y - top, r.right + window.scrollX, r.bottom + window.scrollY - top]);
      if (out.length >= maxWords) return out;
    }
  }
  return out;
}
"""

CLEAN_JS = """
() => {
  // Remove overlays: fixed/sticky bars, cookie banners, modals, ads; unlock scrolling.
  for (const el of Array.from(document.querySelectorAll('body *'))) {
    const st = getComputedStyle(el);
    if ((st.position === 'fixed' || st.position === 'sticky') && el.tagName !== 'HTML') el.remove();
  }
  for (const sel of ['[id*="cookie" i]', '[class*="cookie" i]', '[id*="consent" i]', '[class*="consent" i]', '[class*="modal" i]',
                     '[class*="paywall" i]', '[class*="newsletter" i]', 'iframe', '[id^="google_ads"]', '[class*="advert" i]']) {
    document.querySelectorAll(sel).forEach(e => { if (e !== document.body) e.remove(); });
  }
  document.documentElement.style.overflow = 'auto'; document.body.style.overflow = 'auto';
}
"""

PARAS_JS = """
() => Array.from(document.querySelectorAll('article p, main p, p'))
  .map(p => p.innerText.replace(/\\s+/g, ' ').trim()).filter(t => t.length > 60).slice(0, 60)
"""


@dataclass
class Capture:
    url: str
    final_url: str
    title: str
    image: Path
    words: list = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)


class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        channel = os.environ.get("EXPLAINER_BROWSER_CHANNEL") or None
        self.browser = self._pw.chromium.launch(headless=True, channel=channel)
        self.context = self.browser.new_context(viewport={"width": VIEWPORT_W, "height": 1400}, device_scale_factor=DPR, locale="en-US",
                                                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                                            "Chrome/130.0.0.0 Safari/537.36"))

    def close(self):
        self.context.close()
        self.browser.close()
        self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _dismiss_consent(self, page) -> None:
        for frame in [page.main_frame] + page.frames[1:4]:
            try:
                btn = frame.get_by_role("button", name=CONSENT).first
                if btn.count():
                    btn.click(timeout=1500)
                    page.wait_for_timeout(600)
                    return
            except Exception:
                continue

    def article(self, url: str, out_png: Path, timeout_ms: int = 35000, max_words: int = 6000) -> Capture | None:
        """Screenshot of an article from its headline down. None if the page is blocked."""
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            title = page.title() or ""
            body = (page.inner_text("body", timeout=5000) or "")[:20000]
            low = (title + " " + body[:1500]).lower()
            if len(body) < 1200 or any(m in low for m in BLOCK_MARKERS):
                return None
            self._dismiss_consent(page)
            page.evaluate(CLEAN_JS)
            page.wait_for_timeout(400)
            top = page.evaluate("""() => { const h = document.querySelector('article h1, main h1, h1');
                                          return h ? Math.max(0, h.getBoundingClientRect().top + window.scrollY - 60) : 0; }""")
            full_h = page.evaluate("() => document.documentElement.scrollHeight")
            height = max(800, min(MAX_CAPTURE_H, full_h - top))
            page.screenshot(path=str(out_png), clip={"x": 0, "y": top, "width": VIEWPORT_W, "height": height}, full_page=True)
            words = page.evaluate(WORDS_JS, [top, height, max_words])
            paragraphs = page.evaluate(PARAS_JS)
            if len(words) < 80:
                return None
            scaled = [[w[0]] + [round(v * DPR, 1) for v in w[1:]] for w in words]
            return Capture(url, page.url, title, out_png, scaled, paragraphs)
        except Exception:
            return None
        finally:
            page.close()

    def news_cards(self, items: list[dict], out_png: Path) -> list[dict]:
        """Headline cards (our own layout, filled from the RSS results). Returns cards with boxes."""
        page = self.context.new_page()
        try:
            page.set_content(cards_html(items))
            page.wait_for_timeout(200)
            boxes = page.evaluate("() => Array.from(document.querySelectorAll('.card')).map(c => { const r = c.getBoundingClientRect(); "
                                  "return [r.left, r.top, r.right, r.bottom]; })")
            page.locator("#cards").screenshot(path=str(out_png), omit_background=True)
            origin = page.evaluate("() => { const r = document.querySelector('#cards').getBoundingClientRect(); return [r.left, r.top]; }")
            out = []
            for it, b in zip(items, boxes):
                box = [round((b[0] - origin[0]) * DPR), round((b[1] - origin[1]) * DPR), round((b[2] - origin[0]) * DPR), round((b[3] - origin[1]) * DPR)]
                out.append({**it, "box": box})
            return out
        finally:
            page.close()


def cards_html(items: list[dict]) -> str:
    cards = []
    for i, it in enumerate(items):
        pub = html.escape(it.get("publisher", ""))
        initial = html.escape((it.get("publisher") or "?")[:1].upper())
        snippet = html.escape(it.get("snippet", ""))
        cards.append(f"""<div class="card" style="margin-left:{(i % 2) * 34}px">
          <div class="src"><span class="ico">{initial}</span>{pub}</div>
          <div class="title">{html.escape(it.get("title", ""))}</div>
          {f'<div class="snip">{snippet}</div>' if snippet else ''}
          <div class="age">{html.escape(it.get("age", ""))}</div></div>""")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      body {{ margin: 0; background: transparent; font-family: Arial, Helvetica, sans-serif; }}
      #cards {{ width: 700px; padding: 10px; }}
      .card {{ background: #202124; color: #e8eaed; border-radius: 10px; padding: 16px 18px; margin: 0 0 14px; width: 620px;
               box-shadow: 0 2px 6px rgba(0,0,0,.25); }}
      .src {{ font-size: 13px; color: #bdc1c6; display: flex; align-items: center; gap: 8px; }}
      .ico {{ width: 20px; height: 20px; border-radius: 50%; background: #5f6368; color: #fff; display: inline-flex;
              align-items: center; justify-content: center; font-size: 12px; font-weight: bold; }}
      .title {{ font-size: 19px; color: #8ab4f8; margin: 8px 0 6px; line-height: 1.3; }}
      .snip {{ font-size: 14px; color: #bdc1c6; line-height: 1.4; }}
      .age {{ font-size: 12px; color: #9aa0a6; margin-top: 8px; }}
    </style></head><body><div id="cards">{''.join(cards)}</div></body></html>"""


def snippet_from(paragraphs: list[str], limit: int = 170) -> str:
    for p in paragraphs:
        if len(p) > 80:
            return p if len(p) <= limit else p[: limit - 1].rsplit(" ", 1)[0] + "…"
    return ""
