"""sources.txt: the evidence shown in a video, ready to paste into a YouTube/Facebook description."""

from __future__ import annotations

from pathlib import Path

from .evidence import Evidence


def write_sources(path: Path, evidence: Evidence, used: set[str]) -> None:
    lines = []
    seen = set()
    for a_id, art in evidence.articles.items():
        if f"article:{a_id}" in used and art.url not in seen:
            seen.add(art.url)
            lines.append(f"- {art.publisher}: {art.title} {art.url}".replace("  ", " "))
    for c_id, ch in evidence.charts.items():
        if f"chart:{c_id}" in used and ch.url not in seen:
            seen.add(ch.url)
            lines.append(f"- {ch.source}: {ch.title} {ch.url}")
    if "news" in used:
        for card in evidence.news_cards:
            if card.get("url") and card["url"] not in seen:
                seen.add(card["url"])
                lines.append(f"- {card.get('publisher', '')}: {card.get('title', '')} {card['url']}")
    logos = [lg["name"] for lg in evidence.logos.values() if f"logo:{lg['name']}" in used]
    if not lines and not logos:
        return
    text = "Sources:\n" + "\n".join(lines) + "\n"
    if logos:
        text += f"\nLogos shown for identification only ({', '.join(logos)}); trademarks belong to their owners.\n"
    path.write_text(text, encoding="utf-8")
