"""Synthesised sound effects and an optional music pad (numpy only: no samples, no licences).

    pop       short rising blip when a key amount or the hook pops in
    whoosh    noise sweep when a screenshot or chart slides onto the board
    pad       very quiet sustained chord, only when background_music_path is "generated"

Everything is mixed into one stereo WAV that the encoder lays under the voice track.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    env = np.ones(n)
    env[:min(a, n)] = np.linspace(0, 1, min(a, n))
    if r < n:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _bandpass_noise(n: int, rng: np.random.Generator, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    out = np.fft.irfft(spec, n)
    return out / (np.abs(out).max() or 1.0)


def pop(rng: np.random.Generator) -> np.ndarray:
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    f = np.linspace(520, 980, n)
    return 0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38)


def whoosh(rng: np.random.Generator) -> np.ndarray:
    n = int(0.32 * SR)
    noise = _bandpass_noise(n, rng, 300, 3000)
    return 0.3 * noise * np.sin(np.linspace(0, np.pi, n)) ** 2


def pad(seconds: float) -> np.ndarray:
    """A quiet A-minor-ish chord with a slow swell (a placeholder: real music is better)."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f, a in ((110.0, 0.5), (164.8, 0.35), (220.0, 0.3), (261.6, 0.22), (329.6, 0.15)):
        out += a * np.sin(2 * np.pi * f * t) * (0.75 + 0.25 * np.sin(2 * np.pi * t / 8 + f))
    return 0.12 * out / (np.abs(out).max() or 1.0) * _env(n, 1.5, 2.0)


def events_from_layout(layout) -> list[tuple[str, float, float]]:
    """(kind, start, seconds) for every pop-in and evidence entrance in an explainer layout."""
    seen: set[int] = set()
    out: list[tuple[str, float, float]] = []
    for sc in layout.scenes:
        for sp in sc.sprites:
            # carried-over drawings are copies placed before the scene: already heard once
            if id(sp) in seen or sp.t < 0 or sp.t < sc.start - 0.5:
                continue
            seen.add(id(sp))
            if sp.anim == "pop":  # drawings are silent: no marker sound
                out.append(("pop", sp.t, 0.1))
        for w in sc.words:
            if id(w) in seen or w.t < 0:
                continue
            seen.add(id(w))
            if w.pop:
                out.append(("pop", w.t, 0.1))
    for st in layout.stages:
        out.append(("whoosh", max(0.0, st.t_in - 0.1), 0.3))
    # many pops at the same moment (the hook's words) sound like one pop
    out.sort(key=lambda e: e[1])
    merged: list[tuple[str, float, float]] = []
    last_pop = -9.0
    for e in out:
        if e[0] == "pop":
            chained = e[1] - last_pop < 0.2  # a run of staggered pop-ins is one sound
            last_pop = e[1]
            if chained:
                continue
        merged.append(e)
    return merged


def render_track(events: list[tuple[str, float, float]], seconds: float, path: str | Path, volume: float = 0.35,
                 music_pad: bool = False, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR) + SR
    mix = np.zeros((n, 2))
    for kind, t, dur in events:
        clip = pop(rng) if kind == "pop" else whoosh(rng)
        i = int(t * SR)
        if i >= n:
            continue
        clip = clip[: n - i]
        pan = rng.uniform(-0.25, 0.25)
        mix[i:i + len(clip), 0] += clip * (1 - pan)
        mix[i:i + len(clip), 1] += clip * (1 + pan)
    mix *= volume
    if music_pad:
        p = pad(seconds)
        mix[: len(p), 0] += p
        mix[: len(p), 1] += p
    mix = np.clip(mix[: int(seconds * SR)], -1, 1)
    path = Path(path)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((mix * 32767).astype("<i2").tobytes())
    return path
