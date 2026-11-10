"""Error types. Every public failure is a PmiError with a stable machine code."""
from __future__ import annotations

from typing import Any


class PmiError(Exception):
    code = "error"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "details": self.details}


class ValidationError(PmiError):
    code = "validation"


class NotFoundError(PmiError):
    code = "not_found"


class PolicyDenied(PmiError):
    code = "policy_denied"


class ConflictError(PmiError):
    code = "conflict"


class SchemaTooNew(PmiError):
    code = "schema_too_new"
