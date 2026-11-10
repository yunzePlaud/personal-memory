"""Deterministic, LLM-free extraction for meeting notes written in a light Markdown convention.

Frontmatter: title, date, project, attendees|participants, type.
Sections (## or ###): 决策/决定/Decisions → decision; 事实/背景/结论/Facts/Context/Background →
semantic; 待办/行动项/Action Items/Actions/TODO → episodic + tag 'action-item'.
Each bullet becomes one create op whose evidence quote is the bullet line as written.
A leading '[slot: x]' sets the slot (prefixed with the project slug).
"""
from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError
from .textnorm import normalize_slot, slugify

_SECTIONS = {
    "decision": {"决策", "决定", "decisions", "decision"},
    "semantic": {"事实", "背景", "结论", "facts", "context", "background"},
    "action": {"待办", "行动项", "action items", "actions", "todo"},
}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$")
_SLOT_TAG = re.compile(r"^\[slot:\s*([^\]]+?)\s*\]\s*", re.IGNORECASE)
_MENTION = re.compile(r"@(\w+)")
MAX_CONTENT = 4000


def _section_kind(heading: str) -> str | None:
    h = heading.strip().casefold()
    for kind, names in _SECTIONS.items():
        if h in names:
            return kind
    return None


def _people(fm: dict[str, Any]) -> list[str]:
    raw = fm.get("attendees") or fm.get("participants") or []
    if isinstance(raw, str):
        raw = re.split(r"[,，、]", raw)
    return [str(p).strip() for p in raw if str(p).strip()]


def _body(text: str, has_frontmatter: bool) -> str:
    if has_frontmatter and text.startswith("---\n"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[end + 4:]
    return text


def _op(kind: str, content: str, quote: str, source_id: str, entities: list[dict[str, str]],
        date: str | None, slot: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    memory: dict[str, Any] = {"kind": kind, "content": content, "entities": entities,
                              "evidence": [{"source_id": source_id, "quote": quote}]}
    if slot:
        memory["slot"] = slot
    if tags:
        memory["tags"] = tags
    if date:
        memory["valid_from"] = date
    return {"op": "create", "memory": memory}


def extract_ops(source: dict[str, Any]) -> list[dict[str, Any]]:
    fm: dict[str, Any] = (source.get("metadata") or {}).get("frontmatter") or {}
    body = _body(source["text"], bool(fm))
    project = str(fm.get("project") or "").strip()
    date = source.get("source_time")  # normalised; a date we cannot parse is left out rather than failing
    people = _people(fm)
    base = [{"name": project, "kind": "project", "role": "subject"}] if project else []
    ops: list[dict[str, Any]] = []

    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), None)
    if date and first_line and (people or str(fm.get("type", "")).lower() == "meeting"):
        title = str(fm.get("title") or source.get("title") or "meeting").strip()
        content = f"Meeting: {title} on {date[:10]}." + (f" Attendees: {', '.join(people)}." if people else "")
        entities = base + [{"name": p, "kind": "person", "role": "participant"} for p in people]
        ops.append(_op("episodic", content, first_line, source["id"], entities, date, tags=["meeting"]))

    section: str | None = None
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = _section_kind(heading.group(2)) if len(heading.group(1)) >= 2 else None
            continue
        bullet = _BULLET.match(line)
        if not section or not bullet:
            continue
        quote = bullet.group(1)
        tag = _SLOT_TAG.match(quote)
        content = quote[tag.end():].strip() if tag else quote
        if not content or len(content) > MAX_CONTENT:
            continue
        slot = None
        if tag:
            try:
                slot = normalize_slot(tag.group(1))
            except ValidationError:
                slot = None  # a malformed tag must not abort the whole import
            if slot and project:
                slot = f"{slugify(project)}/{slot}"
        entities = base + [{"name": n, "kind": "person", "role": "owner"} for n in _MENTION.findall(content)]
        kind = "episodic" if section == "action" else section
        tags = ["action-item"] if section == "action" else None
        ops.append(_op(kind, content, quote, source["id"], entities, date, slot, tags))
    return ops
