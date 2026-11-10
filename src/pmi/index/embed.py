"""Pluggable embedders. Runtime never downloads: models load from data/models only."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..config import Settings
from ..paths import Paths


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbedderUnavailable(Exception):
    pass


class FastEmbedder:
    """Local ONNX embeddings via fastembed (optional `semantic` extra)."""

    def __init__(self, model: str, cache_dir: Path, *, local_only: bool = True):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbedderUnavailable("fastembed is not installed (uv sync --extra semantic)") from exc
        try:
            self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir), local_files_only=local_only)
        except Exception as exc:  # fastembed raises various types when files are missing
            raise EmbedderUnavailable(
                f"model {model} is not available locally; run `pmi model download` ({type(exc).__name__})"
            ) from exc
        self.model_id = model
        self.dim = len(self.embed(["dimension probe"])[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        vecs = np.asarray(list(self._model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vecs / norms).tolist()


def _try(settings: Settings, paths: Paths) -> tuple[Embedder | None, str | None]:
    if settings.embedding_provider == "none":
        return None, "disabled in settings"
    try:
        return FastEmbedder(settings.embedding_model, paths.models), None
    except EmbedderUnavailable as exc:
        return None, str(exc)


def get_embedder(settings: Settings, paths: Paths) -> Embedder | None:
    return _try(settings, paths)[0]


def embedder_status(settings: Settings, paths: Paths) -> dict[str, Any]:
    emb, reason = _try(settings, paths)
    return {"available": emb is not None, "provider": settings.embedding_provider,
            "model": settings.embedding_model, "reason": reason}


def download_model(settings: Settings, paths: Paths) -> dict[str, Any]:
    if settings.embedding_provider == "none":
        return {"downloaded": False, "reason": "disabled in settings"}
    paths.models.mkdir(parents=True, exist_ok=True)
    emb = FastEmbedder(settings.embedding_model, paths.models, local_only=False)
    return {"downloaded": True, "model": emb.model_id, "dim": emb.dim, "cache_dir": "data/models"}
