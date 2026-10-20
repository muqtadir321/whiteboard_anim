"""Quality assurance for rendered videos: file, visual and semantic checks.

A video is only accepted when every critical check passes. Checks are
measurements against configured thresholds; no subjective score is invented.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

import numpy as np

from .audio_processor import volume_stats
from .illustration_composer import DESIGN_H, DESIGN_W, DRAW_REGION, SAFE_MARGIN, ComposedScene, _area, _overlap


@dataclass
class Check:
    name: str
    passed: bool
    critical: bool
    value: object = None
    threshold: object = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "critical": self.critical, "value": self.value, "threshold": self.threshold, "detail": self.detail}


@dataclass
class QualityReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, *args, **kw) -> Check:
        c = Check(*args, **kw)
        self.checks.append(c)
        return c

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.critical)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {"passed": self.passed, "critical_failures": [c.name for c in self.checks if c.critical and not c.passed],
                "warnings": [c.name for c in self.checks if not c.critical and not c.passed], "checks": [c.to_dict() for c in self.checks]}


def ffprobe_json(path: str) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {}
    return json.loads(proc.stdout or "{}")


def _fps(rate: str) -> float:
    try:
        n, d = rate.split("/")
        return float(n) / float(d)
    except Exception:
        return 0.0


def sample_frame(path: str, t: float, width: int, height: int) -> np.ndarray | None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True, timeout=120)
    if proc.returncode != 0 or len(proc.stdout) < width * height:
        return None
    return np.frombuffer(proc.stdout[: width * height], dtype=np.uint8).reshape(height, width)


def check_file(report: QualityReport, path: str, cfg: dict, expected_duration: float) -> dict:
    v = cfg["video"]
    q = cfg["quality"]
    import os

    exists = os.path.exists(path) and os.path.getsize(path) > 0
    report.add("file_exists", exists, True, os.path.getsize(path) if exists else 0, "> 0 bytes")
    if not exists:
        return {}
    meta = ffprobe_json(path)
    streams = meta.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    au = next((s for s in streams if s.get("codec_type") == "audio"), None)
    report.add("container_readable", bool(meta), True, meta.get("format", {}).get("format_name"))
    report.add("video_stream", vs is not None, True, vs.get("codec_name") if vs else None, "h264")
    report.add("audio_stream", au is not None, True, au.get("codec_name") if au else None, "aac")
    if vs:
        report.add("resolution", (vs.get("width"), vs.get("height")) == (v["width"], v["height"]), True, f"{vs.get('width')}x{vs.get('height')}", f"{v['width']}x{v['height']}")
        fps = _fps(vs.get("avg_frame_rate") or vs.get("r_frame_rate", "0/1"))
        report.add("frame_rate", abs(fps - v["fps"]) < 0.05, True, round(fps, 3), v["fps"])
        report.add("pixel_format", vs.get("pix_fmt") == "yuv420p", True, vs.get("pix_fmt"), "yuv420p")
        report.add("video_codec", vs.get("codec_name") == "h264", True, vs.get("codec_name"), "h264")
    if au:
        report.add("audio_sample_rate", int(au.get("sample_rate", 0)) == v["audio_sample_rate"], True, au.get("sample_rate"), v["audio_sample_rate"])
    duration = float(meta.get("format", {}).get("duration", 0) or 0)
    report.add("duration_matches_audio", abs(duration - expected_duration) <= q["maximum_duration_mismatch_seconds"], True, round(duration, 3),
               f"{expected_duration:.3f} ± {q['maximum_duration_mismatch_seconds']}")
    limit = v["maximum_duration_seconds"]
    report.add("duration_limit", duration <= limit + 0.01 or v["allow_longer_than_maximum"], True, round(duration, 3), f"<= {limit}s"
               + (" (allow_longer_than_maximum)" if v["allow_longer_than_maximum"] else ""))
    peak, mean = volume_stats(path)
    report.add("audio_not_silent", peak > q["minimum_audio_peak_db"], True, peak, f"> {q['minimum_audio_peak_db']} dB")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True, text=True, timeout=cfg["automation"]["subprocess_timeout_seconds"])
    errors = [line for line in proc.stderr.splitlines() if line.strip()]
    report.add("decodes_cleanly", proc.returncode == 0 and not errors, True, len(errors), 0, "; ".join(errors[:3]))
    return {"duration": duration, "width": vs.get("width") if vs else None, "height": vs.get("height") if vs else None}


def check_frames(report: QualityReport, path: str, cfg: dict, plan: dict, timelines: list) -> list[dict]:
    """Sample each scene after its drawing finished: the board must not be blank."""
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    sx = w / DESIGN_W
    x0, y0, x1, y1 = (int(DRAW_REGION[0] * sx), int(DRAW_REGION[1] * sx), int(DRAW_REGION[2] * sx), int(DRAW_REGION[3] * sx))
    samples = []
    blank = 0
    for tl in timelines:
        t = min(tl.end - 0.05, max(tl.transition_end + 0.1, tl.drawing_end + 0.05))
        frame = sample_frame(path, t, w, h)
        if frame is None:
            samples.append({"scene_id": tl.scene_id, "time": round(t, 2), "ink_ratio": None})
            blank += 1
            continue
        region = frame[y0:y1, x0:x1]
        ink = float((region < 200).mean())
        samples.append({"scene_id": tl.scene_id, "time": round(t, 2), "ink_ratio": round(ink, 4)})
        if ink < 0.002:
            blank += 1
    ratio = blank / max(1, len(timelines))
    report.add("no_blank_scenes", ratio <= cfg["quality"]["maximum_blank_frame_ratio"], True, round(ratio, 3), f"<= {cfg['quality']['maximum_blank_frame_ratio']}")
    return samples


def check_visuals(report: QualityReport, composed: list[ComposedScene], plan: dict, render_stats, cfg: dict, caption_missing: set[str]) -> None:
    q = cfg["quality"]
    outside = []
    overlaps = []
    text_mismatch = []
    missing_glyphs = set(caption_missing)
    empty = []
    for cs, scene in zip(composed, plan["scenes"]):
        if not cs.items:
            empty.append(cs.scene_id)
        planned_texts = {a["text"] for a in scene["visual_actions"] if a["action"] == "write"}
        rendered_texts = {t.text for it in cs.items for t in it.texts}
        for pt in planned_texts:
            if pt not in rendered_texts:
                text_mismatch.append(f"{cs.scene_id}:{pt}")
        missing_glyphs.update(cs.missing_glyphs)
        drawn = [it for it in cs.items if it.action in ("draw", "duplicate", "write")]
        for it in cs.items:
            bx0, by0, bx1, by1 = it.bbox
            if bx0 < SAFE_MARGIN - 1 or by0 < SAFE_MARGIN - 1 or bx1 > DESIGN_W - SAFE_MARGIN + 1 or by1 > DRAW_REGION[3] + 30:
                outside.append(f"{cs.scene_id}/{it.id}")
        for i, a in enumerate(drawn):
            for b in drawn[i + 1:]:
                small = min(_area(a.bbox), _area(b.bbox)) or 1
                held = a.held or b.held
                if _overlap(a.bbox, b.bbox) > 0.35 * small and not held:
                    overlaps.append(f"{cs.scene_id}:{a.id}/{b.id}")
    report.add("every_scene_has_visuals", not empty, True, empty)
    report.add("drawings_inside_safe_area", not outside, True, outside[:10])
    report.add("planned_text_rendered", not text_mismatch, True, text_mismatch[:10])
    report.add("glyph_coverage", len(missing_glyphs) <= q["maximum_unsupported_characters"], True, sorted(missing_glyphs), f"<= {q['maximum_unsupported_characters']}")
    report.add("caption_overflow", render_stats.caption_overflows <= q["maximum_caption_overflow"], True, render_stats.caption_overflows, f"<= {q['maximum_caption_overflow']}")
    report.add("cursor_inside_drawing_region", render_stats.cursor_out_of_bounds == 0, False, render_stats.cursor_out_of_bounds, 0)
    report.add("element_overlap", len(overlaps) == 0, False, overlaps[:10], 0, "overlaps larger than 35% of the smaller element")


def check_semantics(report: QualityReport, plan: dict, cfg: dict) -> None:
    q = cfg["quality"]
    problems = [f"{s['scene_id']}:{p}" for s in plan["scenes"] for p in s.get("validation_problems", [])]
    report.add("scene_plan_validation", len(problems) <= q["maximum_plan_validation_errors"], True, problems[:10], f"<= {q['maximum_plan_validation_errors']}")
    ok_scenes = [s for s in plan["scenes"] if not s.get("validation_problems")]
    report.add("minimum_successful_scenes", len(ok_scenes) >= q["minimum_successful_scenes"], True, len(ok_scenes), f">= {q['minimum_successful_scenes']}")
    # Numbers in the narration must appear in the scene's visuals.
    lost = []
    for s in plan["scenes"]:
        texts = " ".join(json.dumps(a, ensure_ascii=False) for a in s["visual_actions"])
        for qd in s.get("quantities", []):
            if qd["display"] not in texts:
                lost.append(f"{s['scene_id']}:{qd['display']}")
    report.add("numbers_preserved", not lost, True, lost[:10])
    fallback = [s["scene_id"] for s in plan["scenes"] if s.get("fallback")]
    report.add("topic_specific_scenes", len(fallback) < len(plan["scenes"]), True, f"{len(plan['scenes']) - len(fallback)}/{len(plan['scenes'])} topic-specific",
               "at least one", "text-only fallback scenes: " + ", ".join(fallback))
    dup = []
    for a, b in zip(plan["scenes"], plan["scenes"][1:]):
        sa = (a["scene_type"], tuple(sorted((x.get("object") or "", x["position"]) for x in a["visual_actions"])))
        sb = (b["scene_type"], tuple(sorted((x.get("object") or "", x["position"]) for x in b["visual_actions"])))
        if sa == sb:
            dup.append(f"{a['scene_id']}={b['scene_id']}")
    report.add("adjacent_scenes_distinct", not dup, False, dup)
    timing_ok = all(s["end_seconds"] > s["start_seconds"] for s in plan["scenes"])
    report.add("scene_timing_valid", timing_ok, True)
    gaps = [round(b["start_seconds"] - a["end_seconds"], 3) for a, b in zip(plan["scenes"], plan["scenes"][1:]) if abs(b["start_seconds"] - a["end_seconds"]) > 0.05]
    report.add("scenes_cover_narration", not gaps, True, gaps[:5])


def check_explainer(report: QualityReport, layout, cfg: dict) -> None:
    """Explainer style: the narration is written on the board and evidence is real and sourced."""
    q = cfg["quality"]
    def shown(sc):  # words, a doodle, or an evidence stage on screen at the end of the scene
        t = sc.end - 0.05
        return sc.words or sc.sprites or any(st.t_in <= t < st.t_out for st in layout.stages)

    empty = [sc.scene_id for sc in layout.scenes if not shown(sc)]
    report.add("every_scene_has_visuals", not empty, True, empty)
    outside = [f"{sc.scene_id}:{w.text}" for sc in layout.scenes for w in sc.words
               if w.bbox[0] < SAFE_MARGIN - 1 or w.bbox[2] > DESIGN_W - SAFE_MARGIN + 1 or w.bbox[1] < SAFE_MARGIN - 1]
    report.add("drawings_inside_safe_area", not outside, True, outside[:10])
    report.add("glyph_coverage", len(layout.missing_glyphs) <= q["maximum_unsupported_characters"], True, sorted(layout.missing_glyphs),
               f"<= {q['maximum_unsupported_characters']}")
    unsourced = [st.id for st in layout.stages if st.kind in ("article", "chart") and not st.source_url]
    report.add("evidence_has_source", not unsourced, True, unsourced)
    report.add("evidence_cues_resolved", not layout.warnings, False, layout.warnings[:10], 0)
    h = layout.highlights
    report.add("highlights_found_on_page", h["matched"] == h["requested"], False, f"{h['matched']}/{h['requested']}", "all")


def scene_signature(scene: dict) -> str:
    return re.sub(r"\s+", " ", scene["scene_type"])
