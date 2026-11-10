"""Turn files into normalised text plus metadata. Markdown/TXT/PDF/DOCX."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .errors import ValidationError
from .textnorm import normalize_text

SUPPORTED_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".pdf", ".docx"})
_MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ParsedDoc:
    text: str
    title: str
    mime: str
    frontmatter: dict[str, Any] = field(default_factory=dict)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def parse_markdown_text(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter, text). The text is returned unchanged; bad YAML yields {}."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0 or text[end + 4: end + 5] not in ("", "\n"):
        return {}, text
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return _jsonable(data), text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("cannot decode text (tried utf-8 and gb18030)")


def _pdf_text(data: bytes) -> str:
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):  # owner-password-only PDFs open with ""
            raise ValidationError("no extractable text (encrypted PDF)")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except ValidationError:
        raise
    except Exception as exc:  # pypdf raises many unrelated types for damaged files
        raise ValidationError(f"cannot read PDF: {type(exc).__name__}") from exc


def _docx_text(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types for bad files
        raise ValidationError(f"cannot read DOCX: {type(exc).__name__}") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def parse_bytes(data: bytes, filename: str) -> ParsedDoc:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationError(f"unsupported file type: {ext or '(none)'}")
    if ext == ".pdf":
        raw = _pdf_text(data)
    elif ext == ".docx":
        raw = _docx_text(data)
    else:
        raw = _decode(data)
    text = normalize_text(raw)
    if not text:
        raise ValidationError("no extractable text")
    frontmatter: dict[str, Any] = {}
    if ext in (".md", ".markdown", ".txt"):
        frontmatter, _ = parse_markdown_text(text)
    title = str(frontmatter.get("title") or "").strip()
    if not title:
        m = H1_PATTERN.search(text)
        title = m.group(1).strip() if m else Path(filename).stem
    return ParsedDoc(text=text, title=title, mime=_MIME[ext], frontmatter=frontmatter)


def parse_file(path: Path) -> ParsedDoc:
    return parse_bytes(Path(path).read_bytes(), Path(path).name)
