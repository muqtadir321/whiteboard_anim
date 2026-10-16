"""Configuration loading, merging and validation.

Precedence (lowest to highest):
  1. Built-in defaults (DEFAULT_CONFIG)
  2. Repository configuration file (input/config.yaml)
  3. Explicit overrides (CLI flags / GitHub workflow inputs)

Explicit workflow inputs always win; the config file can never silently
override them because overrides are applied last.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from . import LANGUAGE_KEYS

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas"

QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    # Reliable completion first: smaller frames, simpler scenes.
    "fast": {"width": 720, "height": 1280, "fps": 24, "detail": 0.6, "x264_preset": "veryfast", "crf": 23},
    "standard": {"width": 1080, "height": 1920, "fps": 30, "detail": 1.0, "x264_preset": "veryfast", "crf": 20},
    "high": {"width": 1080, "height": 1920, "fps": 30, "detail": 1.35, "x264_preset": "medium", "crf": 18},
}

OUTPUT_FORMATS: dict[str, dict[str, Any]] = {
    "shorts_9_16": {"aspect_ratio": "9:16"},
}

DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "name": "whiteboard-engine",
        "mode": "supplied_content",
        "output_directory": "output",
        "temporary_directory": "tmp",
    },
    "languages": {
        "english": {
            "enabled": True,
            "script_path": "input/scripts/english.txt",
            "audio_path": "input/audio/english.mp3",
            "script_language": "en",
            "caption_language": "en",
        },
        "urdu": {
            "enabled": True,
            "script_path": "input/scripts/urdu.txt",
            "audio_path": "input/audio/urdu.mp3",
            "script_language": "ur",
            "caption_language": "ur",
        },
        "roman_urdu": {
            "enabled": True,
            "script_path": "input/scripts/roman_urdu.txt",
            "audio_path": "input/audio/roman_urdu.mp3",
            "script_language": "roman_urdu",
            "caption_language": "roman_urdu",
        },
        "pashto": {
            "enabled": True,
            "script_path": "input/scripts/pashto.txt",
            "audio_path": "input/audio/pashto.mp3",
            "script_language": "ps",
            "caption_language": "ps",
        },
    },
    "voice": {
        "generate_missing": True,
        "transcribe_missing_scripts": True,
        "prefer_supplied_audio": True,
        "allow_online_free_tts": True,
        "allow_local_tts": True,
        "allow_wrong_language_fallback": False,
        # Online providers are only used when listed here explicitly.
        # "edge_tts" is available but relies on an unofficial service; see README.
        "online_providers": [],
        # Local engines are tried in this order; unavailable ones are skipped.
        "local_engines": ["piper", "mms", "espeak"],
        # Meta MMS voices are CC-BY-NC-4.0 (non-commercial). Reported in metadata.
        "allow_noncommercial_models": True,
        "sentence_pause_seconds": 0.35,
        "piper_voices": {"en": "en_US-ljspeech-medium"},
        "mms_models": {
            "ur": "facebook/mms-tts-urd-script_arabic",
            "ps": "facebook/mms-tts-pbt",
        },
        "espeak_voices": {"en": "en-us", "ur": "ur"},
        "edge_voices": {
            "en": "en-US-AndrewNeural",
            "ur": "ur-PK-AsadNeural",
            "ps": "ps-AF-GulNawazNeural",
        },
    },
    "transcription": {
        "engine": "faster_whisper",
        "model": "small",
        "compute_type": "int8",
        "minimum_confidence": 0.45,
    },
    "video": {
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "codec": "libx264",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_sample_rate": 48000,
        "maximum_duration_seconds": 180,
        "allow_longer_than_maximum": False,
        "captions_enabled": True,
        "background_music_enabled": False,
        "background_music_path": "",
        "background_music_volume": 0.08,
        "intro_seconds": 0.4,
        "outro_seconds": 0.8,
        "quality": "standard",
        "output_format": "shorts_9_16",
    },
    "animation": {
        "style": "whiteboard",
        "background": "#FFFFFF",
        "primary_stroke": "#202020",
        "accent_color": "#246BCE",
        "positive_color": "#2E8B57",
        "negative_color": "#C0392B",
        "stroke_width": 7,
        "hand_cursor_enabled": True,
        "hand_cursor_style": "hand",
        "hand_cursor_scale": 1.0,
        "semantic_scene_planning": True,
        "progressive_strokes": True,
        "drawing_speed": 1.0,
        "seed": 7,
    },
    "planner": {
        "max_scene_seconds": 9.0,
        "min_scene_seconds": 1.6,
        "local_llm": {"enabled": False, "endpoint": "http://127.0.0.1:11434/api/generate", "model": "qwen2.5:1.5b"},
    },
    "quality": {
        "minimum_successful_scenes": 1,
        "maximum_plan_validation_errors": 0,
        "maximum_caption_overflow": 0,
        "maximum_unsupported_characters": 0,
        "maximum_blank_frame_ratio": 0.1,
        "maximum_duration_mismatch_seconds": 0.75,
        "minimum_audio_peak_db": -45.0,
        "frame_samples_per_scene": 1,
    },
    "automation": {
        "automatic_topic_discovery": False,
        "topic_feeds": ["https://feeds.bbci.co.uk/news/business/rss.xml"],
        "topic_languages": ["english"],
        "retry_limit": 3,
        "retry_base_seconds": 2.0,
        "download_timeout_seconds": 60,
        "subprocess_timeout_seconds": 1800,
        "continue_on_language_failure": True,
        "upload_artifacts": True,
        "artifact_retention_days": 14,
        "target_languages": "auto",
        "minimum_free_disk_mb": 1500,
        "resume": True,
    },
}


class ConfigError(ValueError):
    pass


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_schema(name: str) -> dict[str, Any]:
    with open(SCHEMA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def validate_config(cfg: dict[str, Any]) -> None:
    schema = load_schema("config.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(cfg), key=lambda e: list(e.path))
    if errors:
        lines = [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
        raise ConfigError("Invalid configuration:\n  " + "\n  ".join(lines))
    if cfg["video"]["width"] % 2 or cfg["video"]["height"] % 2:
        raise ConfigError("video.width and video.height must be divisible by two for H.264/yuv420p")


def apply_quality_profile(cfg: dict[str, Any], explicit_video: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the selected quality profile. Explicit width/height/fps overrides survive."""
    profile = QUALITY_PROFILES[cfg["video"]["quality"]]
    explicit_video = explicit_video or {}
    if cfg["video"]["quality"] != "standard":
        for key in ("width", "height", "fps"):
            if key not in explicit_video:
                cfg["video"][key] = profile[key]
    cfg["render"] = {
        "detail": profile["detail"],
        "x264_preset": profile["x264_preset"],
        "crf": profile["crf"],
    }
    return cfg


