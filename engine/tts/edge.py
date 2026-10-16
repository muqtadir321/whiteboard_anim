"""Optional online adapter using the `edge-tts` package.

`edge-tts` talks to Microsoft Edge's Read Aloud service, which is NOT a
documented public API. It is therefore disabled unless the user lists
"edge_tts" in voice.online_providers. Requests are rate-limited by the bounded
retry policy and never retried aggressively.
"""

from __future__ import annotations

import asyncio

from .base import RetryableTTSError, TTSError, TTSProvider


class EdgeTTSProvider(TTSProvider):
    name = "edge_tts"
    online = True
    license = "edge-tts: LGPL-3.0; service terms: Microsoft (unofficial interface)"
    commercial_use = "unclear (unofficial service)"

    def is_available(self, language: str) -> bool:
        if "edge_tts" not in self.cfg["voice"].get("online_providers", []) or not self.cfg["voice"]["allow_online_free_tts"]:
            return False
        try:
            import edge_tts  # noqa: F401
        except Exception:
            return False
        return bool(self.cfg["voice"]["edge_voices"].get(language))

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        import edge_tts

        voice = self.cfg["voice"]["edge_voices"].get(language)
        if not voice:
            raise TTSError(f"No edge-tts voice for {language}")
        mp3 = output_path + ".mp3"
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(mp3))
        except Exception as exc:
            raise RetryableTTSError(f"edge-tts request failed: {exc}") from exc
        from ..audio_processor import run

        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", mp3, output_path], timeout=120)
        return output_path
