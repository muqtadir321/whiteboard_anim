"""Command-line entry point.

    python -m engine.cli run [options]       generate videos
    python -m engine.cli deps                print optional engines this run needs
    python -m engine.cli check-config        validate configuration
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import LANGUAGE_KEYS
from .config import REPO_ROOT, ConfigError, load_config, parse_bool, parse_target_languages
from .input_discovery import discover_inputs
from .results import Status


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def build_overrides(args) -> dict:
    o: dict = {"video": {}, "voice": {}, "automation": {}}
    if args.output_format:
        o["video"]["output_format"] = args.output_format
    if args.quality:
        o["video"]["quality"] = args.quality
    if args.max_duration not in (None, ""):
        o["video"]["maximum_duration_seconds"] = float(args.max_duration)
    for flag, section, key in ((args.captions, "video", "captions_enabled"), (args.generate_missing, "voice", "generate_missing"),
                               (args.transcribe_missing, "voice", "transcribe_missing_scripts"), (args.auto_topic, "automation", "automatic_topic_discovery"),
                               (args.publish_artifacts, "automation", "upload_artifacts"), (args.allow_longer, "video", "allow_longer_than_maximum")):
        b = parse_bool(flag)
        if b is not None:
            o[section][key] = b
    return {k: v for k, v in o.items() if v}


def explicit_paths(args) -> dict:
    out = {}
    for key in LANGUAGE_KEYS:
        s = getattr(args, f"{key}_script", None)
        a = getattr(args, f"{key}_audio", None)
        out[key] = {"script": s or "", "audio": a or ""}
    return out


def add_common(p):
    for key in LANGUAGE_KEYS:
        p.add_argument(f"--{key.replace('_', '-')}-script", dest=f"{key}_script", default="")
        p.add_argument(f"--{key.replace('_', '-')}-audio", dest=f"{key}_audio", default="")
    p.add_argument("--config", default="")
    p.add_argument("--target-languages", default="auto")
    p.add_argument("--output-format", default="")
    p.add_argument("--quality", default="", choices=["", "fast", "standard", "high"])
    p.add_argument("--max-duration", default="")
    p.add_argument("--captions", default="")
    p.add_argument("--generate-missing", default="")
    p.add_argument("--transcribe-missing", default="")
    p.add_argument("--auto-topic", default="")
    p.add_argument("--publish-artifacts", default="")
    p.add_argument("--allow-longer", default="")
    p.add_argument("--output", default="")
    p.add_argument("--artifact-name", default="whiteboard-video-output")
    p.add_argument("--repo-root", default=str(REPO_ROOT))


def cmd_run(args) -> int:
    from .artifact_manager import finalize
    from .pipeline import LanguageJob, LanguageResult
    from .reporting import write_reports
    from .topic_discovery import discover_topic

    repo = Path(args.repo_root).resolve()
    try:
        cfg = load_config(args.config or None, build_overrides(args), repo)
        targets = parse_target_languages(args.target_languages)
    except ConfigError as exc:
        print(f"::error::{exc}")
        return 2
    out_root = Path(args.output or (repo / cfg["project"]["output_directory"])).resolve()
    tmp_root = (repo / cfg["project"]["temporary_directory"]).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    inputs = discover_inputs(cfg, explicit_paths(args), repo)
    _log("input discovery: " + json.dumps({k: {"script": v.script_source, "audio": v.audio_source, "errors": v.errors} for k, v in inputs.items()}))
    results = []
    topic_script = None
    if cfg["automation"]["automatic_topic_discovery"] and not any(i.has_content for i in inputs.values()):
        try:
            topic_script = discover_topic(cfg, _log)
            _log(f"automatic topic script: {topic_script[:120]}...")
        except Exception as exc:
            _log(f"automatic topic discovery failed: {exc}")
    for key in LANGUAGE_KEYS:
        li = inputs[key]
        if targets is not None and key not in targets:
            r = LanguageResult(key, li.code, Status.SKIPPED, "not in target languages")
            results.append(r)
            continue
        override = None
        if topic_script and not li.has_content and key in cfg["automation"]["topic_languages"]:
            override = topic_script
        if targets is not None and not li.has_content and not override and not li.errors:
            r = LanguageResult(key, li.code, Status.PERMANENT_FAILURE, "requested, but no script or voice-over was found")
            results.append(r)
            continue
        job = LanguageJob(li, cfg, out_root, tmp_root, _log, script_text_override=override)
        r = job.run()
        results.append(r)
        _log(f"{key}: {r.status.value} - {r.message}")
        if not r.status.ok and r.status != Status.SKIPPED and not cfg["automation"]["continue_on_language_failure"]:
            _log("continue_on_language_failure is false: stopping")
            break
    finalize(out_root, results, tmp_root, keep_tmp=cfg["automation"]["resume"])
    run_info = {"target_languages": targets or "auto", "quality": cfg["video"]["quality"], "inputs": {k: v.to_dict() for k, v in inputs.items()},
                "github_run_id": os.environ.get("GITHUB_RUN_ID"), "automatic_topic": bool(topic_script)}
    overall, code = write_reports(out_root, results, run_info, args.artifact_name, targets)
    _log(f"overall: {overall}")
    for r in results:
        if r.status in (Status.PERMANENT_FAILURE, Status.RETRYABLE_FAILURE):
            print(f"::warning title={r.key} failed::{r.message[:300]}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"overall={overall}\n")
            fh.write(f"videos={sum(1 for r in results if r.status.ok)}\n")
    return code


def cmd_deps(args) -> int:
    """Report which optional engines the current inputs need (used by the workflow
    to install heavy packages only when necessary)."""
    repo = Path(args.repo_root).resolve()
    cfg = load_config(args.config or None, build_overrides(args), repo)
    inputs = discover_inputs(cfg, explicit_paths(args), repo)
    targets = parse_target_languages(args.target_languages)
    need = {"transcription": False, "piper": False, "mms": False, "edge_tts": False}
    for key, li in inputs.items():
        if targets is not None and key not in targets:
            continue
        if li.audio_path and (cfg["voice"]["transcribe_missing_scripts"]) and cfg["transcription"]["engine"] != "none":
            need["transcription"] = True  # also used to align supplied script and audio
        if li.script_path and not li.audio_path and cfg["voice"]["generate_missing"]:
            tts_lang = {"english": "en", "urdu": "ur", "roman_urdu": "ur", "pashto": "ps"}[key]
            engines = cfg["voice"]["local_engines"] if cfg["voice"]["allow_local_tts"] else []
            if "piper" in engines and tts_lang in cfg["voice"]["piper_voices"]:
                need["piper"] = True
            if "mms" in engines and cfg["voice"]["allow_noncommercial_models"] and tts_lang in cfg["voice"]["mms_models"] and tts_lang not in cfg["voice"]["espeak_voices"]:
                need["mms"] = True
            if "edge_tts" in cfg["voice"]["online_providers"] and cfg["voice"]["allow_online_free_tts"]:
                need["edge_tts"] = True
    if cfg["automation"]["automatic_topic_discovery"] and not any(li.has_content for li in inputs.values()):
        need["piper"] = True
    for k, v in need.items():
        print(f"{k}={'true' if v else 'false'}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            for k, v in need.items():
                fh.write(f"{k}={'true' if v else 'false'}\n")
    return 0


def cmd_check(args) -> int:
    try:
        cfg = load_config(args.config or None, build_overrides(args), Path(args.repo_root))
    except ConfigError as exc:
        print(exc)
        return 2
    print(json.dumps({"video": cfg["video"], "render": cfg["render"]}, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="whiteboard-engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "deps", "check-config"):
        add_common(sub.add_parser(name))
    args = parser.parse_args(argv)
    return {"run": cmd_run, "deps": cmd_deps, "check-config": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
