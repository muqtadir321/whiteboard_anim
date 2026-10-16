"""Input discovery and path validation for every language.

Order of precedence per file: explicit workflow/CLI path > configured path >
known aliases. An explicit path that is invalid is reported as an error for
that language; it is never silently replaced with other content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import LANGUAGE_CODES, LANGUAGE_KEYS

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm", ".mp4", ".wma", ".aiff", ".aif")
SCRIPT_EXTENSIONS = (".txt", ".md")
MAX_SCRIPT_BYTES = 1_000_000
MAX_AUDIO_BYTES = 500_000_000

# Legacy / alternative locations accepted for compatibility.
SCRIPT_ALIASES = {
    "english": ["input/scripts/english", "input/scripts/en", "input/english", "scripts/english", "input/english_script"],
    "urdu": ["input/scripts/urdu", "input/scripts/ur", "input/urdu", "scripts/urdu", "input/urdu_script"],
    "roman_urdu": ["input/scripts/roman_urdu", "input/scripts/roman-urdu", "input/scripts/romanurdu", "input/roman_urdu", "scripts/roman_urdu"],
    "pashto": ["input/scripts/pashto", "input/scripts/ps", "input/pashto", "scripts/pashto", "input/pashto_script"],
}
AUDIO_ALIASES = {
    "english": ["input/audio/english", "input/audio/en", "input/english", "audio/english", "input/english_voiceover"],
    "urdu": ["input/audio/urdu", "input/audio/ur", "input/urdu", "audio/urdu", "input/urdu_voiceover"],
    "roman_urdu": ["input/audio/roman_urdu", "input/audio/roman-urdu", "input/roman_urdu", "audio/roman_urdu"],
    "pashto": ["input/audio/pashto", "input/audio/ps", "input/pashto", "audio/pashto", "input/pashto_voiceover"],
}


class PathError(ValueError):
    pass


@dataclass
class LanguageInputs:
    key: str
    code: str
    script_path: Path | None = None
    audio_path: Path | None = None
    script_source: str = "none"
    audio_source: str = "none"
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return self.script_path is not None or self.audio_path is not None

    def to_dict(self) -> dict:
        return {"language": self.key, "code": self.code, "script_path": str(self.script_path) if self.script_path else None,
                "audio_path": str(self.audio_path) if self.audio_path else None, "script_source": self.script_source,
                "audio_source": self.audio_source, "errors": self.errors, "notes": self.notes}


def safe_path(raw: str, repo_root: Path, allowed_ext: tuple[str, ...], max_bytes: int) -> Path:
    """Resolve a user path inside the repository; reject traversal and wrong file types."""
    text = (raw or "").strip()
    if not text:
        raise PathError("empty path")
    if "\x00" in text:
        raise PathError("path contains NUL byte")
    p = Path(text)
    root = repo_root.resolve()
    full = (p if p.is_absolute() else root / p).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise PathError(f"{raw!r} points outside the repository") from exc
    if full.suffix.lower() not in allowed_ext:
        raise PathError(f"{raw!r} has unsupported extension (allowed: {', '.join(allowed_ext)})")
    if not full.exists():
        raise FileNotFoundError(raw)
    if not full.is_file():
        raise PathError(f"{raw!r} is not a regular file")
    size = full.stat().st_size
    if size == 0:
        raise PathError(f"{raw!r} is empty")
    if size > max_bytes:
        raise PathError(f"{raw!r} is larger than {max_bytes // 1_000_000} MB")
    return full


def _find(stems: list[str], exts: tuple[str, ...], repo_root: Path, max_bytes: int) -> Path | None:
    for stem in stems:
        for ext in exts:
            cand = repo_root / (stem + ext)
            if cand.is_file() and cand.stat().st_size > 0:
                try:
                    return safe_path(str(cand.relative_to(repo_root)), repo_root, exts, max_bytes)
                except (PathError, FileNotFoundError):
                    continue
    return None


def _resolve(kind: str, key: str, explicit: str | None, configured: str | None, repo_root: Path, li: LanguageInputs):
    exts = SCRIPT_EXTENSIONS if kind == "script" else AUDIO_EXTENSIONS
    limit = MAX_SCRIPT_BYTES if kind == "script" else MAX_AUDIO_BYTES
    if explicit and explicit.strip():
        try:
            return safe_path(explicit, repo_root, exts, limit), "explicit"
        except FileNotFoundError:
            li.errors.append(f"{kind} path given for {key} does not exist: {explicit}")
        except PathError as exc:
            li.errors.append(f"invalid {kind} path for {key}: {exc}")
        return None, "explicit-invalid"
    if configured:
        try:
            return safe_path(configured, repo_root, exts, limit), "config"
        except FileNotFoundError:
            pass
        except PathError as exc:
            # A configured default that exists but is unusable (e.g. empty) is worth reporting.
            if (repo_root / configured).exists():
                li.notes.append(f"configured {kind} for {key} ignored: {exc}")
        # Same stem with a different extension (e.g. english.wav instead of english.mp3).
        stem = str(Path(configured).with_suffix(""))
        found = _find([stem], exts, repo_root, limit)
        if found:
            return found, "config-extension-variant"
    aliases = SCRIPT_ALIASES[key] if kind == "script" else AUDIO_ALIASES[key]
    found = _find(aliases, exts, repo_root, limit)
    if found:
        return found, "alias"
    return None, "none"


def discover_inputs(cfg: dict, explicit: dict[str, dict[str, str]] | None, repo_root: Path) -> dict[str, LanguageInputs]:
    explicit = explicit or {}
    result = {}
    for key in LANGUAGE_KEYS:
        lc = cfg["languages"][key]
        li = LanguageInputs(key, lc["script_language"] or LANGUAGE_CODES[key])
        if not lc["enabled"]:
            li.notes.append("disabled in configuration")
            result[key] = li
            continue
        ex = explicit.get(key, {})
        li.script_path, li.script_source = _resolve("script", key, ex.get("script"), lc["script_path"], repo_root, li)
        li.audio_path, li.audio_source = _resolve("audio", key, ex.get("audio"), lc["audio_path"], repo_root, li)
        result[key] = li
    return result
