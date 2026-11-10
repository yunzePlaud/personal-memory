"""Write policy (design §7.3): a pure function of actor, operation, targets and evidence trust.

Owners may do anything. Agents and the extractor may change a memory directly only when it is not
protected: not user-confirmed, and not backed by more trusted evidence than the change brings.
"""
from __future__ import annotations

from typing import Any, Literal

from .actors import Actor
from .config import Settings
from .memories import TRUST_RANK

Decision = Literal["apply", "review", "deny"]
_OWNER_ONLY = {"confirm", "forget", "approve", "reject"}
_GUARDED = {"update", "supersede", "archive"}


def trust_rank(trust: str | None) -> int:
    return TRUST_RANK.get(trust, -1) if trust else -1


def is_protected(target: dict[str, Any], new_evidence_trust: str | None) -> bool:
    if target.get("verification") == "user_confirmed":
        return True
    return trust_rank(target.get("evidence_trust")) > trust_rank(new_evidence_trust)


def decide(op: str, actor: Actor, *, targets: list[dict[str, Any]], new_evidence_trust: str | None,
           has_evidence: bool, settings: Settings) -> tuple[Decision, str]:
    if actor.is_owner:
        return "apply", "owner"
    if op in _OWNER_ONLY:
        return "deny", f"'{op}' is owner-only"
    if op == "create":
        if has_evidence:
            return "apply", "source-backed create"
        return settings.policy_inferred_create, "create without evidence (inferred)"  # type: ignore[return-value]
    if op == "supersede" and not has_evidence:
        return settings.policy_inferred_create, "replacement without evidence (inferred)"  # type: ignore[return-value]
    if op in _GUARDED:
        if any(is_protected(t, new_evidence_trust) for t in targets):
            return "review", "target is user-confirmed or backed by more trusted evidence"
        return "apply", "target is not protected"
    if op == "restore":
        if any(str(t.get("archived_by") or "").startswith("owner:") for t in targets):
            return "review", "archived by the owner"
        return "apply", "archived by an agent"
    if op == "link":
        return "apply", "links do not change memory content"
    return "deny", f"unknown operation '{op}'"
