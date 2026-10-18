"""Integration and end-to-end tests (render real MP4 files with FFmpeg)."""

import json
import subprocess
from pathlib import Path

import pytest

from engine import cli
from engine.config import load_config
from engine.input_discovery import discover_inputs
from engine.pipeline import LanguageJob
from engine.results import Status
from engine.video_assembler import ffmpeg_command

from .conftest import HAS_ESPEAK, FakeTranscriber, ToneTTS, espeak_audio, tone

ROOT = Path(__file__).resolve().parent.parent
needs_espeak = pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng not installed")


def make_cfg(**video):
    over = {"video": {"quality": "fast", **video}, "transcription": {"engine": "none"}, "voice": {"local_engines": ["espeak"]}}
    return load_config(overrides=over, repo_root=ROOT)


def run_job(repo, key, cfg, **kw):
    inputs = discover_inputs(cfg, None, repo)[key]
    job = LanguageJob(inputs, cfg, repo / "output", repo / "tmp", log=lambda m: None, **kw)
    return job.run()


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], capture_output=True, text=True).stdout
    return json.loads(out)


def test_ffmpeg_command_construction(cfg):
    cmd = ffmpeg_command(1080, 1920, 30, "a.wav", "o.mp4", 12.5, cfg)
    s = " ".join(cmd)
    for part in ("-pix_fmt yuv420p", "-c:v libx264", "-c:a aac", "-ar 48000", "-s 1080x1920", "-r 30", "-movflags +faststart", "-t 12.500"):
        assert part in s
    with pytest.raises(ValueError):
        ffmpeg_command(1081, 1920, 30, "a", "b", 1, cfg)


@needs_espeak
def test_english_script_with_supplied_audio(repo):
    cfg = make_cfg()
    (repo / "input/scripts/english.txt").write_text("A person starts investing with fifty dollars. Over time, their money grows by one hundred percent.")
    espeak_audio(repo / "input/audio/english.wav", "A person starts investing with fifty dollars. Over time, their money grows by one hundred percent.")
    r = run_job(repo, "english", cfg)
    assert r.status == Status.SUCCESS, r.message
    meta = json.loads((repo / "output/english/metadata.json").read_text())
    assert meta["voice"]["provider"] == "supplied"
    assert meta["timing"]["source"] == "estimated"
    assert r.quality["passed"]


@needs_espeak
def test_english_script_without_audio_generates_narration(repo):
    cfg = make_cfg()
    (repo / "input/scripts/english.txt").write_text("A worker receives a salary from the company.")
    r = run_job(repo, "english", cfg)
    assert r.status == Status.SUCCESS, r.message
    meta = json.loads((repo / "output/english/metadata.json").read_text())
    assert meta["voice"]["provider"] == "espeak" and meta["timing"]["source"] == "exact"


def test_audio_without_script_is_transcribed(repo):
    cfg = make_cfg()
    tone(repo / "input/audio/english.mp3", 4.0)
    text = "Prices rise every year. Families save less money."
    r = run_job(repo, "english", cfg, transcriber=FakeTranscriber(text, 4.0))
    assert r.status == Status.SUCCESS, r.message
    assert (repo / "output/english/transcript.txt").read_text().strip() == text
    assert json.loads((repo / "output/english/metadata.json").read_text())["timing"]["source"] == "words"


def test_audio_without_script_and_no_recogniser_fails_clearly(repo):
    cfg = make_cfg()
    tone(repo / "input/audio/english.mp3", 2.0)
    r = run_job(repo, "english", cfg)
    assert r.status == Status.PERMANENT_FAILURE
    assert "speech-recognition" in r.message


@needs_espeak
def test_roman_urdu_script_with_supplied_audio(repo):
    cfg = make_cfg()
    (repo / "input/scripts/roman_urdu.txt").write_text("Aik shakhs ne pachas dollar lagaye. Waqt ke saath paisa 100 fisad barh gaya.")
    espeak_audio(repo / "input/audio/roman_urdu.wav", "ایک شخص نے پچاس ڈالر لگائے۔ وقت کے ساتھ پیسہ سو فیصد بڑھ گیا۔", "ur")
    r = run_job(repo, "roman_urdu", cfg)
    assert r.status == Status.SUCCESS, r.message
    plan = json.loads((repo / "output/roman_urdu/scene_plan.json").read_text())
    assert plan["language"] == "roman_urdu"
    srt = (repo / "output/roman_urdu/captions.srt").read_text()
    assert "Aik shakhs" in srt  # captions keep the original Roman Urdu text


@needs_espeak
def test_urdu_script_and_audio_with_rtl_numeric_captions(repo):
    cfg = make_cfg()
    text = "منافع 100% ہو گیا۔ مہنگائی کی وجہ سے لوگ کم بچت کرتے ہیں۔"
    (repo / "input/scripts/urdu.txt").write_text(text)
    espeak_audio(repo / "input/audio/urdu.wav", text, "ur")
    r = run_job(repo, "urdu", cfg)
    assert r.status == Status.SUCCESS, r.message
    checks = {c["name"]: c for c in r.quality["checks"]}
    assert checks["glyph_coverage"]["passed"] and checks["caption_overflow"]["passed"]


