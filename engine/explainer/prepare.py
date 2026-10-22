"""Prepare a topic folder: articles, screenshots, charts, logos and draft scripts.

    python -m engine.explainer.prepare --topic "why buying a house got harder" [--region US]
        [--languages english,urdu] [--model qwen2.5:7b] [--out input/topics]

Writes input/topics/<date>-<slug>/ with english.txt / urdu.txt / roman_urdu.txt (drafts to review),
evidence/ (screenshots + manifest.json), notes.md (what to check) and topic.yaml.
Render it with:  python -m engine.cli run --topic-dir input/topics/<date>-<slug>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import yaml

from ..config import REPO_ROOT
from . import draft, fred, logos
from .cues import extract_cues, has_unresolved
from .evidence import Evidence
from .sources import NewsItem, search_news

STOP = {"the", "and", "for", "with", "why", "how", "what", "that", "this", "from", "are", "was", "got", "get", "its", "your", "you"}
GENERIC = {"about", "using", "used", "tactics", "ways", "things", "really", "going", "will", "could", "should", "news", "latest", "today"}


def slugify(text: str, n: int = 6) -> str:
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split() if w not in STOP]
    return "-".join(words[:n]) or "topic"


def topic_keys(topic: str) -> set[str]:
    """4-letter stems of the topic's content words, plus acronyms such as AI or IMF."""
    stems = {w[:4] for w in re.findall(r"[a-z]{4,}", topic.lower()) if w not in STOP and w not in GENERIC}
    return stems | set(re.findall(r"\b[A-Z]{2,5}\b", topic))


def relevance(topic: str, title: str) -> int:
    """Distinct topic keywords found in a headline."""
    low = title.lower()
    return sum(1 for k in topic_keys(topic) if (re.search(rf"\b{k}\b", title) if k.isupper() else k in low))


def relevant(topic: str, items: list[NewsItem]) -> list[NewsItem]:
    """Headlines that match most of the topic: one shared word ("fraud") is not enough."""
    keys = topic_keys(topic)
    scores = {id(n): relevance(topic, n.title) for n in items}
    best = max(scores.values(), default=0)
    need = max(1, min(len(keys), round(best * 0.67)))
    return [n for n in items if scores[id(n)] >= need]


def candidates(topic: str, items: list[NewsItem], per_domain: int = 1) -> list[NewsItem]:
    scored = sorted(relevant(topic, items), key=lambda n: (n.rank(), -relevance(topic, n.title)))
    out, seen = [], {}
    for n in scored:
        if n.rank() >= 3 or seen.get(n.domain, 0) >= per_domain:
            continue
        seen[n.domain] = seen.get(n.domain, 0) + 1
        out.append(n)
    return out


