"""Publishing kit for a finished video: titles, description, tags, pinned comment, thumbnail, upload.json.

    python -m engine.cli publish-kit --job output/english [--topic "car liability insurance"]

Written next to the video in <job>/publish/ (also at the end of every run). Everything comes from
the script, the scene plan and the evidence sources, so nothing is claimed that the video does not
say. Titles are options to pick from, not final copy: the strongest is usually the hook.
upload.json holds YouTube Data API fields for a later uploader (category 27 = Education).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

from .explainer.cues import extract_cues

MAX_TITLE = 60
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been it its this that these those your you
i my me we our they their he she not no so than then too very can could will would should may might just about into over
what which who how why when where do does did has have had more most less much many here there it's that's don't isn't
imagine think say let's lets get got still even every each one two three four five six seven eight nine ten""".split())
GENERIC = set("money things thing time today people way years year lot something everything anything nothing".split())
QUESTION = set("how much many what is are why do does should can really actually you your i need".split())
RULE_WORDS = re.compile(r"\b(means|is called|rule|should|don't|never|always|remember|the goal|that's why|so before|before you)\b", re.I)


def clean_script(text: str) -> str:
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", " ", extract_cues(text)[0]).strip()


def sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def title_case(phrase: str) -> str:
    small = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "vs"}
    words = phrase.split()
    return " ".join(w if w[:1].isupper() or any(c.isdigit() for c in w) else (w if i and w in small else w[:1].upper() + w[1:])
                    for i, w in enumerate(words))


def short_topic(topic: str, script: str) -> str:
    """'how much car liability insurance do you need' -> 'car liability insurance'; without a topic, the most
    repeated two-word phrase (else word) of the script."""
    if topic:
        words = [w for w in re.findall(r"[A-Za-z0-9$%']+", topic) if w.lower() not in QUESTION]
        if words:
            return " ".join(words)
    toks = [w.lower() for w in re.findall(r"[A-Za-z']+", script)]
    bigrams = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:])
                      if a not in STOP and b not in STOP and a not in GENERIC and b not in GENERIC)
    if bigrams and bigrams.most_common(1)[0][1] >= 2:
        return bigrams.most_common(1)[0][0]
    single = Counter(t for t in toks if t not in STOP and t not in GENERIC and len(t) > 3)
    return single.most_common(1)[0][0] if single else "money"