def test_pashto_with_compatible_test_voice(repo):
    cfg = make_cfg()
    (repo / "input/scripts/pashto.txt").write_text("یو سړی دوکان ته ځي او ټیلیفون اخلي. ګټه یې سل سلنه شوه.")
    r = run_job(repo, "pashto", cfg, tts_chain=[ToneTTS(cfg, ("ps",))])
    assert r.status == Status.SUCCESS, r.message
    plan = json.loads((repo / "output/pashto/scene_plan.json").read_text())
    assert any(q["display"] == "100%" for s in plan["scenes"] for q in s["quantities"])


@needs_espeak
def test_invalid_audio_falls_back_to_tts(repo):
    cfg = make_cfg()
    (repo / "input/scripts/english.txt").write_text("Savings grow when you save money every month.")
    (repo / "input/audio/english.mp3").write_bytes(b"this is not audio" * 50)
    r = run_job(repo, "english", cfg)
    assert r.status == Status.PARTIAL_SUCCESS, r.message
    assert json.loads((repo / "output/english/metadata.json").read_text())["voice"]["provider"] == "espeak"


def test_corrupt_script_without_audio_fails(repo):
    cfg = make_cfg()
    (repo / "input/scripts/english.txt").write_bytes(b"\x00\x00\x00\x00binary")
    r = run_job(repo, "english", cfg)
    assert r.status == Status.PERMANENT_FAILURE and "Invalid script" in r.message


def test_duration_limit_is_enforced_without_truncation(repo):
    cfg = make_cfg(maximum_duration_seconds=3)
    (repo / "input/scripts/english.txt").write_text("Money grows over time.")
    tone(repo / "input/audio/english.mp3", 6.0)
    r = run_job(repo, "english", cfg)
    assert r.status == Status.PERMANENT_FAILURE and "not cut" in r.message
    cfg2 = make_cfg(maximum_duration_seconds=3, allow_longer_than_maximum=True)
    r2 = run_job(repo, "english", cfg2)
    assert r2.status == Status.PARTIAL_SUCCESS, r2.message
    dur = float(probe(repo / "output/english/video.mp4")["format"]["duration"])
    assert dur > 6.0  # full narration kept


@needs_espeak
def test_one_language_fails_others_succeed_via_cli(repo, monkeypatch):
    (repo / "input/scripts/english.txt").write_text("A person buys a phone from the shop.")
    (repo / "input/scripts/pashto.txt").write_text("یو سړی دوکان ته ځي.")  # no Pashto voice installed in CI
    (repo / "input/config.yaml").write_text("voice:\n  local_engines: [espeak]\n  allow_noncommercial_models: false\ntranscription:\n  engine: none\n")
    code = cli.main(["run", "--repo-root", str(repo), "--quality", "fast"])
    assert code == 0
    gen = json.loads((repo / "output/reports/generation_report.json").read_text())
    assert gen["overall"] == "partial_success"
    assert "english" in gen["summary"]["SUCCESS"] and "pashto" in gen["summary"]["PERMANENT_FAILURE"]
    assert "urdu" in gen["summary"]["SKIPPED"]
    assert (repo / "output/english/video.mp4").exists() and not (repo / "output/pashto").exists()
    assert (repo / "output/reports/asset_licenses.json").exists()
    assert (repo / "output/README.txt").exists()


def test_nothing_to_do_produces_diagnostic_report(repo):
    code = cli.main(["run", "--repo-root", str(repo)])
    assert code == 0
    gen = json.loads((repo / "output/reports/generation_report.json").read_text())
    assert gen["overall"] == "nothing_to_do"
    assert "No video was produced" in (repo / "output/reports/summary.md").read_text()


@needs_espeak
def test_end_to_end_acceptance_standard_profile(repo):
    """Acceptance: script + locally generated voice-over -> validated 1080x1920 MP4."""
    script = ("A person starts investing with fifty dollars. Over time, their money grows by one hundred percent. "
              "Because prices rise, families save less money.")
    (repo / "input/scripts/english.txt").write_text(script)
    espeak_audio(repo / "input/audio/english.wav", script)
    cfg = load_config(overrides={"transcription": {"engine": "none"}}, repo_root=ROOT)
    r = run_job(repo, "english", cfg)
    assert r.status == Status.SUCCESS, r.message
    out = repo / "output/english"
    meta = probe(out / "video.mp4")
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    a = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"], v["pix_fmt"]) == (1080, 1920, "h264", "yuv420p")
    assert v["avg_frame_rate"] in ("30/1", "30000/1000")
    assert a["codec_name"] == "aac" and a["sample_rate"] == "48000"
    plan = json.loads((out / "scene_plan.json").read_text())
    assert any(s["scene_type"] not in ("text_emphasis",) for s in plan["scenes"])
    assert r.quality["passed"]
    for name in ("captions.srt", "captions.vtt", "transcript.txt", "metadata.json", "thumbnail.png", "quality_report.json"):
        assert (out / name).exists(), name
    assert str(out / "video.mp4") in r.outputs.values()


def test_broken_recogniser_does_not_fail_script_plus_audio(repo):
    class Broken(FakeTranscriber):
        def transcribe(self, audio_path, language):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

    cfg = make_cfg()
    (repo / "input/scripts/english.txt").write_text("[curious] Money grows over time. [pause] Prices rise every year.")
    tone(repo / "input/audio/english.mp3", 5.0)
    r = run_job(repo, "english", cfg, transcriber=Broken("", 5.0))
    assert r.status == Status.SUCCESS, r.message
    assert "[" not in (repo / "output/english/captions.srt").read_text()