def model_available(host: str, model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as resp:
            names = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
        return any(n == model or n.split(":")[0] == model.split(":")[0] for n in names)
    except Exception:
        return False


def prepare(topic: str, region: str = "US", languages: tuple[str, ...] = ("english", "urdu"), out_root: Path | None = None,
            model: str = "qwen2.5:7b", host: str = "http://127.0.0.1:11434", max_articles: int = 2, log=print, auto: bool = False,
            script_dir: Path | None = None) -> Path:
    """`auto`: one-click mode, no human review: sentences with unverified numbers are dropped.
    `script_dir`: a folder that already holds your own english.txt / urdu.txt / roman_urdu.txt:
    no drafting; evidence is collected for `topic` and the visual tags are placed automatically."""
    from .capture import Browser, snippet_from

    out_root = out_root or REPO_ROOT / "input" / "topics"
    folder = script_dir or out_root / f"{dt.date.today():%Y-%m-%d}-{slugify(topic)}"
    own = {lang: (folder / f"{lang}.txt").read_text(encoding="utf-8") for lang in ("english", "urdu", "roman_urdu")
           if script_dir and (folder / f"{lang}.txt").is_file()}
    if script_dir and not own:
        raise SystemExit(f"{folder} has no english.txt, urdu.txt or roman_urdu.txt")
    ev = folder / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    notes: list[str] = []

    log(f"searching news for {topic!r} ({region})")
    items = search_news(topic, region)
    if not items:
        raise SystemExit("No news results for this topic; try different words.")
    cands = candidates(topic, items)
    log(f"{len(items)} results, {len(cands)} screenshot candidates")

    articles: list[dict] = []
    with Browser() as b:
        for n in cands[:12]:
            if len(articles) >= max_articles:
                break
            k = len(articles) + 1
            cap = b.article(n.url, ev / f"article_{k}.png")
            log(f"  {'captured' if cap else 'skipped '} {n.domain}: {n.title[:70]}")
            if cap:
                articles.append({"id": str(k), "image": f"article_{k}.png", "url": cap.final_url, "title": n.title, "publisher": n.publisher,
                                 "words": cap.words, "paragraphs": cap.paragraphs, "news_url": n.url})
        on_topic = relevant(topic, items) or items
        recent = sorted(on_topic, key=lambda n: -(n.published or now - dt.timedelta(days=999)).timestamp())[:4]
        snippets = {a["news_url"]: snippet_from(a["paragraphs"]) for a in articles}
        card_items = [{"title": n.title, "publisher": n.publisher, "url": n.url, "age": n.age_text(now), "snippet": snippets.get(n.url, ""),
                       "published": n.published.isoformat() if n.published else None} for n in recent]
        cards = b.news_cards(card_items, ev / "news_cards.png")
    if not articles:
        notes.append("No article could be captured (blocked or paywalled). Add [news] and [chart] tags only, or try another topic wording.")

    text_pool = " ".join([topic, own.get("english", "")] + [n.title for n in items[:20]] + [" ".join(a["paragraphs"][:20]) for a in articles])
    charts = [c for c in (fred.download(sid) for sid in fred.pick_series(text_pool)) if c]
    log(f"charts: {[c['id'] for c in charts] or 'none'}")

    if own:
        use_model = model != "none" and model_available(host, model)
        return tag_own_scripts(folder, ev, topic, region, own, card_items, cards, articles, charts, now, log,
                               model if use_model else None, host)

    # -- drafts ----------------------------------------------------------------
    brief = draft.evidence_brief(topic, card_items, articles, charts, {c["id"]: fred.summary(c) for c in charts})
    use_model = model_available(host, model)
    if use_model:
        log(f"drafting the English script with {model}")
        english = draft.clean_output(draft.ollama(draft.english_prompt(brief), model, host))
    else:
        log(f"model {model} not reachable at {host}: writing an extractive draft instead")
        notes.append(f"The AI model ({model}) was not available, so the draft is built from article sentences. Rewrite it in your words.")
        english = draft.extractive_draft(card_items, articles, charts)

    logo_rows = []
    wanted = []
    for c in extract_cues(english)[1]:
        if c.kind == "logo" and c.ref and c.ref.lower() not in {w.lower() for w in wanted}:
            wanted.append(c.ref)
    for name in wanted[:3]:
        hit = logos.find_logo(name, ev)
        log(f"  logo {name}: {'found' if hit else 'not found'}")
        if hit:
            logo_rows.append(hit)

    manifest = {"news": {"image": "news_cards.png", "cards": cards},
                "articles": [{k: v for k, v in a.items() if k not in ("paragraphs", "news_url")} for a in articles],
                "charts": charts, "logos": logo_rows}
    (ev / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for a in articles:
        (ev / f"article_{a['id']}.txt").write_text("\n\n".join(a["paragraphs"]) + "\n", encoding="utf-8")

    evidence = Evidence.load(ev)
    known = draft.evidence_numbers([brief] + [" ".join(a["paragraphs"]) for a in articles]) | {round(v, 2) for c in charts for _, v in c["points"]}
    known |= {float(round(v)) for v in list(known)}  # "around 3%" for 2.65%
    logo_names = {lg["name"].lower() for lg in logo_rows}
    english, en_notes = draft.validate(english, evidence.articles, set(evidence.charts), logo_names, known, "en")
    notes += [f"english: {n}" for n in en_notes]
    scripts = {"english": english}
    for lang, prompt, code, label in (("urdu", draft.urdu_prompt, "ur", "Urdu-script"), ("roman_urdu", draft.roman_urdu_prompt, "roman_urdu", "Roman Urdu")):
        if lang not in languages:
            continue
        if use_model:
            log(f"drafting the {label} script")
            text = draft.clean_output(draft.ollama(prompt(english), model, host, temperature=0.3))
            text, lang_notes = draft.validate(text, evidence.articles, set(evidence.charts), logo_names, known, code)
            notes += [f"{lang}: {n}" for n in lang_notes]
        else:
            text = f"{{VERIFY: write the {label} script (keep the [tags] from english.txt)}}\n"
        scripts[lang] = text
    if auto:
        for lang in list(scripts):
            scripts[lang], removed = draft.drop_unverified(scripts[lang])
            if removed:
                notes.append(f"{lang}: one-click mode removed {removed} sentence(s) whose numbers were not in the evidence")
            if len(re.sub(r"\[[^\]]*\]", " ", scripts[lang]).split()) < 25 and lang in languages:
                raise SystemExit(f"The {lang} draft is too short after removing unverified sentences; try another topic wording or review it by hand.")
    codes = {"english": "en", "urdu": "ur", "roman_urdu": "roman_urdu"}
    for lang, text in scripts.items():
        if lang in languages:
            if "{VERIFY" not in text:
                text, draw_notes = add_drawings(text, codes[lang], model if use_model else None, host, log)
                notes += [f"{lang}: {n}" for n in draw_notes]
            (folder / f"{lang}.txt").write_text(text, encoding="utf-8")
    (folder / "audio").mkdir(exist_ok=True)
    (folder / "audio" / "README.txt").write_text(
        "Optional: put your own voice-over here as english.mp3 / urdu.mp3 / roman_urdu.mp3 (any audio format).\n"
        "Without it, the narration is generated in your cloned voice from input/voice/reference.*\n", encoding="utf-8")

    meta = {"topic": topic, "region": region, "created": now.isoformat(timespec="seconds"), "model": model if use_model else "none (extractive)",
            "languages": list(languages), "sources": evidence.sources(),
            "notice": "Screenshots and logos belong to their owners; they are shown as evidence for commentary. Check each source before publishing."}
    (folder / "topic.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (folder / "notes.md").write_text(review_notes(topic, folder, brief, articles, charts, logo_rows, notes, scripts), encoding="utf-8")
    log(f"topic folder ready: {folder}")
    return folder


def add_drawings(text: str, code: str, model: str | None, host: str, log) -> tuple[str, list[str]]:
    """[icon ...] tags for the things each sentence names (see drawings.py); unchanged if unavailable."""
    from .drawings import tag_drawings
    from .layout import emoji_library
    from .semantic import get_embedder

    lib = emoji_library(log)
    emb = get_embedder(log=log) if lib else None
    if lib is None or emb is None:
        log("drawings: not available, the hand-made doodles are used")
        return text, []
    return tag_drawings(text, code, lib, emb, model=model, host=host, log=log)


def tag_own_scripts(folder, ev, topic, region, own, card_items, cards, articles, charts, now, log,
                    model: str | None = None, host: str = "http://127.0.0.1:11434") -> Path:
    """Your own scripts: keep every word, add [news] / [article] / [chart] tags where the evidence backs a
    sentence (by numbers, then by meaning), and [icon] tags for the things each sentence names."""
    from .semantic import get_embedder

    manifest = {"news": {"image": "news_cards.png", "cards": cards},
                "articles": [{k: v for k, v in a.items() if k not in ("paragraphs", "news_url")} for a in articles],
                "charts": charts, "logos": []}
    (ev / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for a in articles:
        (ev / f"article_{a['id']}.txt").write_text("\n\n".join(a["paragraphs"]) + "\n", encoding="utf-8")
    evidence = Evidence.load(ev)
    paragraphs = {a["id"]: a["paragraphs"] for a in articles}
    notes = []
    codes = {"english": "en", "urdu": "ur", "roman_urdu": "roman_urdu"}
    emb = get_embedder(log=log)
    for lang, text in own.items():
        tagged, tag_notes = draft.auto_tag(text, codes[lang], evidence.articles, paragraphs, charts, bool(cards),
                                           emb=emb, model=model, host=host)
        tagged, draw_notes = add_drawings(tagged, codes[lang], model, host, log)
        tag_notes += draw_notes
        (folder / f"{lang}.txt").write_text(tagged, encoding="utf-8")
        notes += [f"{lang}: {n}" for n in tag_notes]
        log(f"{lang}: {len(tag_notes)} visual tag(s) placed")
        for n in tag_notes:
            log(f"    {n}")
    (folder / "audio").mkdir(exist_ok=True)
    meta = {"topic": topic, "region": region, "created": now.isoformat(timespec="seconds"), "model": "none (your own script)",
            "languages": list(own), "sources": evidence.sources(),
            "notice": "Screenshots and logos belong to their owners; they are shown as evidence for commentary. Check each source before publishing."}
    (folder / "topic.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (folder / "notes.md").write_text(review_notes(topic, folder, "", articles, charts, [], notes, own), encoding="utf-8")
    log(f"topic folder ready: {folder}")
    return folder


def review_notes(topic, folder, brief, articles, charts, logo_rows, notes, scripts) -> str:
    lines = [f"# {topic}", "", "## Before you render", "",
             "1. Read `english.txt` (and `urdu.txt` / `roman_urdu.txt`). Rewrite anything that sounds off; keep it 45–60 seconds.",
             "2. Replace every `{VERIFY: ...}` with a checked fact (rendering refuses to start while one is left).",
             "3. Check each screenshot in `evidence/` and the quotes in `[article N \"...\"]` tags (copy them exactly from the article text below).",
             "4. Optional: add your voice-over as `audio/english.mp3` / `audio/urdu.mp3`.",
             f"5. Run **Generate Whiteboard Video** with `topic_dir` = `{folder.relative_to(REPO_ROOT) if folder.is_relative_to(REPO_ROOT) else folder}`.", "",
             "## Tags you can use", "",
             "- `[news]` headline cards", "- `[article N \"exact words\"]` article screenshot with those words highlighted",
             "- `[chart ID 2021]` data chart with a marker on a date", "- `[logo \"Name\"]` organisation logo",
             "- `[icon oil-drum]` a drawing on the next word: any Fluent Emoji name (oil-drum, fuel-pump, mobile-phone, pill, "
             "house, …, see icon-sets.iconify.design/fluent-emoji-flat) or a doodle (house, bank, document, money_bag, rate_badge, lock, …)", ""]
    if notes:
        lines += ["## Check these", ""] + [f"- {n}" for n in notes] + [""]
    unresolved = sum(len(has_unresolved(s)) for s in scripts.values())
    lines += [f"Placeholders to fill: **{unresolved}**", "", "## Evidence", ""]
    for a in articles:
        lines += [f"### Article {a['id']}: {a['title']}", f"{a['publisher']} — {a['url']}", f"![article {a['id']}](evidence/{a['image']})", "",
                  "Sentences you can quote:", ""] + [f"- {s}" for s in draft.key_sentences(a["paragraphs"])] + [""]
    for c in charts:
        lines += [f"### Chart {c['id']}: {c['title']}", f"{c['source']} — {c['url']}", f"Values: {fred.summary(c)}", ""]
    for lg in logo_rows:
        lines += [f"### Logo: {lg['name']}", lg["url"], ""]
    lines += ["### Headline cards", "![news cards](evidence/news_cards.png)", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topic", required=True)
    p.add_argument("--region", default="US")
    p.add_argument("--languages", default="english,urdu")
    p.add_argument("--model", default=os.environ.get("DRAFT_MODEL", "qwen2.5:7b"))
    p.add_argument("--host", default="http://127.0.0.1:11434")
    p.add_argument("--max-articles", type=int, default=2)
    p.add_argument("--out", default="")
    p.add_argument("--script-dir", default="", help="folder with your own english.txt / urdu.txt: tag them instead of drafting")
    p.add_argument("--auto", action="store_true", help="one-click: drop unverified sentences instead of leaving {VERIFY} for review")
    a = p.parse_args(argv)
    langs = tuple(x.strip() for x in a.languages.split(",") if x.strip())
    bad = [x for x in langs if x not in ("english", "urdu", "roman_urdu")]
    if bad:
        print(f"::error::explainer topics support english, urdu and roman_urdu, not {', '.join(bad)}")
        return 2
    folder = prepare(a.topic.strip(), a.region.strip().upper() or "US", langs, Path(a.out).resolve() if a.out else None, a.model, a.host, a.max_articles,
                     log=lambda m: print(m, flush=True), auto=a.auto, script_dir=(REPO_ROOT / a.script_dir).resolve() if a.script_dir else None)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            rel = folder.relative_to(REPO_ROOT) if folder.is_relative_to(REPO_ROOT) else folder
            fh.write(f"topic_dir={rel}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write((folder / "notes.md").read_text(encoding="utf-8").split("## Evidence")[0])
            for lang in langs:
                if (folder / f"{lang}.txt").exists():
                    fh.write(f"\n### {lang}.txt\n\n```\n{(folder / f'{lang}.txt').read_text(encoding='utf-8')}```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
