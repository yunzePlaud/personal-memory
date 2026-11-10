"""Split source text into overlapping chunks whose spans index the original text exactly."""
from __future__ import annotations

_SENTENCE_ENDS = "。！？.!?\n"


def _cut_point(text: str, start: int, end: int) -> int:
    """Best place to end a chunk in (start, end]: paragraph break, else sentence end, else end."""
    floor = start + (end - start) // 2
    para = text.rfind("\n\n", floor, end)
    if para > floor:
        return para
    best = max(text.rfind(ch, floor, end) for ch in _SENTENCE_ENDS)
    return best + 1 if best >= floor else end


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[tuple[int, int, str]]:
    n = len(text)
    if n == 0:
        return []
    size = max(size, 50)
    overlap = max(0, min(overlap, size // 2))
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            end = _cut_point(text, start, end)
        if text[start:end].strip():
            chunks.append((start, end, text[start:end]))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks
