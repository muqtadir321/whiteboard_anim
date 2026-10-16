"""eSpeak NG adapter (local, GPL-3.0 program; generated audio is not restricted).

Tested in development for English (en-us) and Urdu (ur). eSpeak NG has no
Pashto voice, so Pashto is reported as unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache

from .base import TTSError, TTSProvider


@lru_cache(maxsize=1)
def _voices() -> set[str]:
    exe = shutil.which("espeak-ng")
    if not exe:
        return set()
    try:
        out = subprocess.run([exe, "--voices"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names = set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            names.add(parts[1])
    return names


class EspeakProvider(TTSProvider):
    name = "espeak"
    license = "GPL-3.0-or-later (eSpeak NG)"
    commercial_use = "yes (output audio is not covered by the GPL)"

    def voice_for(self, language: str) -> str | None:
        return self.cfg["voice"]["espeak_voices"].get(language)

    def is_available(self, language: str) -> bool:
        voice = self.voice_for(language)
        return bool(voice and voice in _voices())

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        voice = self.voice_for(language)
        if not voice:
            raise TTSError(f"No eSpeak voice configured for {language}")
        # Text is passed through a file, never through a shell.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            tmp = fh.name
        rate = "150" if language == "en" else "140"
        proc = subprocess.run(["espeak-ng", "-v", voice, "-s", rate, "-g", "4", "-f", tmp, "-w", output_path], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise TTSError(f"espeak-ng failed: {proc.stderr.strip()[-300:]}")
        return output_path
