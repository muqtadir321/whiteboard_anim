import shutil
import subprocess
from pathlib import Path

import pytest

from engine import cli
from engine.voice_clone import CloneError, VoiceCloner, apply_clone, resolve_reference
from engine.voice_manager import synthesize_sentences

from .conftest import ToneTTS, tone


class CopyCloner(VoiceCloner):
    """Fake converter: returns the input unchanged (or stretched to test the timing guard)."""

    name = "fake_clone"
    license = "test"

    def __init__(self, stretch: float = 1.0):
        self.stretch = stretch
        self.references = []

    def is_available(self):
        return True

    def convert_parts(self, parts, reference, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.references.append(reference)
        outs = []
        for p in parts:
            o = out_dir / (p.stem + "_cloned.wav")
            if self.stretch == 1.0:
                shutil.copy(p, o)
            else:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-af", f"atempo={1 / self.stretch}", str(o)], check=True)
            outs.append(o)
        return outs


def test_reference_resolution(cfg, repo):
    assert resolve_reference(cfg, repo) is None  # auto + no file -> cloning off
    (repo / "input/voice").mkdir(parents=True)
    tone(repo / "input/voice/reference.mp3", 12)
    assert resolve_reference(cfg, repo).name == "reference.mp3"  # other extension accepted
    cfg["voice"]["clone"]["enabled"] = False
    assert resolve_reference(cfg, repo) is None
    cfg["voice"]["clone"]["enabled"] = True
    cfg["voice"]["clone"]["reference_path"] = "input/voice/missing.wav"
    with pytest.raises(CloneError):
        resolve_reference(cfg, repo)


def test_cloned_voice_used_for_all_languages(cfg, tmp_path):
    ref = tone(tmp_path / "ref.wav", 12)
    for lang in ("en", "ur", "ps"):
        cloner = CopyCloner()
        r = synthesize_sentences(["One sentence.", "Two sentences."], lang, cfg, tmp_path / lang, log=lambda m: None,
                                 chain=[ToneTTS(cfg, (lang,))], reference=ref, cloner=cloner)
        assert r.clone["applied"] is True and cloner.references == [ref]
        assert r.timing.source == "exact"


def test_clone_falls_back_when_timing_drifts(cfg, tmp_path):
    ref = tone(tmp_path / "ref.wav", 12)
    parts = [tone(tmp_path / "a.wav", 2.0)]
    out, info = apply_clone(parts, ref, cfg, tmp_path / "c", CopyCloner(stretch=1.5), log=lambda m: None)
    assert out == parts and info["applied"] is False and "duration" in info["reason"]


def test_clone_engine_missing_is_reported_not_fatal(cfg, tmp_path):
    ref = tone(tmp_path / "ref.wav", 12)
    parts = [tone(tmp_path / "a.wav", 2.0)]
    out, info = apply_clone(parts, ref, cfg, tmp_path / "c", None)
    assert out == parts and "not installed" in info["reason"]


def test_deps_requests_mms_and_clone(repo, capsys):
    (repo / "input/scripts/urdu.txt").write_text("ایک شخص نے پچاس ڈالر لگائے۔", encoding="utf-8")
    (repo / "input/scripts/pashto.txt").write_text("یو سړی.", encoding="utf-8")
    (repo / "input/voice").mkdir(parents=True)
    tone(repo / "input/voice/reference.wav", 12)
    cli.main(["deps", "--repo-root", str(repo)])
    out = capsys.readouterr().out
    assert "mms=false" in out and "voice_clone=true" in out  # MMS is non-commercial: off by default
    (repo / "input/config.yaml").write_text("voice:\n  allow_noncommercial_models: true\n", encoding="utf-8")
    cli.main(["deps", "--repo-root", str(repo)])
    out = capsys.readouterr().out
    assert "mms=true" in out and "voice_clone=true" in out and "transcription=false" in out


def test_omnivoice_speaks_in_reference_voice_for_pashto(cfg, tmp_path, monkeypatch):
    """OmniVoice adapter contract, with a fake omnivoice package (no model download)."""
    import sys
    import types

    import numpy as np

    calls = {}

    class FakeModel:
        sampling_rate = 24000

        @classmethod
        def from_pretrained(cls, name, **kw):
            calls["model"] = name
            return cls()

        def create_voice_clone_prompt(self, ref_audio, ref_text=None):
            calls["ref"] = ref_audio
            return "PROMPT"

        def generate(self, text, language, voice_clone_prompt, num_step):
            calls["generate"] = (list(text), language, voice_clone_prompt, num_step)
            t = np.linspace(0, 1.5, int(1.5 * self.sampling_rate), endpoint=False)
            return [0.3 * np.sin(2 * np.pi * 220 * t).astype("float32") for _ in text]

    fake = types.ModuleType("omnivoice")
    fake.OmniVoice = FakeModel
    monkeypatch.setitem(sys.modules, "omnivoice", fake)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(float32="float32"))
    pytest.importorskip("soundfile")
    monkeypatch.setenv("WHITEBOARD_CACHE", str(tmp_path / "cache"))
    ref = tone(tmp_path / "ref.wav", 20)
    from engine.voice_manager import build_chain

    chain = build_chain(cfg, ref)
    assert chain[0].name == "omnivoice"
    r = synthesize_sentences(["یو سړی.", "پیسې زیاتې شوې."], "ps", cfg, tmp_path / "ps", log=lambda m: None, chain=chain[:1], reference=ref)
    assert r.provider["provider"] == "omnivoice"
    assert r.clone["applied"] and r.clone["engine"] == "omnivoice"
    assert calls["generate"][1] == "ps" and len(calls["generate"][0]) == 2  # one batched call
    assert Path(calls["ref"]).exists()
