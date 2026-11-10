"""Who is acting. Policy decisions depend only on the actor kind."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActorKind = Literal["owner", "agent", "extractor"]


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    name: str

    @staticmethod
    def owner(name: str = "cli") -> "Actor":
        return Actor("owner", name)

    @staticmethod
    def agent(client: str) -> "Actor":
        return Actor("agent", client or "unknown")

    @staticmethod
    def extractor() -> "Actor":
        return Actor("extractor", "structured")

    @property
    def is_owner(self) -> bool:
        return self.kind == "owner"

    def __str__(self) -> str:
        if self.kind == "extractor":
            return "extractor"
        return f"{self.kind}:{self.name}"
