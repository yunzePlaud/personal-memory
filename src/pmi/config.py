"""Settings from config/settings.toml (optional). No secrets live here."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace

from .errors import ValidationError
from .paths import Paths


@dataclass(frozen=True)
class Settings:
    embedding_provider: str = "fastembed"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    context_max_tokens: int = 4000
    vector_min_similarity: float = 0.35
    max_file_mb: int = 50
    import_roots: tuple[str, ...] = ("~/Documents", "~/Desktop", "~/Downloads")
    policy_inferred_create: str = "review"
    chunk_chars: int = 800
    chunk_overlap: int = 100


# (section, key) -> Settings field
_KEYS = {
    ("embedding", "provider"): "embedding_provider",
    ("embedding", "model"): "embedding_model",
    ("retrieval", "max_tokens"): "context_max_tokens",
    ("retrieval", "vector_min_similarity"): "vector_min_similarity",
    ("capture", "max_file_mb"): "max_file_mb",
    ("capture", "import_roots"): "import_roots",
    ("policy", "inferred_create"): "policy_inferred_create",
    ("index", "chunk_chars"): "chunk_chars",
    ("index", "chunk_overlap"): "chunk_overlap",
}
_CHOICES = {
    "embedding_provider": {"fastembed", "none"},
    "policy_inferred_create": {"review", "apply", "deny"},
}


def load_settings(paths: Paths) -> Settings:
    if not paths.config_file.exists():
        return Settings()
    try:
        raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid settings.toml: {exc}") from exc
    types = {f.name: f.type for f in fields(Settings)}
    defaults = Settings()
    updates: dict[str, object] = {}
    for section, table in raw.items():
        if not isinstance(table, dict):
            raise ValidationError(f"settings.toml: [{section}] must be a table")
        for key, value in table.items():
            name = _KEYS.get((section, key))
            if name is None:
                raise ValidationError(f"settings.toml: unknown key [{section}] {key}")
            current = getattr(defaults, name)
            if isinstance(current, tuple):
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValidationError(f"settings.toml: {section}.{key} must be a list of strings")
                value = tuple(value)
            elif isinstance(current, float) and isinstance(value, int):
                value = float(value)
            elif not isinstance(value, type(current)) or isinstance(value, bool):
                raise ValidationError(f"settings.toml: {section}.{key} has wrong type ({types[name]})")
            if name in _CHOICES and value not in _CHOICES[name]:
                raise ValidationError(f"settings.toml: {section}.{key} must be one of {sorted(_CHOICES[name])}")
            updates[name] = value
    return replace(defaults, **updates)
