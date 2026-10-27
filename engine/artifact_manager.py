"""Artifact folder layout, README and clean-up of temporary files."""

from __future__ import annotations

import shutil
from pathlib import Path

from .results import Status

README = """Whiteboard video output
=======================

Each language folder that was processed contains:

  video.mp4           Final vertical video (H.264/AAC, 9:16) - upload this to YouTube Shorts.
  thumbnail.png       A fully drawn scene, usable as a thumbnail.
  captions.srt/.vtt   Subtitles synchronised with the narration.
  transcript.txt      The narration text used for captions and scene planning.
  scene_plan.json     The semantic scene plan (concepts, visual actions, timing).
  scenes/*.svg        The vector illustration of every scene.
  metadata.json       Voice, timing method, licences and warnings for this video.
  quality_report.json Every quality check and its measured value.
  publish/            Title options, description (with sources), tags, pinned comment,
                      Shorts thumbnail (the hook card) and upload.json for YouTube.

reports/ contains the run-level generation report, quality report and the
asset/dependency licence manifest.

Run status:
{status}
"""


def finalize(out_root: Path, results, tmp_root: Path | None = None, keep_tmp: bool = False) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in results:
        lines.append(f"  {r.key:<11} {r.status.value:<18} {r.message}")
        folder = out_root / r.key
        # Remove folders of languages that produced no validated video (no partial files are published).
        if folder.exists() and r.status not in (Status.SUCCESS, Status.PARTIAL_SUCCESS):
            failed_dir = out_root / "reports" / "failed" / r.key
            failed_dir.mkdir(parents=True, exist_ok=True)
            for name in ("quality_report.json", "scene_plan.json"):
                if (folder / name).exists():
                    shutil.copy(folder / name, failed_dir / name)
            shutil.rmtree(folder)
    (out_root / "README.txt").write_text(README.format(status="\n".join(lines)), encoding="utf-8")
    if tmp_root and not keep_tmp and tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
