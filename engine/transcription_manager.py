"""Speech recognition for voice-overs that arrive without a script.

Adapter: faster-whisper (MIT) with OpenAI Whisper weights (MIT), run locally
on CPU. Whisper supports English, Urdu and Pashto; Roman Urdu audio is
recognised as Urdu and transliterated to Roman script (approximate, flagged).
NOTE: the model download (Hugging Face) was unreachable in the development
container, so the adapter is covered by mocked tests only; availability is
detected at run time and low-confidence results are reported as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .results import StageError, Status
from .transliteration import urdu_to_roman

WHISPER_LANG = {"en": "en", "ur": "ur", "roman_urdu": "ur", "ps": "ps", "ps_latn": "ps"}


@dataclass
class Transcript:
    text: str
    language: str
    segments: list[dict]
    words: list[dict]
    confidence: float
    engine: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"text": self.text, "language": self.language, "confidence": round(self.confidence, 3), "engine": self.engine,
                "notes": self.notes, "segments": self.segments, "words": self.words}


class Transcriber:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def transcribe(self, audio_path: str, language: str) -> Transcript:
        raise NotImplementedError


class FasterWhisperTranscriber(Transcriber):
    name = "faster_whisper"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            t = self.cfg["transcription"]
            try:
                self._model = WhisperModel(t["model"], device="cpu", compute_type=t["compute_type"])
            except Exception as exc:
                raise StageError(f"Could not load Whisper model '{t['model']}': {exc}", Status.RETRYABLE_FAILURE) from exc
        return self._model

    def transcribe(self, audio_path: str, language: str) -> Transcript:
        model = self._load()
        segments, words, logps = [], [], []
        try:
            segments_iter, info = model.transcribe(audio_path, language=WHISPER_LANG.get(language, language), word_timestamps=True, vad_filter=True)
            for seg in segments_iter:
                segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip(), "avg_logprob": seg.avg_logprob})
                logps.append(seg.avg_logprob)
                for w in seg.words or []:
                    words.append({"word": w.word.strip(), "start": w.start, "end": w.end, "probability": w.probability})
        except Exception as exc:  # library/runtime problems must not abort the whole language
            raise StageError(f"Speech recognition failed: {type(exc).__name__}: {exc}", Status.RETRYABLE_FAILURE) from exc
        return finalize_transcript(segments, words, logps, language, self.name)


def finalize_transcript(segments: list[dict], words: list[dict], logps: list[float], language: str, engine: str) -> Transcript:
    text = " ".join(s["text"] for s in segments).strip()
    conf = float(sum(math.exp(lp) for lp in logps) / len(logps)) if logps else 0.0
    notes = []
    if language == "roman_urdu":
        text = urdu_to_roman(text)
        for w in words:
            w["word"] = urdu_to_roman(w["word"])
        for s in segments:
            s["text"] = urdu_to_roman(s["text"])
        notes.append("Recognised as Urdu and transliterated to Roman Urdu by rule; spelling is approximate.")
        conf *= 0.85
    if not text:
        raise StageError("Transcription produced no text", Status.PERMANENT_FAILURE)
    return Transcript(text, language, segments, words, conf, engine, notes)


def get_transcriber(cfg: dict) -> Transcriber | None:
    engine = cfg["transcription"]["engine"]
    if engine == "faster_whisper":
        t = FasterWhisperTranscriber(cfg)
        return t if t.is_available() else None
    return None
