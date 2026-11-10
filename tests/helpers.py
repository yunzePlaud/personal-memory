"""Test-only utilities: fixture file builders and a deterministic embedder."""
from __future__ import annotations

from pathlib import Path


def _pdf(content: str) -> bytes:
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return bytes(out)


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with ASCII text lines (Helvetica)."""
    esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
    body = " ".join(f"({ln}) Tj T*" for ln in esc)
    return _pdf(f"BT /F1 12 Tf 72 720 Td 14 TL {body} ET")


def make_empty_pdf() -> bytes:
    """A one-page PDF with no text (like a scanned page without OCR)."""
    return _pdf("")


def make_docx(path: Path, paragraphs: list[str], table: list[list[str]] | None = None) -> None:
    import docx

    doc = docx.Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                t.cell(r, c).text = cell
    doc.save(str(path))


def seed_source(truth, text: str, *, trust: str = "owner", source_time: str | None = None,
                origin_key: str | None = None, kind: str = "document") -> str:
    """Add a text source and return its id. Text input never touches the file store."""
    from pmi.actors import Actor
    from pmi.config import Settings
    from pmi.paths import Paths
    from pmi.sources import add_source

    out = add_source(truth, Paths(Path.cwd()), Settings(), actor=Actor.owner(), text=text,
                     trust=trust, source_time=source_time, origin_key=origin_key, kind=kind)
    return out["source_id"]


class HashEmbedder:
    """Deterministic bag-of-tokens embedder for tests. `aliases` maps tokens onto a shared bucket
    so that texts with no common token can still be made 'semantically' close."""

    def __init__(self, dim: int = 64, model_id: str | None = None, aliases: dict[str, str] | None = None):
        self.dim = dim
        self.model_id = model_id or f"hash-{dim}"
        self.aliases = aliases or {}
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import math

        from pmi.textnorm import fts_tokens

        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in fts_tokens(t).split():
                tok = self.aliases.get(tok, tok)
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out
