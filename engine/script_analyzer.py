"""Script normalisation and sentence analysis.

The canonical script is never rewritten: normalisation only fixes whitespace,
line endings, and Unicode normal form (NFC), so meaning, punctuation, numbers,
currency, dates and units are preserved.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .numbers import Quantity, extract_quantities

SENTENCE_END = ".!?۔؟"
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd", "co", "no", "rs", "approx"}


STAGE_TAG = re.compile(r"\[[A-Za-z][A-Za-z _'-]{0,30}\]")


class ScriptError(ValueError):
    pass


@dataclass
class Sentence:
    index: int
    text: str
    start_char: int
    paragraph: int
    quantities: list[Quantity] = field(default_factory=list)
    emphasis: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "start_char": self.start_char,
            "paragraph": self.paragraph,
            "emphasis": self.emphasis,
            "quantities": [q.to_dict() for q in self.quantities],
        }


def normalize_script(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ScriptError(f"Script is not valid UTF-8 text: {exc}") from exc
    if "\x00" in raw:
        raise ScriptError("Script contains NUL bytes; it looks like a binary or corrupted file")
    text = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))
    # Voice-direction tags such as [curious], [pause], [soft] (ElevenLabs style) are
    # instructions for the narrator, not words: they never reach captions or planning.
    text = STAGE_TAG.sub(" ", text)
    # Drop zero-width BOMs and control characters but keep ZWNJ/ZWJ (used in Urdu/Persian).
    text = "".join(c for c in text if c in "\n\t‌‍" or unicodedata.category(c)[0] != "C")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.split("\n")]
    # Collapse 3+ blank lines into one paragraph break.
    out: list[str] = []
    blank = 0
    for line in lines:
        if line:
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    text = "\n".join(out).strip()
    if not text:
        raise ScriptError("Script is empty")
    letters = sum(1 for c in text if c.isalpha())
    if letters < 3:
        raise ScriptError("Script does not contain readable words")
    printable_ratio = sum(1 for c in text if c.isprintable() or c == "\n") / len(text)
    if printable_ratio < 0.95:
        raise ScriptError("Script contains too many non-printable characters (corrupted?)")
    return text


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Return (sentence, start_char, paragraph_index)."""
    results = []
    paragraph = 0
    pos = 0
    for para in text.split("\n"):
        if not para.strip():
            paragraph += 1
            pos += len(para) + 1
            continue
        start = 0
        i = 0
        n = len(para)
        while i < n:
            ch = para[i]
            if ch in SENTENCE_END:
                # Decimal numbers: 3.5
                if ch == "." and i > 0 and i + 1 < n and para[i - 1].isdigit() and para[i + 1].isdigit():
                    i += 1
                    continue
                # Abbreviations: Mr. Dr. e.g.
                if ch == ".":
                    m = re.search(r"([A-Za-z.]+)$", para[start:i])
                    if m and m.group(1).lower().strip(".") in ABBREVIATIONS:
                        i += 1
                        continue
                j = i + 1
                while j < n and (para[j] in SENTENCE_END or para[j] in "\"'”’)»"):
                    j += 1
                chunk = para[start:j].strip()
                if chunk:
                    results.append((chunk, pos + start + (len(para[start:j]) - len(para[start:j].lstrip())), paragraph))
                start = j
                i = j
                continue
            i += 1
        tail = para[start:].strip()
        if tail:
            results.append((tail, pos + start + (len(para[start:]) - len(para[start:].lstrip())), paragraph))
        pos += len(para) + 1
        paragraph += 1
    return results


def analyze_script(text: str, lang: str) -> list[Sentence]:
    sentences = []
    for idx, (chunk, start, para) in enumerate(split_sentences(text)):
        emphasis = chunk.endswith("!") or bool(re.search(r"\b[A-Z]{3,}\b", chunk))
        sentences.append(Sentence(idx, chunk, start, para, extract_quantities(chunk, lang), emphasis))
    if not sentences:
        raise ScriptError("No sentences found in script")
    return sentences


def split_long_sentence(text: str, max_chars: int) -> list[str]:
    """Split an overly long sentence into phrases at commas/conjunctions (keeps all words)."""
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r"(?<=[,،;:])\s+|\s+(?=(?:and|but|so|because|which|while|aur|lekin|magar|kyunke|isliye|اور|لیکن|کیونکہ|او|خو|ځکه)\s)", text)
    parts = [p for p in parts if p and p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) + len(p) + 1 <= max_chars:
            merged[-1] = merged[-1] + " " + p.strip()
        else:
            merged.append(p.strip())
    # Hard-split anything still too long at word boundaries.
    out: list[str] = []
    for m in merged:
        while len(m) > max_chars * 1.6:
            cut = m.rfind(" ", 0, max_chars)
            if cut <= 0:
                break
            out.append(m[:cut])
            m = m[cut + 1:]
        out.append(m)
    return out
