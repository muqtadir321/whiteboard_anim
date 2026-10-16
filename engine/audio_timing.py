"""Sentence timing for narration audio.

Sources, best first:
  * exact      - generated narration (each sentence synthesised separately)
  * words      - word timestamps from speech recognition, aligned to the script
  * estimated  - silence detection + sentence length weighting (supplied audio
                 with no usable recogniser). Never presented as word-perfect.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class TimedSentence:
    text: str
    start: float
    end: float
    words: list[dict] = field(default_factory=list)


@dataclass
class Timing:
    sentences: list[TimedSentence]
    source: str  # exact | words | estimated
    confidence: float
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "details": self.details,
            "sentences": [{"text": s.text, "start": round(s.start, 3), "end": round(s.end, 3), "word_count": len(s.words)} for s in self.sentences],
        }


def _weight(text: str) -> float:
    # Spoken length is roughly proportional to letters plus a pause per punctuation mark.
    letters = sum(1 for c in text if c.isalnum())
    pauses = sum(1 for c in text if c in ",;:،")
    return max(1.0, letters + 4 * pauses + 6)


def estimate_timing(sentences: list[str], duration: float, silences: list[tuple[float, float]], lead: float = 0.0) -> Timing:
    """Distribute sentences over the audio, snapping boundaries to detected pauses."""
    speech_start = lead
    speech_end = duration
    if silences and silences[0][0] <= 0.05:
        speech_start = max(speech_start, silences[0][1])
    if silences and silences[-1][1] >= duration - 0.05:
        speech_end = min(speech_end, silences[-1][0])
    if speech_end - speech_start < 0.5:
        speech_start, speech_end = 0.0, duration
    weights = [_weight(s) for s in sentences]
    total = sum(weights)
    bounds = [speech_start]
    acc = 0.0
    for w in weights[:-1]:
        acc += w
        bounds.append(speech_start + (speech_end - speech_start) * acc / total)
    bounds.append(speech_end)
    mids = [((s + e) / 2, e - s) for s, e in silences if speech_start < (s + e) / 2 < speech_end]
    snapped = 0
    used = set()
    for i in range(1, len(bounds) - 1):
        est = bounds[i]
        window = max(1.2, 0.4 * min(bounds[i] - bounds[i - 1], bounds[i + 1] - bounds[i]))
        best = None
        for j, (m, d) in enumerate(mids):
            if j in used or not (bounds[i - 1] + 0.2 < m < bounds[i + 1] - 0.2):
                continue
            dist = abs(m - est)
            if dist <= window and (best is None or dist - 0.3 * d < best[0]):
                best = (dist - 0.3 * d, j, m)
        if best:
            used.add(best[1])
            bounds[i] = best[2]
            snapped += 1
    timed = [TimedSentence(s, bounds[i], bounds[i + 1]) for i, s in enumerate(sentences)]
    # Scenes must tile the whole narration: first starts at 0, last ends at the audio end.
    timed[0].start = 0.0
    timed[-1].end = duration
    n_bounds = max(1, len(sentences) - 1)
    conf = 0.35 + 0.45 * (snapped / n_bounds if len(sentences) > 1 else 1.0)
    return Timing(timed, "estimated", conf, {"snapped_boundaries": snapped, "boundaries": len(sentences) - 1, "silences": len(silences)})


def exact_timing(sentences: list[str], spans: list[tuple[float, float]], duration: float) -> Timing:
    timed = []
    for i, (s, (a, b)) in enumerate(zip(sentences, spans)):
        end = spans[i + 1][0] if i + 1 < len(spans) else duration
        timed.append(TimedSentence(s, 0.0 if i == 0 else a, end))
    return Timing(timed, "exact", 0.98, {"method": "per-sentence synthesis"})


def _norm_word(w: str) -> str:
    w = unicodedata.normalize("NFC", w.lower())
    return re.sub(r"[^\w]", "", w)


def align_words(sentences: list[str], words: list[dict], duration: float) -> tuple[Timing, float]:
    """Align script sentences to recognised words. Returns (timing, similarity)."""
    script_tokens: list[tuple[int, str]] = []
    for si, s in enumerate(sentences):
        for w in s.split():
            nw = _norm_word(w)
            if nw:
                script_tokens.append((si, nw))
    rec = [_norm_word(w.get("word", "")) for w in words]
    sm = difflib.SequenceMatcher(a=[t for _, t in script_tokens], b=rec, autojunk=False)
    mapping: dict[int, int] = {}
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            mapping[block.a + k] = block.b + k
    similarity = sm.ratio()
    starts: list[float | None] = []
    sent_words: list[list[dict]] = [[] for _ in sentences]
    for si in range(len(sentences)):
        idxs = [i for i, (s, _) in enumerate(script_tokens) if s == si]
        matched = [mapping[i] for i in idxs if i in mapping]
        starts.append(words[min(matched)]["start"] if matched else None)
        for m in matched:
            sent_words[si].append(words[m])
    # Fill unmatched sentence starts by interpolation.
    known = [(i, t) for i, t in enumerate(starts) if t is not None]
    if not known:
        return estimate_timing(sentences, duration, []), similarity
    for i in range(len(starts)):
        if starts[i] is None:
            prev = max((k for k in known if k[0] < i), default=(0, 0.0), key=lambda k: k[0])
            nxt = min((k for k in known if k[0] > i), default=(len(starts), duration), key=lambda k: k[0])
            frac = (i - prev[0]) / max(1, nxt[0] - prev[0])
            starts[i] = prev[1] + (nxt[1] - prev[1]) * frac
    timed = []
    for i, s in enumerate(sentences):
        start = 0.0 if i == 0 else float(starts[i])
        end = float(starts[i + 1]) if i + 1 < len(sentences) else duration
        timed.append(TimedSentence(s, start, max(start + 0.2, end), sent_words[i] if len(sent_words[i]) == len(s.split()) else []))
    return Timing(timed, "words", min(0.95, 0.4 + 0.55 * similarity), {"word_similarity": round(similarity, 3)}), similarity