def load_config(
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    file_cfg: dict[str, Any] = {}
    path = Path(config_path) if config_path else repo_root / "input" / "config.yaml"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                file_cfg = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Cannot parse {path}: {exc}") from exc
        if not isinstance(file_cfg, dict):
            raise ConfigError(f"{path} must contain a mapping at the top level")
    elif config_path:
        raise ConfigError(f"Configuration file not found: {path}")
    cfg = deep_merge(cfg, file_cfg)
    cfg = deep_merge(cfg, overrides or {})
    validate_config(cfg)
    apply_quality_profile(cfg, (overrides or {}).get("video"))
    validate_config({k: v for k, v in cfg.items() if k != "render"})
    return cfg


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "":
        return None
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    raise ConfigError(f"Expected a boolean value, got {value!r}")


def parse_target_languages(value: str | None) -> list[str] | None:
    """`auto`/blank -> None (all languages with content); otherwise a validated list."""
    if value is None or str(value).strip().lower() in ("", "auto", "all"):
        return None
    aliases = {"en": "english", "ur": "urdu", "ps": "pashto", "roman": "roman_urdu", "roman-urdu": "roman_urdu"}
    out = []
    for raw in str(value).replace(";", ",").split(","):
        key = raw.strip().lower().replace(" ", "_")
        if not key:
            continue
        key = aliases.get(key, key)
        if key not in LANGUAGE_KEYS:
            raise ConfigError(f"Unknown language {raw!r}; expected one of {', '.join(LANGUAGE_KEYS)}")
        if key not in out:
            out.append(key)
    return out
