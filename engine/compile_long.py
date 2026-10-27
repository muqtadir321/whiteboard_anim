"""Long-form compiler: several finished Shorts -> one 16:9 video with chapters.

    python -m engine.cli compile --videos output/a/english output/b/english ... --title "Money Basics" --out long/money.mp4

Long videos earn far more per view than Shorts, and a weekly compilation reuses the week's work.
Each Short (9:16) sits in the middle of a 1920x1080 whiteboard; the side panels show the series
title, the chapter list with the current chapter marked, and Mo. A 1.5 s title card opens every
chapter. Loudness is normalised per segment (YouTube plays at about -14 LUFS), segments are
concatenated without re-encoding, and the chapter timestamps go into the description of a
publishing kit next to the video.
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cairo

from .explainer.layout import element_surface
from .explainer.mascot import mascot
from .text_shaper import shape_line

W, H, FPS = 1920, 1080, 30
CARD_SECONDS = 1.5
PAPER = (0.985, 0.98, 0.965)
INK = (0.12, 0.12, 0.12)
ACCENT = (0.14, 0.42, 0.81)
MARKER = (1.0, 0.88, 0.3)


class CompileError(RuntimeError):
    pass


@dataclass
class Segment:
    video: Path
    title: str
    script: str = ""
    duration: float = 0.0


def _run(cmd: list[str], timeout: float = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise CompileError(f"{cmd[0]} failed: {proc.stderr.strip()[-600:]}")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, timeout=60)
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise CompileError(f"cannot read the duration of {path}") from exc


def resolve(item: str | Path, title: str = "") -> Segment:
    """A job folder (output/<job>/english) or an mp4; the chapter title is the given one, the job's first
    publish title, or the file name."""
    p = Path(item)
    script = ""
    if p.is_dir():
        job = p
        p = job / "video.mp4"
        titles = job / "publish" / "titles.txt"
        if not title and titles.exists():
            title = titles.read_text(encoding="utf-8").splitlines()[0].strip()
        meta = job / "metadata.json"
        if meta.exists():
            sp = (json.loads(meta.read_text(encoding="utf-8")).get("inputs") or {}).get("script_path")
            if sp and Path(sp).exists():
                script = Path(sp).read_text(encoding="utf-8")
    if not p.exists():
        raise CompileError(f"video not found: {p}")
    return Segment(p, title or p.stem.replace("_", " "), script)


def _text(ctx: cairo.Context, text: str, size: float, x: float, y: float, color, max_width: float, align: str = "left") -> float:
    """Handwritten text (the engine's font, shaped like the board text). Returns the width drawn."""
    line = shape_line(text, size, "en", True)
    if line.width > max_width:
        size *= max_width / line.width
        line = shape_line(text, size, "en", True)
    x0 = x - line.width / 2 if align == "center" else x
    ctx.set_source_rgb(*color)
    for g in line.glyphs:
        for c in g.contours():
            if len(c) > 1:
                ctx.move_to(x0 + c[0][0], y + c[0][1])
                for px, py in c[1:]:
                    ctx.line_to(x0 + px, y + py)
                ctx.close_path()
    ctx.fill()
    return line.width


def _mascot(ctx: cairo.Context, mood: str, box: tuple[float, float, float, float], cfg: dict) -> None:
    from .illustration_composer import Style

    surf, _ = element_surface(mascot(mood), box[3] - box[1], Style.from_config(cfg), "en")
    k = min((box[2] - box[0]) / surf.get_width(), (box[3] - box[1]) / surf.get_height())
    ctx.save()
    ctx.translate(box[0] + ((box[2] - box[0]) - surf.get_width() * k) / 2, box[1] + ((box[3] - box[1]) - surf.get_height() * k) / 2)
    ctx.scale(k, k)
    ctx.set_source_surface(surf, 0, 0)
    ctx.paint()
    ctx.restore()


def _paper(ctx: cairo.Context) -> None:
    ctx.set_source_rgb(*PAPER)
    ctx.paint()
    rng = random.Random(3)  # faint paper speckle so the side panels are not flat
    ctx.set_source_rgba(0, 0, 0, 0.035)
    for _ in range(900):
        ctx.arc(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.6, 1.6), 0, 6.283)
        ctx.fill()


