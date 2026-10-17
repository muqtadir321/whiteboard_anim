"""Voice cloning for generated narration: make TTS output sound like a reference voice.

Engine: OpenVoice V2 tone-colour converter (MIT licence, MyShell). It changes the
*timbre* of any input speech to match a short reference recording and works for
any language, because it does not synthesise text itself. The language,
pronunciation and rhythm still come from the underlying TTS voice (Piper, MMS,
eSpeak NG ...), so the result sounds like the reference speaker reading with
that voice's accent.

Only generated narration is converted. A supplied voice-over is never altered.
Use a reference of your own voice (or one you have permission to use).

NOTE: not exercised in the development container (model hosts unreachable);
covered by tests with a fake converter. Any failure keeps the unconverted
narration and is reported in metadata.json.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .audio_processor import probe, run
from .results import StageError, Status
from .tts.base import cache_dir

CONVERTER_REPO = "myshell-ai/OpenVoiceV2"
CONVERTER_FILES = ("converter/config.json", "converter/checkpoint.pth")
SAMPLE_RATE = 22050


class CloneError(StageError):
    pass


class VoiceCloner:
    name = "base"
    license = "unknown"
    commercial_use = "unknown"

    def is_available(self) -> bool:
        raise NotImplementedError

    def convert_parts(self, parts: list[Path], reference: Path, out_dir: Path) -> list[Path]:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"engine": self.name, "license": self.license, "commercial_use": self.commercial_use}


def prepare_reference(reference: Path, work: Path, max_seconds: float = 60.0, chunk: float = 8.0) -> list[Path]:
    """Mono 22.05 kHz, loudness-normalised chunks of the reference voice."""
    work.mkdir(parents=True, exist_ok=True)
    info = probe(reference)
    if info.duration < 4.0:
        raise CloneError(f"Reference voice is only {info.duration:.1f}s; record at least 10 seconds of clear speech", Status.PERMANENT_FAILURE)
    full = work / "reference_22k.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(reference), "-t", f"{max_seconds}", "-af",
         "highpass=f=70,loudnorm=I=-18:TP=-2", "-ac", "1", "-ar", str(SAMPLE_RATE), str(full)], timeout=300)
    pattern = work / "ref_%03d.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(full), "-f", "segment", "-segment_time", f"{chunk}",
         "-c:a", "pcm_s16le", str(pattern)], timeout=300)
    chunks = sorted(p for p in work.glob("ref_*.wav") if probe(p).duration >= 2.0)
    return chunks or [full]


class OpenVoiceCloner(VoiceCloner):
    name = "openvoice_v2"
    license = "MIT (OpenVoice V2 code and checkpoints)"
    commercial_use = "yes (MIT); you must have the right to use the reference voice"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._converter = None

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
            from openvoice.api import ToneColorConverter  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._converter is not None:
            return self._converter
        try:
            from huggingface_hub import hf_hub_download
            from openvoice.api import ToneColorConverter

            from openvoice.api import OpenVoiceBaseClass

            paths = [hf_hub_download(CONVERTER_REPO, f, cache_dir=str(cache_dir("openvoice"))) for f in CONVERTER_FILES]

            class _Converter(ToneColorConverter):
                """ToneColorConverter without the optional audio watermark (and its extra model)."""

                def __init__(self, config_path, device):
                    OpenVoiceBaseClass.__init__(self, config_path, device=device)
                    self.watermark_model = None
                    self.version = getattr(self.hps, "_version_", "v1")

            conv = _Converter(paths[0], device="cpu")
            conv.load_ckpt(paths[1])
        except Exception as exc:
            raise CloneError(f"Could not load OpenVoice converter: {exc}", Status.RETRYABLE_FAILURE) from exc
        self._converter = conv
        return conv

    def _embedding(self, wavs: list[Path], key: str):
        import torch

        path = cache_dir("openvoice") / f"se_{key}.pth"
        if path.exists():
            return torch.load(str(path))
        se = self._load().extract_se([str(w) for w in wavs], se_save_path=str(path))
        return se

    def convert_parts(self, parts: list[Path], reference: Path, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        conv = self._load()
        ref_key = hashlib.sha256(reference.read_bytes()).hexdigest()[:16]
        tgt_se = self._embedding(prepare_reference(reference, out_dir / "reference"), "ref_" + ref_key)
        src22 = []
        for p in parts:
            q = out_dir / (p.stem + "_22k.wav")
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(p), "-ac", "1", "-ar", str(SAMPLE_RATE), str(q)], timeout=120)
            src22.append(q)
        src_key = hashlib.sha256(b"".join(q.read_bytes()[:200000] for q in src22[:5])).hexdigest()[:16]
        src_se = self._embedding(src22[:8], "src_" + src_key)
        tau = float(self.cfg["voice"]["clone"]["tau"])
        outs = []
        for q in src22:
            o = out_dir / (q.stem.replace("_22k", "") + "_cloned.wav")
            try:
                conv.convert(audio_src_path=str(q), src_se=src_se, tgt_se=tgt_se, output_path=str(o), tau=tau)
            except Exception as exc:
                raise CloneError(f"OpenVoice conversion failed: {exc}") from exc
            outs.append(o)
        return outs


def get_cloner(cfg: dict) -> VoiceCloner | None:
    """Tone-colour converter used for engines that cannot clone by themselves
    (also the fallback when OmniVoice is unavailable)."""
    if cfg["voice"]["clone"]["engine"] in ("openvoice", "omnivoice"):
        c = OpenVoiceCloner(cfg)
        return c if c.is_available() else None
    return None


def resolve_reference(cfg: dict, repo_root: Path) -> Path | None:
    """The reference voice file, if cloning is enabled and the file exists."""
    clone = cfg["voice"]["clone"]
    if clone["enabled"] is False or clone["enabled"] == "false":
        return None
    raw = clone["reference_path"]
    candidates = [repo_root / raw]
    stem = (repo_root / raw).with_suffix("")
    candidates += [stem.with_suffix(ext) for ext in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".webm", ".aac")]
    for c in candidates:
        try:
            c.resolve().relative_to(repo_root.resolve())
        except ValueError:
            continue
        if c.is_file() and c.stat().st_size > 0:
            return c
    if clone["enabled"] is True:
        raise CloneError(f"voice.clone.enabled is true but no reference voice was found at {raw}", Status.PERMANENT_FAILURE)
    return None


def apply_clone(parts: list[Path], reference: Path, cfg: dict, work: Path, cloner: VoiceCloner | None, log=print) -> tuple[list[Path], dict]:
    """Convert per-sentence parts to the reference voice. Never fails the job:
    on any problem the original parts are returned and the reason recorded."""
    info = {"requested": True, "reference": str(reference), "applied": False}
    if cloner is None:
        info["reason"] = "voice cloning engine (OpenVoice) is not installed"
        return parts, info
    info.update(cloner.describe())
    try:
        outs = cloner.convert_parts(parts, reference, work)
    except StageError as exc:
        info["reason"] = str(exc)
        log(f"  voice cloning skipped: {exc}")
        return parts, info
    # Conversion must keep timing: reject outputs whose duration drifted.
    limit = float(cfg["voice"]["clone"]["max_duration_change"])
    for src, dst in zip(parts, outs):
        a, b = probe(src).duration, probe(dst).duration
        if a > 0 and abs(b - a) / a > limit:
            info["reason"] = f"converted audio duration changed by {abs(b - a) / a:.1%} (limit {limit:.0%}); kept original voice"
            return parts, info
    info["applied"] = True
    return outs, info

