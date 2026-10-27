"""Script checker for Shorts retention: advice only, scripts are never changed.

    python -m engine.cli lint-script input/scripts/english.txt

Checks what the first two seconds and the last line do, because that is where Shorts are
won or lost: a hook short enough to say in two seconds, with a number or stakes, spoken to
"you"; one concrete example amount; 20 to 60 seconds of speech; an ending that calls back to
the hook so a replay feels natural. Finance is educational content: the checker also flags
promises of returns that YouTube and viewers treat as advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .explainer.cues import extract_cues
from .numbers import extract_quantities

WORDS_PER_SECOND = 2.6
STAKES = set("""lose lost losing cost costs costing mistake mistakes never stop wrong broke debt gone risk risky secret nobody warning
trap traps hidden fee fees penalty fine fines owe owed crash scam scams waste wasted pay paying denied rejected""".split())
GENERIC_OPENERS = ("in this video", "today we", "today i", "let's talk", "lets talk", "hello", "hi guys", "hey guys", "welcome",
                   "have you ever wondered", "did you know")
PROMISES = re.compile(r"\b(guaranteed?|risk[- ]free|get rich|double your money|can't lose|cannot lose|100% safe)\b", re.I)
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been it its this that these those your you
i my we our they their he she not no so than then too very can could will would should may might just about into over
what which who how why when where do does did has have had more most less much many""".split())


@dataclass
class Note:
    level: str  # "fix" (likely to cost viewers) | "tip"
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.message}"


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟۔])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def content_words(sentence: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", sentence.lower()) if w not in STOP and len(w) > 2}


def lint(text: str, lang: str = "en") -> list[Note]:
    clean, cues = extract_cues(text)
    clean = re.sub(r"\s+", " ", clean).strip()
    notes: list[Note] = []
    if not clean:
        return [Note("fix", "the script is empty")]
    if "{VERIFY" in clean:
        notes.append(Note("fix", "a {VERIFY: ...} placeholder is left: rendering refuses to start until it is replaced"))
    sents = sentences(clean)
    words = clean.split()
    first = sents[0]
    hook = next((c.ref for c in cues if c.kind == "hook" and c.ref), "")
    n_first = len(first.split())
    if n_first > 12 and not hook:
        notes.append(Note("fix", f"the first sentence is {n_first} words (~{n_first / WORDS_PER_SECOND:.1f} s): viewers decide in about "
                                 "2 seconds. Open with 12 words or fewer, or add a [hook \"...\"] tag for the opening card"))
    opener = first.lower()
    if any(opener.startswith(g) for g in GENERIC_OPENERS):
        notes.append(Note("fix", f"the script opens with a generic phrase ({first.split()[0]} ...): start with the situation or the number"))
    has_amount = bool([q for q in extract_quantities(first, lang) if q.kind != "year"])
    has_stakes = bool(set(re.findall(r"[a-z]+", opener)) & STAKES)
    if lang == "en" and not has_amount and not has_stakes:
        notes.append(Note("fix", "the hook has no number and no stakes. Breakout finance Shorts open with a scenario and an amount, "
                                 "e.g. \"One crash. $80,000 in bills. Your policy pays $25,000.\""))
    if first.rstrip().endswith("?") and not has_amount:
        notes.append(Note("tip", "an opening question without a number is easy to swipe past; a surprising statement usually holds better"))
    if lang == "en" and not re.search(r"\byou(r|'re|'ll)?\b", " ".join(sents[:2]).lower()):
        notes.append(Note("tip", "speak to the viewer in the first two sentences (\"you\", \"your\")"))
    if lang == "en" and not [q for q in extract_quantities(clean, lang) if q.kind in ("currency", "percent")]:
        notes.append(Note("fix", "no concrete example amount anywhere: add one ($ or %), it makes the idea real and gives the board a number"))
    seconds = len(words) / WORDS_PER_SECOND
    if seconds > 60:
        notes.append(Note("fix", f"about {seconds:.0f} s of speech: Shorts that finish under 60 s are watched to the end more often; cut to 35-55 s"))
    elif seconds < 20:
        notes.append(Note("tip", f"about {seconds:.0f} s of speech: very short videos often feel thin; 35-55 s suits an explanation"))
    if len(sents) > 2 and lang == "en" and not content_words(sents[-1]) & content_words(first + " " + hook):
        notes.append(Note("tip", "the last line does not call back to the hook: ending on the opening idea makes a replay feel natural"))
    if PROMISES.search(clean):
        notes.append(Note("fix", f"\"{PROMISES.search(clean).group(0)}\" reads as a financial promise: keep it educational"))
    long = [s for s in sents if len(s.split()) > 22]
    if long:
        notes.append(Note("tip", f"{len(long)} sentence(s) over 22 words, e.g. \"{long[0][:60]}...\": split them, each sentence is one board"))
    return notes