def background(path: Path, series: str, chapters: list[str], current: int, video_w: int, cfg: dict) -> None:
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    ctx = cairo.Context(surf)
    _paper(ctx)
    left = (W - video_w) / 2
    _text(ctx, series, 58, 50, 120, INK, left - 90)
    y = 210
    for i, ch in enumerate(chapters):
        size = 34 if len(chapters) <= 10 else 26
        if i == current:
            ctx.set_source_rgb(*MARKER)
            ctx.rectangle(40, y - size * 0.95, left - 80, size * 1.35)
            ctx.fill()
        _text(ctx, f"{i + 1}. {ch}", size, 50, y, INK if i == current else (0.45, 0.45, 0.45), left - 100)
        y += size * 1.6
    right = left + video_w
    _mascot(ctx, "talk", (right + 60, 300, W - 60, 900), cfg)
    _text(ctx, f"Chapter {current + 1} of {len(chapters)}", 40, (right + W) / 2, 980, ACCENT, W - right - 80, "center")
    surf.write_to_png(str(path))


def card(path: Path, series: str, index: int, title: str, cfg: dict) -> None:
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    ctx = cairo.Context(surf)
    _paper(ctx)
    _text(ctx, series, 44, W / 2, 230, (0.45, 0.45, 0.45), W - 400, "center")
    _text(ctx, f"Chapter {index + 1}", 64, W / 2, 400, ACCENT, W - 400, "center")
    _text(ctx, title, 92, W / 2, 560, INK, W - 300, "center")
    _mascot(ctx, "hook", (W / 2 - 160, 640, W / 2 + 160, 1040), cfg)
    surf.write_to_png(str(path))


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def compile_long(items: list[str | Path], out: str | Path, series: str, chapter_titles: list[str] | None = None,
                 cfg: dict | None = None, log=print) -> dict:
    from .config import load_config

    cfg = cfg or load_config()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / f".{out.stem}_parts"
    work.mkdir(exist_ok=True)
    titles = chapter_titles or []
    segs = [resolve(it, titles[i] if i < len(titles) else "") for i, it in enumerate(items)]
    if not segs:
        raise CompileError("no videos given")
    names = [s.title for s in segs]
    video_w = round(H * 9 / 16 / 2) * 2  # 608: a 9:16 Short at full height
    parts: list[Path] = []
    chapters: list[str] = []
    t = 0.0
    audio = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    for i, seg in enumerate(segs):
        seg.duration = probe_duration(seg.video)
        log(f"chapter {i + 1}: {seg.title} ({seg.duration:.1f} s)")
        cpng, bpng = work / f"card_{i}.png", work / f"bg_{i}.png"
        card(cpng, series, i, seg.title, cfg)
        background(bpng, series, names, i, video_w, cfg)
        cmp4, smp4 = work / f"card_{i}.mp4", work / f"seg_{i}.mp4"
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(cpng),
              "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-t", f"{CARD_SECONDS}",
              *video, *audio, "-shortest", str(cmp4)])
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(bpng), "-i", str(seg.video),
              "-filter_complex", f"[1:v]scale={video_w}:{H},setsar=1[v];[0:v][v]overlay=(W-w)/2:0:shortest=1,format=yuv420p[vo];"
                                 "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]",
              "-map", "[vo]", "-map", "[ao]", *video, *audio, "-t", f"{seg.duration:.3f}", str(smp4)])
        chapters.append(f"{stamp(t)} {seg.title}")
        t += CARD_SECONDS + seg.duration
        parts += [cmp4, smp4]
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
          "-movflags", "+faststart", str(out)])
    total = probe_duration(out)
    # publishing kit for the long video, chapters in the description
    from .publish import build_kit

    kit_dir = out.parent / out.stem
    kit_dir.mkdir(exist_ok=True)
    card(kit_dir / "thumbnail_source.png", series, 0, f"{len(segs)} money lessons", cfg)
    script = "\n".join(s.script for s in segs if s.script)
    build_kit(kit_dir, script, series, hook=series, video=str(out.resolve()), thumbnail=kit_dir / "thumbnail_source.png", chapters=chapters)
    (kit_dir / "chapters.txt").write_text("\n".join(chapters) + "\n", encoding="utf-8")
    summary = {"video": str(out), "seconds": round(total, 2), "chapters": chapters, "publish_kit": str(kit_dir / "publish"),
               "youtube_chapters_ok": len(chapters) >= 3 and all(s.duration + CARD_SECONDS >= 10 for s in segs)}
    (kit_dir / "compile.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
