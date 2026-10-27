"""Visual cue tags inside scripts.

    [news]                          the headline cards for the topic
    [article 1 "decreased by 18.1%"] screenshot of article 1, highlight the quoted words
    [chart MORTGAGE30US 2021]       data chart, marker on the date (year, YYYY-MM or YYYY-MM-DD)
    [logo "Fannie Mae"]             organisation logo
    [icon oil-drum]                 a drawing on the next word (Fluent Emoji name, or a doodle from object_generator.OBJECTS)
    [hook "One crash. $80,000."]    big opening card for the first second (default: the first sentence)

Tags are instructions, not words: they are removed from the narration, captions and TTS
text and attached to the word that follows them (`word` = index into text.split()).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

CUE_KINDS = ("news", "article", "chart", "logo", "icon", "hook")
CUE_TAG = re.compile(r"\[(" + "|".join(CUE_KINDS) + r")\b([^\]\n]*)\]", re.IGNORECASE)
# Voice-direction tags ([curious], [pause]) are stripped in the same pass so word indices
# refer to the final narration text.
VOICE_TAG = re.compile(r"\[[A-Za-z][A-Za-z _'-]{0,30}\]")
QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})


@dataclass
class Cue:
    kind: str
    word: int  # index of the first word the cue belongs to
    ref: str = ""  # article number, chart series id, logo name or icon name
    quote: str = ""  # article: words to highlight
    date: str = ""  # chart: date to mark
    extra: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "word": self.word, "ref": self.ref}
        if self.quote:
            d["quote"] = self.quote
        if self.date:
            d["date"] = self.date
        return d


def _args(raw: str) -> list[str]:
    raw = raw.translate(QUOTES).strip()
    try:
        return shlex.split(raw)
    except ValueError:  # unbalanced quote: fall back to plain words
        return raw.replace('"', " ").split()


def parse_cue(kind: str, raw_args: str, word: int) -> Cue:
    kind = kind.lower()
    args = _args(raw_args)
    cue = Cue(kind, word)
    if kind == "article":
        cue.ref = args[0] if args else "1"
        cue.quote = " ".join(args[1:])
    elif kind == "chart":
        cue.ref = args[0].upper() if args else ""
        cue.date = args[1] if len(args) > 1 else ""
        cue.extra = args[2:]
    elif kind in ("logo", "icon", "hook"):
        cue.ref = " ".join(args)
    else:  # news
        cue.extra = args
    return cue


def extract_cues(text: str) -> tuple[str, list[Cue]]:
    """Remove cue and voice tags. Returns (text, cues) with cue word indices into text.split()."""
    out: list[str] = []
    cues: list[Cue] = []
    pos = 0
    for m in CUE_TAG.finditer(text):
        out.append(VOICE_TAG.sub(" ", text[pos:m.start()]))
        word = len("".join(out).split())
        cues.append(parse_cue(m.group(1), m.group(2), word))
        out.append(" ")
        pos = m.end()
    out.append(VOICE_TAG.sub(" ", text[pos:]))
    return "".join(out), cues


def has_unresolved(text: str) -> list[str]:
    """`{VERIFY: ...}` placeholders left by the draft writer (must be replaced before rendering)."""
    return re.findall(r"\{VERIFY:[^}]*\}", text)
