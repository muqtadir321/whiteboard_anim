"""Meaning-based matching across languages (free multilingual sentence embeddings, CPU only).

An Urdu sentence and the English article sentence that says the same thing get nearly the
same vector, so the script can be matched to its evidence (and spoken words to drawings)
without any per-topic word lists. Uses fastembed (ONNX runtime, no PyTorch); when it is not
installed or the model cannot be downloaded, `get_embedder()` returns None and callers fall
back to the rule-based matching.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # Apache-2.0, 50+ languages incl. Urdu

_embedders: dict[str, "Embedder | None"] = {}
_override: "Embedder | None" = None


def cache_dir() -> Path:
    root = os.environ.get("WHITEBOARD_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "whiteboard")
    return Path(root) / "semantic"


class Embedder:
    """Unit-length sentence vectors, memoised per text."""

    def __init__(self, model: str = DEFAULT_MODEL, backend=None):
        self.model = model
        self._backend = backend
        self._memo: dict[str, np.ndarray] = {}

    def _load(self):
        if self._backend is None:
            from fastembed import TextEmbedding

            path = cache_dir() / "models"
            path.mkdir(parents=True, exist_ok=True)
            self._backend = TextEmbedding(self.model, cache_dir=str(path))
        return self._backend

    def _raw(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self._load().embed(texts)), dtype=np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        todo = list(dict.fromkeys(t for t in texts if t not in self._memo))
        if todo:
            vecs = self._raw(todo)
            vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
            self._memo.update(zip(todo, vecs))
        dim = next(iter(self._memo.values())).shape[0] if self._memo else 1
        return np.array([self._memo[t] for t in texts]) if texts else np.zeros((0, dim), np.float32)

    def embed_cached(self, texts: list[str], tag: str) -> np.ndarray:
        """Like embed(), but kept on disk (for big fixed lists such as the drawing names)."""
        key = hashlib.sha1(("\n".join(texts) + self.model).encode()).hexdigest()[:16]
        path = cache_dir() / f"{tag}-{key}.npy"
        if path.exists():
            try:
                vecs = np.load(path)
                if vecs.shape[0] == len(texts):
                    return vecs
            except (OSError, ValueError):
                pass
        vecs = self.embed(texts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vecs)
        except OSError:
            pass
        return vecs


def set_embedder(emb: "Embedder | None") -> None:
    """Tests (or callers with their own model) can supply the embedder."""
    global _override
    _override = emb


def get_embedder(model: str = DEFAULT_MODEL, log=None) -> Embedder | None:
    if _override is not None:
        return _override
    if model in _embedders:
        return _embedders[model]
    emb: Embedder | None = Embedder(model)
    try:
        emb.embed(["warm up"])
    except Exception as exc:  # not installed, offline, ...
        if log:
            log(f"semantic matching unavailable ({type(exc).__name__}: {exc}); using rule-based matching")
        emb = None
    _embedders[model] = emb
    return emb


def csls(sim: np.ndarray, k: int = 4) -> np.ndarray:
    """Cross-domain similarity local scaling: 2*cos - (how close each side is to everything).
    Stops one bland sentence ("the cost is high") from being everyone's best match."""
    if sim.size == 0:
        return sim
    kq = min(k, sim.shape[1])
    kc = min(k, sim.shape[0])
    rq = np.sort(sim, axis=1)[:, -kq:].mean(axis=1, keepdims=True)
    rc = np.sort(sim, axis=0)[-kc:, :].mean(axis=0, keepdims=True)
    return 2 * sim - rq - rc
