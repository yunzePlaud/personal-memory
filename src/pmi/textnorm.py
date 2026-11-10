"""Text normalisation, CJK-aware tokenisation for FTS5, quote matching and token estimates."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

from .errors import ValidationError

STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be been what why how when who which "
    "do does did we our you i it this that with about at by from".split()
    + "什么 为什 我们 你们 他们 一个 这个 那个 是否 怎么 如何 的是 了吗 是什".split()
)

_SLOT_RE = re.compile(r"^[\w\-./]{1,120}$")
_MULTI_NL = re.compile(r"\n{3,}")
_WS = re.compile(r"\s+")


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
        or 0x20000 <= o <= 0x2FFFF or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F
    )


def normalize_text(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw).replace("﻿", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _MULTI_NL.sub("\n\n", text).strip()


def collapse_ws(s: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space; return the result and a map to original offsets."""
    out: list[str] = []
    mapping: list[int] = []
    in_ws = False
    for i, ch in enumerate(s):
        if ch.isspace():
            if not in_ws:
                out.append(" ")
                mapping.append(i)
            in_ws = True
        else:
            out.append(ch)
            mapping.append(i)
            in_ws = False
    return "".join(out), mapping


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Whitespace-insensitive search of quote in text; returns [start, end) in the original text."""
    needle = _WS.sub(" ", unicodedata.normalize("NFC", quote)).strip()
    if not needle:
        return None
    hay, mapping = collapse_ws(text)
    pos = hay.find(needle)
    if pos < 0:
        return None
    return mapping[pos], mapping[pos + len(needle) - 1] + 1


def content_key(s: str) -> str:
    """Identity of a claim's wording: case, spacing and punctuation are ignored, except punctuation
    inside numbers (3.5, 1,000, 10:30, 2026-10) and a percent sign after a digit."""
    folded = [ch for ch in unicodedata.normalize("NFKC", s).casefold() if not ch.isspace()]
    kept = []
    for i, ch in enumerate(folded):
        if unicodedata.category(ch)[0] not in ("P", "S"):
            kept.append(ch)
            continue
        prev_digit = i > 0 and folded[i - 1].isdigit()
        next_digit = i + 1 < len(folded) and folded[i + 1].isdigit()
        if (prev_digit and next_digit) or (ch == "%" and prev_digit):
            kept.append(ch)
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def _tokens(s: str) -> list[str]:
    text = unicodedata.normalize("NFKC", s).lower()
    tokens: list[str] = []
    cjk_run: list[str] = []
    word: list[str] = []

    def flush_cjk() -> None:
        if len(cjk_run) == 1:
            tokens.append(cjk_run[0])
        else:
            tokens.extend(cjk_run[i] + cjk_run[i + 1] for i in range(len(cjk_run) - 1))
        cjk_run.clear()

    def flush_word() -> None:
        if word:
            tokens.append("".join(word))
            word.clear()

    for ch in text:
        if is_cjk(ch):
            flush_word()
            cjk_run.append(ch)
        elif ch.isalnum():
            if cjk_run:
                flush_cjk()
            word.append(ch)
        else:
            flush_word()
            if cjk_run:
                flush_cjk()
    flush_word()
    if cjk_run:
        flush_cjk()
    return tokens


def fts_tokens(s: str) -> str:
    return " ".join(_tokens(s))


def query_terms(s: str) -> list[str]:
    seen: dict[str, None] = {}
    for tok in _tokens(s):
        if tok not in STOPWORDS:
            seen.setdefault(tok, None)
    return list(seen)


def estimate_tokens(s: str) -> int:
    cjk = sum(1 for ch in s if is_cjk(ch))
    other = sum(1 for ch in s if not ch.isspace() and not is_cjk(ch))
    return cjk + math.ceil(other / 4)


def normalize_slot(s: str) -> str:
    slot = re.sub(r"-{2,}", "-", _WS.sub("-", s.strip().lower()))
    if not _SLOT_RE.match(slot):
        raise ValidationError(f"invalid slot: {s!r} (use letters, digits, '-', '.', '/')")
    return slot


def slugify(name: str) -> str:
    slug = _WS.sub("-", name.strip().lower())
    slug = re.sub(r"[^\w-]", "", slug)
    return re.sub(r"-{2,}", "-", slug).strip("-")
