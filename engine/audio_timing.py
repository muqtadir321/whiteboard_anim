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


def _word_weight(word: str) -> float:
    return max(1.0, sum(1 for c in word if c.isalnum())) + (2.0 if word[-1:] in ",;:،" else 0.0) + 1.0


def distribute_words(text: str, start: float, end: float, silences: list[tuple[float, float]]) -> list[dict]:
    """Estimated per-word times for one sentence: words are spread over the *speech*
    inside [start, end] (detected pauses are skipped), proportional to word length."""
    tokens = text.split()
    if not tokens:
        return []
    # speech regions = [start, end] minus pauses
    regions = []
    cur = start
    for a, b in silences:
        if b <= start or a >= end:
            continue
        a, b = max(a, start), min(b, end)
        if a > cur:
            regions.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        regions.append((cur, end))
    regions = [r for r in regions if r[1] - r[0] > 0.02] or [(start, end)]
    total_speech = sum(b - a for a, b in regions)
    weights = [_word_weight(w) for w in tokens]
    wsum = sum(weights)

    def at(speech_t: float) -> float:
        for a, b in regions:
            if speech_t <= b - a:
                return a + speech_t
            speech_t -= b - a
        return regions[-1][1]

    out = []
    acc = 0.0
    for w, wt in zip(tokens, weights):
        s_t = at(total_speech * acc / wsum)
        acc += wt
        e_t = at(total_speech * acc / wsum)
        out.append({"word": w, "start": round(s_t, 3), "end": round(max(s_t + 0.02, e_t), 3), "estimated": True})
    return out


