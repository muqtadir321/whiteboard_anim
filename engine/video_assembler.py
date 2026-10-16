"""FFmpeg assembly: raw rendered frames + prepared audio -> H.264/AAC MP4."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .results import StageError, Status


def ffmpeg_command(width: int, height: int, fps: int, audio_path: str, out_path: str, duration: float, cfg: dict) -> list[str]:
    if width % 2 or height % 2:
        raise ValueError("H.264 with yuv420p needs even dimensions")
    v = cfg["video"]
    render = cfg.get("render", {})
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", v["codec"], "-preset", render.get("x264_preset", "veryfast"), "-crf", str(render.get("crf", 20)),
        "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
        "-c:a", v["audio_codec"], "-b:a", v["audio_bitrate"], "-ar", str(v["audio_sample_rate"]),
        "-t", f"{duration:.3f}", "-movflags", "+faststart",
        str(out_path),
    ]


class FrameSink:
    """Context manager that streams frames into an FFmpeg process."""

    def __init__(self, cmd: list[str], timeout: float):
        self.cmd = cmd
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        return self

    def write(self, data) -> None:
        try:
            self.proc.stdin.write(data)
        except BrokenPipeError as exc:
            err = self.proc.stderr.read().decode(errors="replace")[-800:]
            raise StageError(f"FFmpeg stopped accepting frames: {err}", Status.PERMANENT_FAILURE) from exc

    def __exit__(self, exc_type, exc, tb):
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        try:
            self.proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise StageError("FFmpeg encoding timed out", Status.RETRYABLE_FAILURE)
        err = self.proc.stderr.read().decode(errors="replace")
        if exc_type is None and self.proc.returncode != 0:
            raise StageError(f"FFmpeg encoding failed: {err[-800:]}", Status.PERMANENT_FAILURE)
        return False


def assemble(renderer, render_scenes, audio_path: str, out_path: str | Path, duration: float, cfg: dict, sample_times=None):
    cmd = ffmpeg_command(renderer.width, renderer.height, renderer.fps, audio_path, str(out_path), duration, cfg)
    with FrameSink(cmd, cfg["automation"]["subprocess_timeout_seconds"]) as sink:
        stats = renderer.render(render_scenes, duration, sink.write, sample_times)
    return stats
