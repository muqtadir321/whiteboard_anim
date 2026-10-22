"""Job folders, safe paths, package hashes and build_info.json."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
from importlib import metadata
from pathlib import Path

from .. import __version__
from ..config import REPO_ROOT
from . import SCHEMA_VERSION

JOB_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
PACKAGE_FILE = "package.json"
# Never copied into a package or a job folder (voice samples stay private).
PRIVATE_NAMES = re.compile(r"(^|/)(reference\.[a-z0-9]+|voice/.*)$", re.I)


class JobError(ValueError):
    pass


def slug(text: str, n: int = 6) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()
    return "-".join(words[:n])[:32].strip("-") or "topic"  # short: job paths must stay usable on Windows


def new_job_id(label: str, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"{now:%Y%m%d-%H%M%S}-{slug(label)}-{secrets.token_hex(2)}"


def check_job_id(job_id: str) -> str:
    if not JOB_ID.match(job_id or ""):
        raise JobError(f"invalid job id {job_id!r} (lower-case letters, digits and '-')")
    return job_id


def safe_join(root: Path, rel: str) -> Path:
    """`root/rel`, refusing absolute paths and anything that escapes root."""
    if not rel or Path(rel).is_absolute() or re.match(r"^[a-zA-Z]:", rel) or "\x00" in rel:
        raise JobError(f"unsafe relative path: {rel!r}")
    out = (root / rel).resolve()
    if out != root.resolve() and root.resolve() not in out.parents:
        raise JobError(f"path escapes {root}: {rel!r}")
    return out


def create_job_dir(out_root: Path, job_id: str) -> Path:
    """A fresh folder; an existing job is never written into (reruns never corrupt earlier jobs)."""
    check_job_id(job_id)
    path = out_root / job_id
    if path.exists() and any(path.iterdir()):
        raise JobError(f"job folder already exists: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_tree(root: Path, skip: tuple[str, ...] = (PACKAGE_FILE,)) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if rel in skip:
                continue
            out[rel] = sha256_file(p)
    return out


def write_package_manifest(job_dir: Path, info: dict) -> dict:
    files = hash_tree(job_dir)
    leaked = [f for f in files if PRIVATE_NAMES.search(f)]
    if leaked:
        raise JobError(f"voice samples must not be packaged: {leaked}")
    data = {"schema_version": SCHEMA_VERSION, "kind": "research_package", "job_id": job_dir.name,
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **info, "files": files}
    (job_dir / PACKAGE_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def verify_package(pkg_dir: Path) -> tuple[dict, list[str]]:
    """(package.json, problems): missing files, changed hashes. Extra files are allowed (renders add them)."""
    p = pkg_dir / PACKAGE_FILE
    if not p.exists():
        return {}, [f"{PACKAGE_FILE} not found in {pkg_dir}"]
    data = json.loads(p.read_text(encoding="utf-8"))
    problems = []
    if data.get("kind") != "research_package":
        problems.append("not a research package")
    for rel, digest in sorted(data.get("files", {}).items()):
        try:
            f = safe_join(pkg_dir, rel)
        except JobError as exc:
            problems.append(str(exc))
            continue
        if not f.is_file():
            problems.append(f"missing file: {rel}")
        elif sha256_file(f) != digest:
            problems.append(f"hash mismatch: {rel}")
    return data, problems


def copy_package(pkg_dir: Path, job_dir: Path, files: dict[str, str]) -> None:
    for rel in files:
        dst = safe_join(job_dir, rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_join(pkg_dir, rel), dst)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def _version(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return (subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.splitlines() or [""])[0]
    except Exception:
        return ""


def build_info(settings: dict, inputs: dict) -> dict:
    pkgs = {}
    for name in ("numpy", "pillow", "pycairo", "uharfbuzz", "fonttools", "jsonschema", "PyYAML", "playwright", "fastembed",
                 "faster-whisper", "piper-tts", "torch", "torchaudio", "transformers", "omnivoice"):
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return {"engine_version": __version__, "schema_version": SCHEMA_VERSION, "git_commit": os.environ.get("GITHUB_SHA") or _git_commit(),
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""), "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "platform": platform.platform(), "packages": pkgs,
            "ffmpeg": _version(["ffmpeg", "-version"]), "settings": settings, "inputs": inputs}
