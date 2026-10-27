"""Run-level reports: generation report, quality report, licence manifest and
GitHub Actions job summary."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from importlib import metadata
from pathlib import Path

from . import __version__
from .results import Status

FONT_LICENSES = [
    {"asset_id": "font-kalam", "local_file": "assets/fonts/Kalam-Regular.ttf, Kalam-Bold.ttf", "source": "https://github.com/google/fonts/tree/main/ofl/kalam",
     "license": "SIL Open Font License 1.1", "attribution": "Copyright Indian Type Foundry", "license_file": "assets/fonts/OFL-Kalam.txt", "redistribution": True},
    {"asset_id": "font-noto-nastaliq-urdu", "local_file": "assets/fonts/NotoNastaliqUrdu.ttf", "source": "https://github.com/google/fonts/tree/main/ofl/notonastaliqurdu",
     "license": "SIL Open Font License 1.1", "attribution": "Copyright The Noto Project Authors", "license_file": "assets/fonts/OFL-NotoNastaliqUrdu.txt", "redistribution": True},
    {"asset_id": "font-noto-naskh-arabic", "local_file": "assets/fonts/NotoNaskhArabic.ttf", "source": "https://github.com/google/fonts/tree/main/ofl/notonaskharabic",
     "license": "SIL Open Font License 1.1", "attribution": "Copyright The Noto Project Authors", "license_file": "assets/fonts/OFL-NotoNaskhArabic.txt", "redistribution": True},
]
GENERATED_ASSETS = [
    {"asset_id": "illustrations", "generation": "programmatic SVG (engine/character_generator.py, object_generator.py, chart_generator.py, diagram_generator.py)",
     "license": "same as this repository", "redistribution": True},
    {"asset_id": "hand-cursor", "generation": "programmatic cairo drawing (engine/hand_cursor.py)", "license": "same as this repository", "redistribution": True},
    {"asset_id": "mascot", "generation": "programmatic character 'Mo' (engine/explainer/mascot.py)", "license": "same as this repository", "redistribution": True},
    {"asset_id": "sound-effects", "generation": "synthesised with numpy (engine/audio_fx.py): pops, whooshes, optional pad",
     "license": "same as this repository", "redistribution": True},
]
PACKAGES = ["numpy", "pillow", "pycairo", "uharfbuzz", "fonttools", "jsonschema", "PyYAML", "faster-whisper", "piper-tts", "transformers", "torch", "edge-tts"]


def _pkg_info(name: str) -> dict | None:
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return None
    lic = md.get("License-Expression") or md.get("License") or ""
    if not lic or len(lic) > 80:
        classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License")]
        lic = ", ".join(classifiers) or (lic[:80] if lic else "see package metadata")
    return {"package": name, "version": md.get("Version"), "license": lic}


def _tool_version(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.splitlines()[0]
    except Exception:
        return None


def license_manifest(results) -> dict:
    voices = []
    for r in results:
        v = r.metadata.get("voice") if r.metadata else None
        if v:
            voices.append({"language": r.key, **{k: v.get(k) for k in ("provider", "license", "commercial_use", "online", "conversion")}})
    noncommercial = [v for v in voices if str(v.get("commercial_use", "")).startswith("no")]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fonts": FONT_LICENSES,
        "generated_assets": GENERATED_ASSETS,
        "python_packages": [p for p in (_pkg_info(n) for n in PACKAGES) if p],
        "system_tools": {"ffmpeg": {"version": _tool_version(["ffmpeg", "-version"]), "license": "LGPL-2.1+/GPL-2.0+ depending on build (Ubuntu build includes GPL x264)"},
                         "espeak-ng": {"version": _tool_version(["espeak-ng", "--version"]), "license": "GPL-3.0-or-later"}},
        "voices_used": voices,
        "background_music": ("the first track in input/music/ when present (video.background_music_path: auto); its licence is "
                             "yours to check and should be recorded here; 'generated' uses the synthesised pad"),
        "commercial_use_notice": ("Some videos used NON-COMMERCIAL voice models: " + ", ".join(v["language"] for v in noncommercial))
        if noncommercial else "No non-commercial voice models were used in this run. Verify all licences yourself before commercial use.",
    }


def generation_report(results, run_info: dict) -> dict:
    return {
        "engine_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "run": run_info,
        "summary": {s.value: [r.key for r in results if r.status == s] for s in Status},
        "languages": [r.to_dict() for r in results],
    }


def overall_status(results, requested: list[str] | None) -> tuple[str, int]:
    ok = [r for r in results if r.status.ok]
    failed = [r for r in results if r.status in (Status.PERMANENT_FAILURE, Status.RETRYABLE_FAILURE)]
    if ok and not failed:
        return "success", 0
    if ok and failed:
        return "partial_success", 0
    if failed:
        return "failure", 1
    return "nothing_to_do", 0


def step_summary(results, overall: str, artifact_name: str) -> str:
    icon = {"SUCCESS": "✅", "PARTIAL_SUCCESS": "🟡", "SKIPPED": "⏭️", "RETRYABLE_FAILURE": "🔁", "PERMANENT_FAILURE": "❌"}
    lines = ["# Whiteboard video generation", "", f"**Overall result:** `{overall}`", ""]
    lines += ["| Language | Status | Details | Files |", "|---|---|---|---|"]
    for r in results:
        files = ", ".join(f"`{Path(p).name}`" for p in r.outputs) if r.outputs else "—"
        msg = (r.message or "").replace("|", "\\|").replace("\n", " ")[:220]
        lines.append(f"| {r.key} | {icon.get(r.status.value, '')} {r.status.value} | {msg} | {files} |")
    lines += ["", "## Download", ""]
    if any(r.status.ok for r in results):
        lines += [f"Open this run's **Summary** page, scroll to **Artifacts** and download **`{artifact_name}`**. "
                  "Each language folder contains `video.mp4` (1080×1920 vertical, ready for YouTube Shorts), captions (`.srt`/`.vtt`), "
                  "the transcript, the scene plan and a thumbnail. Reports are in `reports/`."]
    else:
        lines += ["No video was produced in this run. Add a script to `input/scripts/<language>.txt` and/or a voice-over to "
                  "`input/audio/<language>.mp3`, then run the workflow again. See `reports/generation_report.json` for details."]
    warn = [w for r in results for w in (r.metadata or {}).get("warnings", [])]
    if warn:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warn[:30]]
    return "\n".join(lines) + "\n"


def write_reports(out_root: Path, results, run_info: dict, artifact_name: str, requested=None) -> tuple[str, int]:
    rep = out_root / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    overall, code = overall_status(results, requested)
    gen = generation_report(results, run_info)
    gen["overall"] = overall
    (rep / "generation_report.json").write_text(json.dumps(gen, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (rep / "quality_report.json").write_text(json.dumps({r.key: r.quality for r in results if r.quality}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (rep / "asset_licenses.json").write_text(json.dumps(license_manifest(results), ensure_ascii=False, indent=2), encoding="utf-8")
    summary = step_summary(results, overall, artifact_name)
    (rep / "summary.md").write_text(summary, encoding="utf-8")
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return overall, code
