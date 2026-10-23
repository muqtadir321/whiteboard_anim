"""Render Video: research package + narration -> separate English / Urdu videos and reports.

The package is checked (hashes), copied into a new job folder and rendered from the copy, so a
rerun never touches an earlier job and never repeats research. Per language:

  voice_mode provided_audio  the supplied voice-over (always wins when given)
             clone_voice     generated speech in a reference voice: needs the reference file AND
                             consent_confirmed; non-commercial models only with allow_restricted_models
             system_tts      local TTS; non-commercial models only with allow_restricted_models

One language failing never stops the other. Supplied audio and voice samples are read from
where they are and never copied into the job folder.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import LANGUAGE_CODES
from ..config import ConfigError, deep_merge, load_config
from ..input_discovery import AUDIO_EXTENSIONS, LanguageInputs
from ..results import Status
from . import claims as claims_mod
from .job import JobError, build_info, copy_package, create_job_dir, new_job_id, sha256_file, verify_package
from .ledger import Ledger

SUFFIX = {"english": "en", "urdu": "ur", "roman_urdu": "ur"}
MEDIA_EXT = set(AUDIO_EXTENSIONS) - {".mp4"}


class RenderError(ValueError):
    pass


@dataclass
class RenderInputs:
    research_dir: Path
    languages: str = "both"  # english | urdu | both
    audio: dict[str, Path] = field(default_factory=dict)  # "english" / "urdu"
    scripts: dict[str, Path] = field(default_factory=dict)  # narration text that replaces the package script
    voice_modes: dict[str, str] = field(default_factory=dict)  # provided_audio | clone_voice | system_tts
    voice_reference: Path | None = None
    voice_consent: bool = False
    quality: str = "final"  # draft | final
    subtitles: bool = False
    music: Path | None = None
    allow_restricted_models: bool = False
    config_path: str = ""
    pronunciations: Path | None = None  # YAML {written word: how the voice should say it}; captions keep the script


def _check_audio(p: Path) -> Path:
    p = Path(p)
    if not p.is_file() or p.stat().st_size == 0:
        raise RenderError(f"audio file not found or empty: {p}")
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        raise RenderError(f"unsupported audio type {p.suffix} (use {', '.join(sorted(AUDIO_EXTENSIONS))})")
    return p


def package_docs(job: Path, ledger: Ledger) -> list[claims_mod.Doc]:
    """The extracted texts and chart values stored in the package (for re-checking new narration)."""
    from .research import chart_doc

    docs = []
    for s in ledger.ordered():
        if s.extracted_text_path and (job / s.extracted_text_path).is_file():
            docs.append(claims_mod.Doc(s.source_id, (job / s.extracted_text_path).read_text(encoding="utf-8"), s.source_type, s.domain,
                                       s.published_at, s.duplicate_of))
    for f in sorted((job / "research" / "charts").glob("*.json")):
        ch = json.loads(f.read_text(encoding="utf-8"))
        docs.append(chart_doc(ch["source_id"], ch))
    return docs


def plan_languages(ri: RenderInputs, topic: Path) -> list[tuple[str, str]]:
    """[(requested, language key)]: "urdu" renders whichever form the package (or the new script) holds."""
    wanted = {"english": ["english"], "urdu": ["urdu"], "both": ["english", "urdu"]}.get(ri.languages)
    if wanted is None:
        raise RenderError("languages must be english, urdu or both")
    out = []
    for lang in wanted:
        if lang == "english":
            out.append(("english", "english"))
            continue
        if "urdu" in ri.scripts:
            from .research import urdu_form

            key, _ = urdu_form(Path(ri.scripts["urdu"]).read_text(encoding="utf-8"), "auto")
        else:
            key = "urdu" if (topic / "urdu.txt").exists() or not (topic / "roman_urdu.txt").exists() else "roman_urdu"
        out.append(("urdu", key))
    return out


def render_package(ri: RenderInputs, out_root: Path, log=print, tts_chain=None, transcriber=None, aligner="auto", cloner="auto") -> tuple[Path, list]:
    from ..pipeline import LanguageJob, LanguageResult

    lines: list[str] = []
    outer = log

    def log(msg: str) -> None:  # also kept as qa/render.log (diagnostics survive a failed render)
        lines.append(msg)
        outer(msg)

    pkg, problems = verify_package(Path(ri.research_dir))
    if problems:
        raise RenderError("research package failed validation: " + "; ".join(problems[:8]))
    job = create_job_dir(out_root, new_job_id((pkg.get("label") or pkg.get("topic", "video"))[:40] + " render"))
    copy_package(Path(ri.research_dir), job, pkg["files"])
    shutil.copy2(Path(ri.research_dir) / "package.json", job / "package.json")
    for d in ("storyboard", "audio", "video", "qa"):
        (job / d).mkdir(exist_ok=True)
    topic = job / "research" / "topic"
    ledger = Ledger.load(job / "research")
    claims_all = json.loads((job / "research" / "claims.json").read_text(encoding="utf-8")) if (job / "research" / "claims.json").exists() else []
    overrides = {"animation": {"style": "explainer"}, "video": {"quality": "fast" if ri.quality == "draft" else "standard",
                                                                "captions_enabled": bool(ri.subtitles),
                                                                "background_music_enabled": bool(ri.music),
                                                                "background_music_path": str(ri.music) if ri.music else ""},
                 "voice": {"allow_noncommercial_models": bool(ri.allow_restricted_models), "prefer_supplied_audio": True},
                 # the forced aligner (torchaudio MMS_FA) has CC-BY-NC weights: whisper or phrase timing unless allowed
                 "timing": {"forced_alignment": bool(ri.allow_restricted_models)}}
    try:
        base_cfg = load_config(ri.config_path or None, overrides)
    except ConfigError as exc:
        raise RenderError(str(exc)) from exc
    if ri.music and not Path(ri.music).is_file():
        raise RenderError(f"background music not found: {ri.music}")
    for pfile in [p for p in (topic / "pronunciations.yaml", ri.pronunciations) if p and Path(p).is_file()]:  # package first, yours last
        import yaml

        try:
            pron = yaml.safe_load(Path(pfile).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RenderError(f"pronunciations file unreadable: {exc}") from exc
        if not isinstance(pron, dict):
            raise RenderError("pronunciations file must map words to spoken forms")
        base_cfg["voice"]["pronunciations"] = {**base_cfg["voice"]["pronunciations"], **{str(k): str(v) for k, v in pron.items()}}
    research_info = json.loads((job / "research" / "research.json").read_text(encoding="utf-8")) if (job / "research" / "research.json").exists() else {}
    work = Path(tempfile.mkdtemp(prefix=f"render-{job.name}-"))  # private: audio copies and intermediate files
    results, statuses, warnings = [], {}, []
    try:
        for requested, key in plan_languages(ri, topic):
            code = LANGUAGE_CODES[key]
            sfx = SUFFIX[key]
            mode = ri.voice_modes.get(requested) or ("provided_audio" if requested in ri.audio else "system_tts")
            log(f"[{key}] voice mode: {mode}")
            cfg = deep_merge(base_cfg, {})
            if not ri.subtitles:
                cfg["video"]["captions_enabled"] = False
            try:
                audio = _check_audio(ri.audio[requested]) if requested in ri.audio else None
                if mode == "provided_audio" and audio is None:
                    raise RenderError("voice_mode provided_audio but no audio file was given")
                reference, clone_tool = None, None
                if mode == "clone_voice" and audio is None:
                    if not ri.voice_reference or not Path(ri.voice_reference).is_file():
                        raise RenderError("voice_mode clone_voice needs a reference recording (--voice-reference)")
                    if not (ri.voice_consent or cfg["voice"]["clone"]["consent_confirmed"]):
                        raise RenderError("voice_mode clone_voice needs consent: confirm the reference is your own voice or one you have "
                                          "written permission to use (--voice-consent true)")
                    reference = Path(ri.voice_reference)
                    cfg["voice"]["clone"]["enabled"] = True
                    if not ri.allow_restricted_models:
                        cfg["voice"]["clone"]["engine"] = "openvoice"  # MIT weights; OmniVoice weights are non-commercial
                    from ..voice_clone import get_cloner

                    clone_tool = get_cloner(cfg) if cloner == "auto" else cloner
                elif mode == "system_tts" or audio is not None:
                    cfg["voice"]["clone"]["enabled"] = False
                cfg["voice"]["generate_missing"] = audio is None
                script_src = ri.scripts.get(requested)
                if script_src:
                    from ..explainer import draft
                    from ..explainer.evidence import Evidence

                    text = Path(script_src).read_text(encoding="utf-8-sig")
                    ev = Evidence.load(topic / "evidence")
                    paras = {}
                    for a in json.loads((topic / "evidence" / "manifest.json").read_text(encoding="utf-8")).get("articles", []):
                        sp = ledger.get(a.get("source_id", ""))
                        if sp and sp.extracted_text_path and (job / sp.extracted_text_path).is_file():
                            paras[a["id"]] = (job / sp.extracted_text_path).read_text(encoding="utf-8").split("\n\n")
                    charts = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((job / "research" / "charts").glob("*.json"))]
                    tagged, _ = draft.auto_tag(text, code, ev.articles, paras, charts, bool(ev.news_image))
                    script_path = work / f"{key}.txt"
                    script_path.write_text(tagged, encoding="utf-8")
                    from ..script_analyzer import normalize_script_with_cues

                    cl = claims_mod.extract_claims(normalize_script_with_cues(tagged)[0], code, {"english": "EN", "urdu": "UR", "roman_urdu": "RU"}[key])
                    claims_mod.match_claims(cl, package_docs(job, ledger))
                    lang_claims = [c.to_dict() for c in cl]
                    warnings.append(f"{key}: narration text replaced by the supplied script; claims re-checked against the package sources")
                else:
                    script_path = topic / f"{key}.txt"
                    if not script_path.exists():
                        raise RenderError(f"the package has no {key} script and none was supplied")
                    lang_claims = [c for c in claims_all if c.get("language") == code]
                li = LanguageInputs(key, code, script_path, audio, "package" if not script_src else "explicit", "explicit" if audio else "none")
                if audio is not None:
                    warnings.append(f"{key}: supplied voice-over used as is (sha256 {sha256_file(audio)[:12]}…)")
                lj = LanguageJob(li, cfg, work / "out", work / "tmp", log, transcriber=transcriber, tts_chain=tts_chain,
                                 reference_voice=reference, cloner=clone_tool, aligner=aligner, evidence_dir=topic / "evidence",
                                 package={"job_id": job.name, "topic": pkg.get("topic", ""), "claims": lang_claims, "ledger": ledger, "asset_base": job})
                r = lj.run()
            except (RenderError, JobError) as exc:
                r = LanguageResult(key, code, Status.PERMANENT_FAILURE, str(exc))
            except Exception as exc:  # a bug fails this language with a clear message; the others still render
                import traceback

                log(traceback.format_exc())
                r = LanguageResult(key, code, Status.PERMANENT_FAILURE, f"unexpected error: {type(exc).__name__}: {exc}")
            results.append(r)
            statuses[key] = r
            est = (research_info.get("scripts", {}).get(key) or {}).get("estimated_seconds")
            spoken = ((r.metadata or {}).get("timing") or {}).get("sentences") or []
            if est and spoken and not script_src:
                real = spoken[-1]["end"]
                if abs(real - est) > 0.4 * est:
                    warnings.append(f"{key}: narration is {real:.1f} s, the package script reads in about {est:.0f} s: is the audio for this script?")
            log(f"[{key}] {r.status.value}: {r.message}")
            export_language(job, work / "out" / key, key, sfx, r)
        finalize_job(job, pkg, ri, results, warnings, ledger)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        (job / "qa").mkdir(exist_ok=True)
        (job / "qa" / "render.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    leaked = [p for p in job.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXT]
    if leaked:  # never ship voice-overs or voice samples in the output
        for p in leaked:
            p.unlink()
        raise RenderError(f"audio files found in the job folder and removed: {[p.name for p in leaked]}")
    return job, results


def export_language(job: Path, src: Path, key: str, sfx: str, r) -> None:
    """Rename the pipeline's per-language files into the job layout; keep diagnostics of failures."""
    moves = {"video.mp4": f"video/explainer_{sfx}.mp4", "thumbnail.png": f"video/thumbnail_{sfx}.png",
             "scene_manifest.json": f"storyboard/scene_manifest.{sfx}.json", "alignment.json": f"audio/alignment.{sfx}.json",
             "audio_info.json": f"audio/{sfx}_audio_info.json", "contact_sheet.jpg": f"qa/contact_sheet_{sfx}.jpg",
             "quality_report.json": f"qa/quality_report.{sfx}.json", "metadata.json": f"qa/metadata.{sfx}.json",
             "captions.srt": f"video/captions_{sfx}.srt", "captions.vtt": f"video/captions_{sfx}.vtt"}
    if not src.exists():
        return
    ok = r.status.ok
    for name, rel in moves.items():
        f = src / name
        if not f.exists():
            continue
        if not ok:
            rel = f"qa/failed/{sfx}/{name}"  # diagnostics of a failed language, never presented as a deliverable
        (job / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, job / rel)
    if not ok:
        for extra in ("scene_plan.json", "transcript.txt"):
            if (src / extra).exists():
                (job / "qa" / "failed" / sfx).mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / extra, job / "qa" / "failed" / sfx / extra)
        if (src / "scenes").is_dir():
            shutil.copytree(src / "scenes", job / "qa" / "failed" / sfx / "scenes", dirs_exist_ok=True)


