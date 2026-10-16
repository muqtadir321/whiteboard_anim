"""Provider-independent TTS interface."""

from __future__ import annotations

import os
from pathlib import Path

from ..results import StageError, Status


class TTSError(StageError):
    pass


def cache_dir(sub: str) -> Path:
    root = Path(os.environ.get("WHITEBOARD_CACHE", Path.home() / ".cache" / "whiteboard-engine"))
    path = root / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


class TTSProvider:
    """Base class. `language` is a pipeline code: en | ur | ps (Roman Urdu is
    transliterated to Urdu script by the voice manager when a provider needs it)."""

    name = "base"
    online = False
    license = "unknown"
    commercial_use = "unknown"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def is_available(self, language: str) -> bool:
        raise NotImplementedError

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        raise NotImplementedError

    def describe(self, language: str) -> dict:
        return {"provider": self.name, "online": self.online, "license": self.license, "commercial_use": self.commercial_use, "language": language}


class RetryableTTSError(TTSError):
    def __init__(self, message: str):
        super().__init__(message, Status.RETRYABLE_FAILURE)
