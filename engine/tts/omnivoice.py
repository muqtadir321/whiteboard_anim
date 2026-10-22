"""OmniVoice zero-shot voice-cloning TTS (k2-fsa, 600+ languages).

Licence: the code is Apache-2.0, but the pre-trained model is CC-BY-NC (model card, "due to
constraints from its training data"): NON-COMMERCIAL. It is only used when
voice.allow_noncommercial_models is true, like Meta MMS, and recorded in each video's metadata.

Speaks the script directly in the reference speaker's voice, in English, Urdu
and Pashto (and Roman Urdu via derived Urdu-script text). Only used when a
reference voice exists (input/voice/reference.*). Model weights download from
Hugging Face on first use and are cached. NOTE: not exercised in the
development container (Hugging Face unreachable); covered by mocked tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..audio_processor import probe, run
from .base import RetryableTTSError, TTSError, TTSProvider, cache_dir

LANGUAGE_IDS = {"en": "en", "ur": "ur", "ps": "ps"}


class OmniVoiceProvider(TTSProvider):
    name = "omnivoice"
    license = "code Apache-2.0; pre-trained model CC-BY-NC (k2-fsa/OmniVoice model card); reference voice must be yours or licensed"
    commercial_use = "no (non-commercial model weights)"
    clones_voice = True
    # Whole paragraphs sound continuous; sentence-by-sentence output adds an
    # audible breath/tail and a gap at every full stop.
    speaks_paragraphs = True

    def __init__(self, cfg: dict, reference: Path | None = None):
        super().__init__(cfg)
        self.reference = reference
        self._model = None
        self._prompt = None

    def is_available(self, language: str) -> bool:
        if self.reference is None or language not in LANGUAGE_IDS:
            return False
        if language not in self.cfg["voice"]["clone"].get("omnivoice_languages", list(LANGUAGE_IDS)):
            return False
        if not self.cfg["voice"].get("allow_noncommercial_models", False):
            self.unavailable_reason = "OmniVoice weights are CC-BY-NC (non-commercial); set voice.allow_noncommercial_models: true to use them"
            return False
        try:
            import torch  # noqa: F401
            from omnivoice import OmniVoice  # noqa: F401
            return True
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"[:200]
            return False

    def _reference_clip(self) -> Path:
        """A clean 3-12 s excerpt: OmniVoice clones best from short references."""
        key = hashlib.sha256(self.reference.read_bytes()).hexdigest()[:16]
        out = cache_dir("omnivoice") / f"ref_{key}.wav"
        if not out.exists():
            info = probe(self.reference)
            seconds = min(12.0, info.duration)
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(self.reference), "-af",
                 "silenceremove=start_periods=1:start_threshold=-45dB,highpass=f=70,loudnorm=I=-18:TP=-2",
                 "-t", f"{seconds:.2f}", "-ac", "1", "-ar", "24000", str(out)], timeout=300)
        return out

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from omnivoice import OmniVoice

            c = self.cfg["voice"]["clone"]
            self._model = OmniVoice.from_pretrained(c["omnivoice_model"], device_map="cpu", dtype=torch.float32)
            ref_text = (c.get("reference_text") or "").strip() or None
            self._prompt = self._model.create_voice_clone_prompt(str(self._reference_clip()), ref_text=ref_text)
        except Exception as exc:
            self._model = None
            raise RetryableTTSError(f"Could not load OmniVoice / build the voice prompt: {exc}") from exc
        return self._model

    def synthesize_batch(self, texts: list[str], language: str, output_paths: list[str]) -> list[str]:
        model = self._load()
        import soundfile as sf

        steps = int(self.cfg["voice"]["clone"]["omnivoice_steps"])
        try:
            audios = model.generate(text=list(texts), language=LANGUAGE_IDS[language], voice_clone_prompt=[self._prompt] * len(texts), num_step=steps)
        except Exception as exc:
            raise TTSError(f"OmniVoice generation failed: {exc}") from exc
        for audio, path in zip(audios, output_paths):
            sf.write(path, audio, model.sampling_rate)
        return output_paths

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        return self.synthesize_batch([text], language, [output_path])[0]