def _contact_sheet(job: Path) -> None:
    from PIL import Image, ImageDraw

    sheets = sorted((job / "qa").glob("contact_sheet_*.jpg"))
    if not sheets:
        return
    imgs = [Image.open(s) for s in sheets]
    w = max(i.width for i in imgs)
    out = Image.new("RGB", (w, sum(i.height + 40 for i in imgs)), "white")
    d = ImageDraw.Draw(out)
    y = 0
    for s, i in zip(sheets, imgs):
        d.text((8, y + 12), s.stem.replace("contact_sheet_", "language: "), fill="black")
        out.paste(i, (0, y + 40))
        y += i.height + 40
    out.save(job / "qa" / "contact_sheet.jpg", quality=85)


def finalize_job(job: Path, pkg: dict, ri: RenderInputs, results: list, warnings: list[str], ledger: Ledger) -> None:
    qa = {}
    for r in results:
        qa[r.key] = {"status": r.status.value, "message": r.message, "quality": r.quality,
                     "stages": [s.to_dict() for s in r.stages]}
        warnings += [f"{r.key}: {w}" for s in r.stages for w in s.warnings]
    (job / "qa" / "qa_report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (job / "qa" / "warnings.json").write_text(json.dumps(warnings, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# QA report", ""]
    for r in results:
        q = r.quality or {}
        lines += [f"## {r.key}: {r.status.value}", "", r.message, ""]
        for c in q.get("checks", []):
            mark = "pass" if c["passed"] else ("FAIL" if c["critical"] else "warn")
            lines.append(f"- [{mark}] {c['name']}: {c.get('value')}" + (f" (expected {c['threshold']})" if c.get("threshold") not in (None, "") else ""))
        lines.append("")
    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings[:80]]
    (job / "qa" / "qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _contact_sheet(job)
    used: set[str] = set()
    for m in job.glob("storyboard/scene_manifest.*.json"):
        used |= {s["source_id"] for s in json.loads(m.read_text(encoding="utf-8"))["sources"]}
    text = "Sources shown in the video(s):\n\n" + (ledger.as_text(used).split("\n", 2)[2] if used else "(none)\n")
    text += "\nAll sources consulted during research: research/sources.txt\n"
    (job / "sources.txt").write_text(text, encoding="utf-8")
    voices = {r.key: (r.metadata or {}).get("voice", {}) for r in results}
    blocking = []
    for k, v in voices.items():
        cu = str(v.get("commercial_use", "unknown"))
        if not (cu.startswith("yes") or cu.startswith("user")):
            blocking.append(f"{k} narration: {v.get('provider', '?')} commercial use '{cu}'")
    for r in results:
        details = ((r.metadata or {}).get("timing") or {}).get("details", {})
        if details.get("model_license"):
            blocking.append(f"{r.key} word timing: {details['model']} ({details['model_license']})")
    for s in ledger.ordered():
        if s.source_id in used and s.commercial_status != "approved":
            blocking.append(f"{s.source_id} ({s.domain}) shown on screen: rights status {s.commercial_status}")
    ok = [r for r in results if r.status.ok]
    meta = ["# Upload notes (not uploaded automatically)", "", f"Topic: {pkg.get('topic', '')}", "",
            "## Suggested description", "", f"{pkg.get('topic', '')}", "", "Sources:", ""]
    for s in ledger.ordered():
        if s.source_id in used:
            meta.append(f"- {s.publisher or s.domain}: {s.title} {s.canonical_url}")
    meta += ["", "Screenshots and quotes are shown for commentary and identification; no publisher endorses this video.", "",
             "## Before publishing", "", "- Read qa/qa_report.md and research/source_report.md (claims marked not_found or contradicted).",
             "- Commercial clearance: " + ("**NOT cleared**: " + "; ".join(blocking[:10]) if blocking else "no blocking items recorded; still verify yourself."),
             f"- Videos: {', '.join(f'video/explainer_{SUFFIX[r.key]}.mp4' for r in ok) or 'none'}"]
    (job / "upload_metadata.md").write_text("\n".join(meta) + "\n", encoding="utf-8")
    settings = {"languages": ri.languages, "quality": ri.quality, "voice_modes": ri.voice_modes, "subtitles": ri.subtitles,
                "background_music": bool(ri.music), "allow_restricted_models": ri.allow_restricted_models}
    inputs = {"research_package": pkg.get("job_id"), "package_files": len(pkg.get("files", {})),
              "audio_sha256": {k: sha256_file(Path(p)) for k, p in ri.audio.items() if Path(p).is_file()},
              "script_sha256": {k: sha256_file(Path(p)) for k, p in ri.scripts.items() if Path(p).is_file()},
              "voice_reference_used": bool(ri.voice_reference)}
    info = build_info(settings, inputs)
    info["languages"] = {r.key: {"status": r.status.value, "message": r.message, "voice": voices.get(r.key, {})} for r in results}
    info["commercial_clearance"] = {"cleared": not blocking and bool(ok), "blocking": blocking}
    (job / "build_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