def fit(title: str, limit: int = MAX_TITLE) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) <= limit:
        return title
    cut = title[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-—")
    return cut + "…"


def titles(script: str, topic: str, hook: str = "") -> list[str]:
    sents = sentences(script)
    first = sents[0] if sents else ""
    t = title_case(short_topic(topic, script))
    out = []
    if hook and not hook.endswith("…"):
        out.append(hook)
    if first and len(first) <= MAX_TITLE:
        out.append(first)
    if topic:  # the topic as people search it; a question keeps its question mark
        q = topic.split()[0].lower() in QUESTION or topic.rstrip().endswith("?")
        out.append(title_case(topic.rstrip("? ")) + ("?" if q else ""))
    out += [f"{t} Explained in 60 Seconds", f"Don't Make This {t} Mistake", f"{t}: What Nobody Tells You"]
    seen, result = set(), []
    for x in out:
        x = fit(x)
        if x.lower() not in seen:
            seen.add(x.lower())
            result.append(x)
    return result[:5]


def takeaways(script: str, n: int = 3) -> list[str]:
    """Sentences that state a rule or a figure, in the order they are said."""
    sents = sentences(script)[1:]
    picked = [s for s in sents if RULE_WORDS.search(s) or re.search(r"[$%]|\d", s)][:n]
    if len(picked) < n:  # fill with substantive sentences spread over the script
        rest = [s for s in sents if s not in picked and len(s.split()) >= 6]
        step = max(1, len(rest) // (n - len(picked) + 1))
        picked += rest[step - 1::step][: n - len(picked)]
        picked.sort(key=sents.index)
    out = []
    for s in picked[:n]:
        out.append(s if len(s) <= 110 else s[:109].rsplit(" ", 1)[0] + "…")
    return out


def read_sources(job: Path) -> list[str]:
    path = job / "sources.txt"
    if not path.exists():
        return []
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"-\s*(.+?)\s+(https?://\S+)\s*$", line)
        if m and m.group(2) not in seen:
            seen.add(m.group(2))
            out.append(f"{m.group(1)}: {m.group(2)}")
    return out


def hashtags(topic_short: str) -> list[str]:
    main = "#" + re.sub(r"[^A-Za-z0-9]", "", title_case(topic_short))
    return list(dict.fromkeys([main, "#PersonalFinance", "#MoneyTips", "#Shorts"]))


def description(script: str, topic_short: str, sources: list[str], chapters: list[str] | None = None) -> str:
    sents = sentences(script)
    lines = [sents[0] if sents else title_case(topic_short), ""]
    tk = takeaways(script)
    if tk:
        lines += ["In this video:"] + [f"• {x}" for x in tk] + [""]
    if chapters:
        lines += ["Chapters:"] + chapters + [""]
    if sources:
        lines += ["Sources:"] + [f"• {s}" for s in sources] + [""]
    lines += ["For education only, not financial advice. Rules and prices differ by place and policy: check your own situation.", "",
              " ".join(hashtags(topic_short))]
    return "\n".join(lines)


def build_kit(job: str | Path, script_text: str = "", topic: str = "", hook: str = "", video: str = "video.mp4",
              thumbnail: str | Path | None = None, chapters: list[str] | None = None, synthetic_voice: bool = False) -> Path:
    job = Path(job)
    out = job / "publish"
    out.mkdir(parents=True, exist_ok=True)
    if not script_text:
        meta = json.loads((job / "metadata.json").read_text(encoding="utf-8")) if (job / "metadata.json").exists() else {}
        sp = (meta.get("inputs") or {}).get("script_path")
        if sp and Path(sp).exists():
            script_text = Path(sp).read_text(encoding="utf-8")
        elif (job / "transcript.txt").exists():
            script_text = (job / "transcript.txt").read_text(encoding="utf-8")
    script = clean_script(script_text)
    if not hook:
        hook = next((c.ref for c in extract_cues(script_text)[1] if c.kind == "hook" and c.ref), "")
    t_short = short_topic(topic, script)
    opts = titles(script, topic, hook)
    sources = read_sources(job)
    desc = description(script, t_short, sources, chapters)
    tags = list(dict.fromkeys([t_short, f"{t_short} explained", *(topic and [topic] or []), "personal finance", "money tips",
                               "finance explained", "whiteboard animation"]))[:15]
    (out / "titles.txt").write_text("\n".join(opts) + "\n", encoding="utf-8")
    (out / "description.txt").write_text(desc + "\n", encoding="utf-8")
    (out / "tags.txt").write_text(", ".join(tags) + "\n", encoding="utf-8")
    (out / "pinned_comment.txt").write_text(
        f"What would you like explained next about {t_short}? Ask below and the best question gets its own video.\n", encoding="utf-8")
    thumb = None
    if thumbnail and Path(thumbnail).exists():
        thumb = out / "thumbnail.png"
        if Path(thumbnail).resolve() != thumb.resolve():
            shutil.copyfile(thumbnail, thumb)
    elif (out / "thumbnail.png").exists():  # made by the run (the hook card): keep it
        thumb = out / "thumbnail.png"
    elif (job / "thumbnail.png").exists():
        thumb = out / "thumbnail.png"
        shutil.copyfile(job / "thumbnail.png", thumb)
    upload = {"video_file": str((job / video).resolve()), "thumbnail_file": str(thumb.resolve()) if thumb else "",
              "snippet": {"title": opts[0] if opts else title_case(t_short), "description": desc, "tags": tags, "categoryId": "27"},
              "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": synthetic_voice},
              "title_options": opts,
              "notes": ["Upload as private first, check it on a phone, then publish.",
                        "containsSyntheticMedia (YouTube's 'altered or synthetic content' disclosure) is true when the voice was "
                        "generated or cloned, false for your own recorded voice; the drawings are clearly animated and need no label."]}
    (out / "upload.json").write_text(json.dumps(upload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
