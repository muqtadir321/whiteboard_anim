"""Forced alignment: exact time of every script word in a supplied voice-over.

Uses torchaudio's MMS_FA pipeline (Meta's multilingual CTC aligner, trained on
1,100+ languages). The script is romanised (uroman for Arabic-script Urdu and
Pashto; Roman Urdu and English are already Latin) and aligned to the audio, so
each word gets the moment it is actually spoken. This is how drawings are
synchronised "on the word" for English, Roman Urdu, Urdu and Pashto alike.

Licence note: the MMS_FA model weights are published by Meta under CC-BY-NC-4.0.
They are only used to compute timings; nothing from the model ends up in the
video. Disable with timing.forced_alignment: false if that matters to you.
NOTE: model download (download.pytorch.org) was not reachable in the
development container; covered by tests with a fake aligner. Any failure falls
back to Whisper alignment or pause-based timing.
"""

from __future__ import annotations

import re
import unicodedata

from .audio_timing import TimedSentence, Timing
from .results import StageError, Status
from .text_shaper import is_arabic_char

_DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


class ForcedAligner:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def align(self, audio_path: str, tokens: list[str], language: str) -> list[tuple[float, float] | None]:
        """Return (start, end) seconds per token, or None for tokens that could not be aligned."""
        raise NotImplementedError


def romanize_token(token: str, uroman=None) -> str:
    """Lower-case a-z/' form of a script token for the CTC aligner ('' if nothing speakable)."""
    t = unicodedata.normalize("NFKC", token).lower().replace("’", "'")
    if any(is_arabic_char(c) for c in t):
        if uroman is None:
            return ""
        try:
            t = uroman.romanize_string(t).lower()
        except Exception:
            return ""
    # Digits are spoken: spell them so the aligner has letters to match.
    t = "".join(" " + _DIGITS[c] + " " if c in _DIGITS else c for c in t)
    t = re.sub(r"[^a-z' ]", " ", t)
    t = re.sub(r"\s+", "", t)
    return t


class MMSForcedAligner(ForcedAligner):
    name = "mms_fa"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._bundle = None

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
            import torchaudio  # noqa: F401
            from torchaudio.pipelines import MMS_FA  # noqa: F401
            return True
        except Exception:
            return False

    def _uroman(self):
        try:
            import uroman as ur

            return ur.Uroman()
        except Exception:
            return None

    def align(self, audio_path: str, tokens: list[str], language: str) -> list[tuple[float, float] | None]:
        try:
            import torch
            import torchaudio
            from torchaudio.pipelines import MMS_FA as bundle

            uro = self._uroman() if language in ("ur", "ps") else None
            if language in ("ur", "ps") and uro is None:
                raise StageError("uroman is required to align Urdu/Pashto script", Status.RETRYABLE_FAILURE)
            roman = [romanize_token(t, uro) for t in tokens]
            keep = [i for i, r in enumerate(roman) if r]
            if not keep:
                raise StageError("no alignable words in script", Status.PERMANENT_FAILURE)
            if self._bundle is None:  # load once; generated narration is aligned clip by clip
                model = bundle.get_model(with_star=False)
                model.eval()
                self._bundle = (model, bundle.get_tokenizer(), bundle.get_aligner())
            model, tokenizer, aligner = self._bundle
            wav, sr = _load_wav(audio_path)
            wav = torch.from_numpy(wav).unsqueeze(0)
            if sr != bundle.sample_rate:
                wav = torchaudio.functional.resample(wav, sr, bundle.sample_rate)
            with torch.inference_mode():
                emission, _ = model(wav)
            spans = aligner(emission[0], tokenizer([roman[i] for i in keep]))
            ratio = wav.size(1) / emission.size(1) / bundle.sample_rate
        except StageError:
            raise
        except Exception as exc:
            raise StageError(f"forced alignment failed: {type(exc).__name__}: {exc}", Status.RETRYABLE_FAILURE) from exc
        out: list[tuple[float, float] | None] = [None] * len(tokens)
        for i, sp in zip(keep, spans):
            if sp:
                out[i] = (float(sp[0].start * ratio), float(sp[-1].end * ratio))
        return out


def _load_wav(path: str):
    """Read the pipeline's PCM16 WAV without needing a torchaudio audio backend."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def get_aligner(cfg: dict) -> ForcedAligner | None:
    if not cfg.get("timing", {}).get("forced_alignment", True):
        return None
    a = MMSForcedAligner(cfg)
    return a if a.is_available() else None


def timing_from_alignment(sentences: list[str], spans: list[tuple[float, float] | None], duration: float) -> Timing:
    """Build sentence + per-word timing from per-token spans (gaps are interpolated)."""
    flat = [(si, tok) for si, s in enumerate(sentences) for tok in s.split()]
    if len(flat) != len(spans):
        raise StageError("alignment token count mismatch", Status.PERMANENT_FAILURE)
    known = [i for i, sp in enumerate(spans) if sp is not None]
    if len(known) < max(2, len(flat) // 3):
        raise StageError(f"only {len(known)} of {len(flat)} words aligned", Status.PERMANENT_FAILURE)
    times: list[tuple[float, float]] = []
    for i, sp in enumerate(spans):
        if sp is not None:
            times.append(sp)
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        lo = spans[prev][1] if prev is not None else 0.0
        hi = spans[nxt][0] if nxt is not None else duration
        n_gap = (nxt if nxt is not None else len(spans)) - (prev if prev is not None else -1) - 1
        pos = i - (prev if prev is not None else -1)
        a = lo + (hi - lo) * (pos - 1) / max(1, n_gap)
        b = lo + (hi - lo) * pos / max(1, n_gap)
        times.append((a, max(a + 0.02, b)))
    per: list[list[dict]] = [[] for _ in sentences]
    for (si, tok), (a, b), sp in zip(flat, times, spans):
        per[si].append({"word": tok, "start": round(a, 3), "end": round(b, 3), "estimated": sp is None})
    timed = []
    for si, s in enumerate(sentences):
        ws = per[si]
        start = 0.0 if si == 0 else ws[0]["start"] - 0.05 if ws else 0.0
        timed.append(TimedSentence(s, start, 0.0, ws))
    for si in range(len(timed)):
        timed[si].end = timed[si + 1].start if si + 1 < len(timed) else duration
        timed[si].start = min(timed[si].start, timed[si].end - 0.1)
    coverage = len(known) / len(flat)
    return Timing(timed, "forced_alignment", round(0.6 + 0.35 * coverage, 3), {"aligned_words": len(known), "words": len(flat),
                  # torchaudio's MMS_FA weights: only word times are taken from it, but the licence is still non-commercial
                  "model": "torchaudio MMS_FA", "model_license": "CC-BY-NC-4.0", "commercial_use": "no (non-commercial weights)"})
