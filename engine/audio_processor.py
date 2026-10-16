"""Audio validation and preparation with FFmpeg.

Supplied narration keeps its natural speed: processing is limited to format
conversion, loudness normalisation and trimming of long leading/trailing
silence (never inside speech).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .results import StageError, Status


class AudioError(StageError):
    pass


@dataclass
class AudioInfo:
    path: str
    duration: float
    sample_rate: int
    channels: int
    codec: str
    max_volume_db: float | None = None
    mean_volume_db: float | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise AudioError(f"{tool} is not installed", Status.PERMANENT_FAILURE)


def run(cmd: list[str], timeout: float = 600, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess without a shell (no injection), with a timeout."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"Command timed out after {timeout}s: {cmd[0]}", Status.RETRYABLE_FAILURE) from exc
    if check and proc.returncode != 0:
        raise AudioError(f"{cmd[0]} failed: {proc.stderr.strip()[-600:]}")
    return proc


def probe(path: str | Path) -> AudioInfo:
    require_ffmpeg()
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], timeout=60, check=False)
    if proc.returncode != 0:
        raise AudioError(f"Unreadable audio file {path}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioError(f"No audio stream in {path}")
    st = streams[0]
    duration = float(data.get("format", {}).get("duration") or st.get("duration") or 0)
    return AudioInfo(str(path), duration, int(st.get("sample_rate", 0) or 0), int(st.get("channels", 0) or 0), st.get("codec_name", "?"))


def volume_stats(path: str | Path) -> tuple[float, float]:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], timeout=300, check=False)
    mx = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)
    mn = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)

    def val(m):
        if not m:
            return -200.0
        return -200.0 if m.group(1) == "-inf" else float(m.group(1))

    return val(mx), val(mn)


def validate_audio(path: str | Path, min_peak_db: float = -45.0) -> AudioInfo:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        raise AudioError(f"Audio file missing or empty: {path}")
    info = probe(p)
    if info.duration < 0.3:
        raise AudioError(f"Audio too short ({info.duration:.2f}s): {path}")
    info.max_volume_db, info.mean_volume_db = volume_stats(p)
    if info.max_volume_db < min_peak_db:
        raise AudioError(f"Audio appears silent (peak {info.max_volume_db} dB): {path}")
    return info


def normalize(src: str | Path, dst: str | Path, sample_rate: int = 48000, trim_silence: bool = True, timeout: float = 900) -> AudioInfo:
    """Convert to mono PCM WAV, loudness-normalise, trim only long edge silence."""
    filters = []
    if trim_silence:
        # Trim only long leading/trailing silence (keeping 0.4 s), found with silencedetect at a
        # low threshold so speech is never cut. Silence inside the narration is untouched.
        info = probe(src)
        quiet = detect_silences(src, noise_db=-50.0, min_duration=0.6)
        start, end = 0.0, info.duration
        if quiet and quiet[0][0] <= 0.05:
            start = max(0.0, quiet[0][1] - 0.4)
        if quiet and quiet[-1][1] >= info.duration - 0.05 and quiet[-1][0] > start:
            end = min(info.duration, quiet[-1][0] + 0.4)
        if end - start > 0.3 and (start > 0 or end < info.duration):
            filters.append(f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS")
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    filters.append(f"aresample={sample_rate}")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", ",".join(filters), "-ac", "1", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", str(dst)], timeout=timeout)
    return probe(dst)


def detect_silences(path: str | Path, noise_db: float = -35.0, min_duration: float = 0.22) -> list[tuple[float, float]]:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"],
               timeout=300, check=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None:
            out.append((max(0.0, s), e))
    return out


def concat_with_pauses(parts: list[str | Path], dst: str | Path, pause: float, sample_rate: int = 48000) -> list[tuple[float, float]]:
    """Concatenate WAV parts with fixed pauses; returns exact (start, end) per part."""
    inputs: list[str] = []
    filters = []
    spans = []
    t = 0.0
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        d = probe(p).duration
        spans.append((t, t + d))
        t += d + pause
        filters.append(f"[{i}:a]aresample={sample_rate},aformat=channel_layouts=mono,apad=pad_dur={pause}[p{i}]")
    chain = "".join(f"[p{i}]" for i in range(len(parts)))
    graph = ";".join(filters) + f";{chain}concat=n={len(parts)}:v=0:a=1[out]"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", str(dst)], timeout=900)
    return spans


def build_final_track(narration: str | Path, dst: str | Path, intro: float, outro: float, sample_rate: int = 48000,
                      music: str | Path | None = None, music_volume: float = 0.08) -> AudioInfo:
    """Add intro/outro silence and optional ducked background music; stereo output."""
    delay = int(round(intro * 1000))
    base = f"[0:a]adelay={delay}|{delay},apad=pad_dur={outro},aformat=channel_layouts=stereo,aresample={sample_rate}"
    if music:
        graph = (f"{base},asplit=2[voice][side];"
                 f"[1:a]aloop=loop=-1:size=2e9,volume={music_volume},aformat=channel_layouts=stereo,aresample={sample_rate}[bg];"
                 f"[bg][side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
                 f"[voice][duck]amix=inputs=2:duration=first:normalize=0[out]")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(narration), "-i", str(music), "-filter_complex", graph, "-map", "[out]"]
    else:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(narration), "-filter_complex", base + "[out]", "-map", "[out]"]
    run(cmd + ["-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)], timeout=900)
    return probe(dst)
