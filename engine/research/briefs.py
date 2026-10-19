"""Video briefs for ranked ideas: SEO titles, hooks, description, tags and a script outline.

The outline follows the same style as the scripts in input/scripts ([tag] voice cues, short
spoken sentences, numbers written out) so it can be turned into a script for the video
workflow. No facts are invented: every number is a {VERIFY: ...} placeholder that points at
the evidence links collected for the idea.
"""

from __future__ import annotations

import datetime as dt
import re

from .analysis import Idea

ACRONYMS = {"us", "uk", "uae", "irs", "ira", "etf", "etfs", "gdp", "fed", "401k", "ai", "usa", "pk", "sp500"}


def title_case(phrase: str) -> str:
    return " ".join(w.upper() if w in ACRONYMS else w[:1].upper() + w[1:] for w in phrase.split())


def slug(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")[:60] or "idea"


def seo_titles(topic: str, year: int) -> dict[str, list[str]]:
    t = title_case(topic)
    return {
        "english": [
            f"{t}: The Real Numbers Nobody Shows You ({year})",
            f"Is {t} Really Worth It? I Did the Math",
            f"{t} Explained in 60 Seconds",
            f"Why Everyone Is Talking About {t} Right Now",
            f"The {t} Mistake That Quietly Costs You Thousands",
        ],
        "roman_urdu": [
            f"{t} ka asal sach | Koi nahi batata ({year})",
            f"{t}: Asal hisaab kitna banta hai?",
            f"{t} 60 second mein samjhein",
            f"Sab {t} ki baat kyun kar rahe hain?",
        ],
    }


def hooks(topic: str) -> dict[str, list[str]]:
    t = topic
    return {
        "english": [
            f"Everyone is talking about {t}. But almost nobody runs the real numbers.",
            f"What if everything you heard about {t} is only half the story?",
            f"Here is what {t} actually means for your wallet this month.",
        ],
        "roman_urdu": [
            f"Aaj kal har koi {t} ki baat kar raha hai. Lekin asal hisaab koi nahi karta.",
            f"Kya {t} ke baare mein jo suna hai, woh poora sach hai?",
        ],
    }


def outline(topic: str, language: str) -> str:
    """Script skeleton in the user's [tag] style, ~2-3 minutes when filled in."""
    t = topic
    if language == "roman_urdu":
        return "\n\n".join([
            f"[curious] {t} ka sach. {{VERIFY: ek bara number jo log sunte hain}} sun kar lagta hai bahut hai. Lekin asal mein kya bachta hai?",
            "[confident] Agle teen minute mein hum poora hisaab karenge, aur teen baatein jo har kisi ko pata honi chahiye.",
            "[pause] [measured] Pehle asal number dekhte hain.",
            "[calm] {VERIFY: main number, source ke saath, alfaaz mein likhein: maslan teen hazaar dirham}.",
            "[measured] Ab is mein se kharche nikaalte hain. {VERIFY: kharcha 1}. {VERIFY: kharcha 2}. {VERIFY: kharcha 3}.",
            "[serious] Bacha kya? {VERIFY: baqi raqam}.",
            "[pause] [concerned] Aur yahan aksar log ghalti karte hain: {VERIFY: aam ghalti}.",
            "[confident] Teen sawaal: pehla, {VERIFY}. Doosra, {VERIFY}. Teesra, {VERIFY}.",
            "[warm] Agar yeh hisaab kaam ka laga, to channel follow karein. Agli video mein is se bhi aham sawaal.",
        ])
    return "\n\n".join([
        f"[curious] {title_case(t)}. It sounds like {{VERIFY: the headline number people hear}}. But what is actually left once real life takes its cut?",
        "[confident] In the next three minutes, we will run the real numbers, and the three things that decide whether this works for you.",
        "[pause] [measured] Start with the number itself.",
        "[calm] {VERIFY: the main figure with its source, written out in words, e.g. one hundred thousand dollars}.",
        "[measured] Now take out the costs. {VERIFY: cost 1}. {VERIFY: cost 2}. {VERIFY: cost 3}.",
        "[serious] What is left? {VERIFY: the remaining amount}.",
        "[pause] [concerned] And here is the mistake most people make: {VERIFY: common mistake}.",
        "[confident] Three questions before you decide. First, {VERIFY}. Second, {VERIFY}. Third, {VERIFY}.",
        "[warm] If this helped, follow for the next breakdown.",
    ])


def description(topic: str, idea: Idea) -> str:
    t = title_case(topic)
    kw = ", ".join(idea.keywords[:6]) if idea.keywords else topic
    return (f"{t}: the real numbers, explained simply on a whiteboard.\n\n"
            f"In this video: what {topic} really means, where the money goes, and three questions to ask before you decide.\n\n"
            f"Related searches: {kw}\n\n"
            "This video is for education only and is not financial advice. Figures are examples; check them for your own situation.")


def tags_for(idea: Idea) -> tuple[list[str], list[str]]:
    base = [idea.topic, *idea.keywords[:12], *idea.seeds[:3], "personal finance", "money tips", "finance explained"]
    tags = list(dict.fromkeys(t.strip() for t in base if t.strip()))[:20]
    hashtags = ["#" + re.sub(r"[^A-Za-z0-9]", "", title_case(idea.topic)), "#PersonalFinance", "#Money", "#Shorts"]
    return tags, list(dict.fromkeys(h for h in hashtags if len(h) > 1))


def brief_markdown(rank: int, idea: Idea, year: int | None = None, draft: dict[str, str] | None = None) -> str:
    year = year or dt.date.today().year
    titles = seo_titles(idea.topic, year)
    hk = hooks(idea.topic)
    tags, hashtags = tags_for(idea)
    lines = [f"# {rank}. {title_case(idea.topic)}", "",
             f"**Viral potential:** {idea.potential} · **score** {idea.score} · **signals from:** {', '.join(idea.sources)}", "",
             "| momentum | value (0-1) |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in idea.components.items()]
    lines += ["", "## Why it is trending (evidence)", ""]
    for e in idea.evidence:
        bits = [e["source"]]
        if e.get("channel"):
            bits.append(str(e["channel"]))
        if e.get("region"):
            bits.append(e["region"])
        if e.get("views"):
            bits.append(f"{e['views']:,} views")
        if e.get("views_per_hour"):
            bits.append(f"{e['views_per_hour']:,} views/hour")
        if e.get("traffic"):
            bits.append(f"{e['traffic']:,}+ searches")
        lines.append(f"- [{e['title']}]({e['url']}) ({' · '.join(bits)})" if e.get("url") else f"- {e['title']} ({' · '.join(bits)})")
    lines += ["", "## SEO titles", "", "**English**", ""] + [f"- {x}" for x in titles["english"]]
    lines += ["", "**Roman Urdu**", ""] + [f"- {x}" for x in titles["roman_urdu"]]
    lines += ["", "## Opening hooks (first 3 seconds)", ""] + [f"- {x}" for x in hk["english"] + hk["roman_urdu"]]
    if idea.keywords:
        lines += ["", "## What people search (YouTube suggestions)", ""] + [f"- {k}" for k in idea.keywords]
    lines += ["", "## Description", "", "```", description(idea.topic, idea), "", " ".join(hashtags), "```",
              "", "## Tags", "", "```", ", ".join(tags), "```"]
    lines += ["", "## Script outline (English)", "",
              "Replace every `{VERIFY: ...}` with a checked figure from the evidence above, then save as",
              "`input/scripts/english.txt` and run **Generate Whiteboard Video**.", "", "```", outline(idea.topic, "english"), "```",
              "", "## Script outline (Roman Urdu)", "", "Save as `input/scripts/roman_urdu.txt`.", "", "```",
              outline(idea.topic, "roman_urdu"), "```"]
    for lang, text in (draft or {}).items():
        lines += ["", f"## DRAFT script ({lang}, written by a local AI model)", "",
                  "Draft only: check every number and claim against the evidence before recording.", "", "```", text.strip(), "```"]
    return "\n".join(lines) + "\n"
