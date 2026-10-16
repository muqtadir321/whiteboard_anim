"""Piper neural TTS adapter (local, offline after the voice model is cached).

Voice models are downloaded from the public `rhasspy/piper-voices` repository
on Hugging Face and cached. The default English voice is trained on the public
domain LJ Speech dataset. NOTE: not exercised in the development container
(Hugging Face was unreachable); availability is detected at run time.
"""

from __future__ import annotations

import urllib.request
import wave
from pathlib import Path

from .base import RetryableTTSError, TTSError, TTSProvider, cache_dir

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_path(voice: str) -> str:
    # en_US-ljspeech-medium -> en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
    locale, name, quality = voice.split("-", 2)
    return f"{locale.split('_')[0]}/{locale}/{name}/{quality}/{voice}.onnx"


class PiperProvider(TTSProvider):
    name = "piper"
    license = "piper: GPL-3.0 (piper1) / MIT (legacy); voice: see model card (LJ Speech dataset is public domain)"
    commercial_use = "check voice model card"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._loaded: dict[str, object] = {}

    def _module(self):
        try:
            import piper  # noqa: F401
            from piper import PiperVoice
            return PiperVoice
        except Exception:
            return None

    def is_available(self, language: str) -> bool:
        return bool(self.cfg["voice"]["piper_voices"].get(language)) and self._module() is not None

    def _ensure_model(self, voice: str) -> Path:
        d = cache_dir("piper")
        model = d / f"{voice}.onnx"
        cfgf = d / f"{voice}.onnx.json"
        timeout = self.cfg["automation"]["download_timeout_seconds"]
        for target, url in ((model, f"{BASE_URL}/{_voice_path(voice)}"), (cfgf, f"{BASE_URL}/{_voice_path(voice)}.json")):
            if target.exists() and target.stat().st_size > 0:
                continue
            try:
                tmp = target.with_suffix(target.suffix + ".part")
                with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as out:
                    out.write(resp.read())
                tmp.rename(target)
            except Exception as exc:
                raise RetryableTTSError(f"Could not download Piper voice {voice}: {exc}") from exc
        return model

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        voice_name = self.cfg["voice"]["piper_voices"].get(language)
        PiperVoice = self._module()
        if not voice_name or PiperVoice is None:
            raise TTSError("Piper is not available")
        if voice_name not in self._loaded:
            self._loaded[voice_name] = PiperVoice.load(str(self._ensure_model(voice_name)))
        voice = self._loaded[voice_name]
        with wave.open(output_path, "wb") as wf:
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(text, wf)  # piper-tts >= 1.3
            else:
                voice.synthesize(text, wf)  # piper-tts 1.2
        if Path(output_path).stat().st_size < 1000:
            raise TTSError("Piper produced an empty file")
        return output_path
