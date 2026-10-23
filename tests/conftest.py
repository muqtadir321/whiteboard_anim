import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import load_config  # noqa: E402
from engine.transcription_manager import Transcriber, finalize_transcript  # noqa: E402
from engine.tts.base import TTSProvider  # noqa: E402

HAS_ESPEAK = shutil.which("espeak-ng") is not None


@pytest.fixture
def cfg():
    return load_config(repo_root=ROOT)


@pytest.fixture
def fast_cfg():
    c = load_config(overrides={"video": {"quality": "fast"}}, repo_root=ROOT)
    return c


@pytest.fixture
def repo(tmp_path):
    """An empty repository layout with input folders."""
    (tmp_path / "input" / "scripts").mkdir(parents=True)
    (tmp_path / "input" / "audio").mkdir(parents=True)
    return tmp_path


def tone(path: Path, seconds: float = 3.0, freq: int = 220) -> Path:
    """Speech-like test audio: amplitude-modulated tone with pauses (locally generated)."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-af", "volume='if(lt(mod(t,1.2),0.9),1,0)':eval=frame", "-ar", "48000", str(path)], check=True)
    return path


def espeak_audio(path: Path, text: str, voice: str = "en-us") -> Path:
    subprocess.run(["espeak-ng", "-v", voice, "-w", str(path), text], check=True, capture_output=True)
    return path


class ToneTTS(TTSProvider):
    """Deterministic local test voice usable for any language (e.g. Pashto test voice)."""

    name = "test_tone"
    license = "test fixture"
    commercial_use = "test only"

    def __init__(self, cfg, languages=("ps",)):
        super().__init__(cfg)
        self.languages = set(languages)
        self.calls = []

    def is_available(self, language):
        return language in self.languages

    def synthesize(self, text, language, output_path):
        self.calls.append((text, language))
        seconds = max(1.0, len(text) / 14.0)
        tone(Path(output_path), seconds)
        return output_path


class FakeTranscriber(Transcriber):
    """Returns a fixed transcript with evenly spaced word timestamps."""

    name = "fake"

    def __init__(self, text, duration):
        self.text = text
        self.duration = duration

    def is_available(self):
        return True

    def transcribe(self, audio_path, language):
        words = self.text.split()
        step = self.duration / max(1, len(words))
        w = [{"word": x, "start": i * step, "end": (i + 1) * step, "probability": 0.9} for i, x in enumerate(words)]
        seg = [{"start": 0.0, "end": self.duration, "text": self.text, "avg_logprob": -0.2}]
        return finalize_transcript(seg, w, [-0.2], language, self.name)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never use the internet (SRS): any connection to a non-local host fails at once instead of
    hanging on DNS. Code under test falls back exactly as it does offline."""
    import socket

    real = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        if host in (None, "localhost", "127.0.0.1", "::1") or str(host).startswith("127."):
            return real(host, *args, **kwargs)
        raise socket.gaierror(f"network disabled in tests: {host}")

    monkeypatch.setattr(socket, "getaddrinfo", local_only)