def estimate_timing(sentences: list[str], duration: float, silences: list[tuple[float, float]], lead: float = 0.0) -> Timing:
    """Place sentence boundaries on detected pauses with dynamic programming.

    Every sentence boundary must sit in a pause. Among all ways of choosing one pause per
    boundary, pick the one whose sentence durations best match the expected durations
    (from sentence length), preferring longer pauses at boundaries and penalising long
    pauses swallowed inside a sentence. This keeps errors from accumulating over long
    narrations, unlike proportional placement.
    """
    speech_start = lead
    speech_end = duration
    if silences and silences[0][0] <= 0.05:
        speech_start = max(speech_start, silences[0][1])
    if silences and silences[-1][1] >= duration - 0.05:
        speech_end = min(speech_end, silences[-1][0])
    if speech_end - speech_start < 0.5:
        speech_start, speech_end = 0.0, duration
    n = len(sentences)
    inner = [(a, b) for a, b in silences if a > speech_start + 0.05 and b < speech_end - 0.05]
    weights = [_weight(s) for s in sentences]
    wsum = sum(weights)
    span = speech_end - speech_start
    expected = [span * w / wsum for w in weights]
    bounds: list[float]
    method = "dp_pauses"
    m = len(inner)
    if n == 1:
        bounds = [speech_start, speech_end]
    elif m < n - 1:
        method = "proportional (too few pauses)"
        bounds = [speech_start]
        acc = 0.0
        for w in weights[:-1]:
            acc += w
            bounds.append(speech_start + span * acc / wsum)
        bounds.append(speech_end)
    else:
        # A sentence starts where speech resumes after its pause (just before the pause ends).
        mids = [max(a, b - 0.06) for a, b in inner]
        durs = [b - a for a, b in inner]
        # long-pause prefix sums for the "swallowed pause" penalty
        longp = [0.0]
        for d in durs:
            longp.append(longp[-1] + max(0.0, d - 0.45))
        # node 0 = speech_start, nodes 1..m = pauses, node m+1 = speech_end
        times = [speech_start] + mids + [speech_end]
        bonus = [0.0] + [min(d, 1.2) for d in durs] + [0.0]

        def seg_cost(k: int, p: int, j: int) -> float:
            dur = times[j] - times[p]
            e = expected[k]
            rel = (dur - e) / max(e, 0.8)
            swallowed = longp[j - 1] - longp[p] if j - 1 > p else 0.0
            return rel * rel + 1.5 * swallowed - 0.6 * bonus[j]

        inf = float("inf")
        # f[k][j]: best cost with sentence k ending at node j
        f = [[inf] * (m + 2) for _ in range(n)]
        back = [[-1] * (m + 2) for _ in range(n)]
        for j in range(1, m + 1):
            f[0][j] = seg_cost(0, 0, j)
        for k in range(1, n - 1):
            for j in range(k + 1, m + 1):
                best, arg = inf, -1
                for p in range(k, j):
                    if f[k - 1][p] == inf:
                        continue
                    dur = times[j] - times[p]
                    if dur > 4 * expected[k] + 3:
                        continue
                    c = f[k - 1][p] + seg_cost(k, p, j)
                    if c < best:
                        best, arg = c, p
                f[k][j], back[k][j] = best, arg
        # last sentence ends at speech_end (node m+1)
        best, arg = inf, -1
        for p in range(n - 1, m + 1):
            prev = f[n - 2][p] if n >= 2 else (0.0 if p == 0 else inf)
            if prev == inf:
                continue
            c = prev + seg_cost(n - 1, p, m + 1)
            if c < best:
                best, arg = c, p
        if arg < 0:
            method = "proportional (dp failed)"
            bounds = [speech_start + span * sum(weights[:i]) / wsum for i in range(n)] + [speech_end]
        else:
            nodes = [m + 1, arg]
            for k in range(n - 2, 0, -1):
                nodes.append(back[k][nodes[-1]])
            nodes.append(0)
            nodes.reverse()
            bounds = [times[x] for x in nodes]
    timed = []
    for i, s in enumerate(sentences):
        a, b = bounds[i], bounds[i + 1]
        timed.append(TimedSentence(s, a, b, distribute_words(s, a, b, silences)))
    # Scenes must tile the whole narration: first starts at 0, last ends at the audio end.
    timed[0].start = 0.0
    timed[-1].end = duration
    conf = 0.6 if method == "dp_pauses" else 0.35
    return Timing(timed, "estimated", conf, {"method": method, "pauses": m, "boundaries": n - 1})


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
        timed.append(TimedSentence(s, start, max(start + 0.2, end)))
    # Per-token word times: recognised words where matched, interpolation in between.
    flat = [(si, tok) for si, s in enumerate(sentences) for tok in s.split()]
    norm_index = {}
    k = 0
    for fi, (si, tok) in enumerate(flat):
        if _norm_word(tok):
            norm_index[fi] = k
            k += 1
    known_t: dict[int, tuple[float, float]] = {}
    for fi, ti in norm_index.items():
        if ti in mapping:
            w = words[mapping[ti]]
            known_t[fi] = (float(w["start"]), float(w["end"]))
    per_sentence: list[list[dict]] = [[] for _ in sentences]
    for fi, (si, tok) in enumerate(flat):
        if fi in known_t:
            a, b = known_t[fi]
        else:
            prev = max((x for x in known_t if x < fi), default=None)
            nxt = min((x for x in known_t if x > fi), default=None)
            lo = known_t[prev][1] if prev is not None else timed[si].start
            hi = known_t[nxt][0] if nxt is not None else timed[si].end
            gap = (nxt - prev) if (prev is not None and nxt is not None) else 1
            off = (fi - prev) if prev is not None else 1
            a = lo + (hi - lo) * (off - 1) / max(1, gap - 1 if prev is not None and nxt is not None else 1)
            b = max(a + 0.05, min(hi, a + 0.3))
        per_sentence[si].append({"word": tok, "start": round(a, 3), "end": round(b, 3), "estimated": fi not in known_t})
    for ts, ws in zip(timed, per_sentence):
        ts.words = ws
    return Timing(timed, "words", min(0.95, 0.4 + 0.55 * similarity), {"word_similarity": round(similarity, 3)}), similarity
