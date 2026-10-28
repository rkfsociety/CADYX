"""Typed application errors with safe, machine-readable engineering diagnostics."""

from __future__ import annotations

import math
import re
from typing import Any

_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s\"'<>|]+")
_UNIX_PATH = re.compile(r"(?<![\w])/(?:Users|Volumes|home|tmp|var|etc|opt|root|workspace|mnt|data|app|private|srv|run|media|proc|sys)/[^\s\"'<>]+")
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/@\s]+@")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET = re.compile(r"(?i)\b(access[_-]?token|refresh[_-]?token|token|api[_-]?key|password|secret)(\s*[:=]\s*)[^\s,;]+")


def public_text(value: str) -> str:
    """Remove credentials and absolute host paths before crossing a public boundary."""
    value = _URL_CREDENTIALS.sub(r"\1[redacted]@", value)
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _SECRET.sub(r"\1\2[redacted]", value)
    value = _WINDOWS_PATH.sub("[host path]", value)
    return _UNIX_PATH.sub("[host path]", value)


def _classification(code: str, message: str, diagnostics: dict[str, Any]) -> tuple[str, str, str | None]:
    category = diagnostics.get("category")
    failed = diagnostics.get("failed_precondition")
    hint = diagnostics.get("correction_hint")
    if code.startswith("TOPOLOGY_"):
        category = category or "topology_selection"
        failed = failed or "the requested selector must resolve uniquely against current topology"
        hint = hint or "Inspect topology at the current revision and choose a unique current selector."
    elif code.startswith("REVISION_"):
        category = category or "stale_state"
        failed = failed or "the saved document must match expected_revision"
        hint = hint or "Inspect the current project revision and rebuild the command from that state."
    elif code in {"KERNEL_OPERATION_FAILED", "INVALID_GEOMETRY"} or category == "kernel":
        category = "kernel"
        failed = failed or "the geometry-kernel operation must produce valid supported geometry"
        hint = hint or "Inspect profile closure, intersections, dimensions and source topology; no automatic repair was applied."
    elif code.startswith("SKETCH_CONSTRAINT_") or category == "constraint_solver":
        category = "constraint_solver"
        failed = failed or "the sketch constraints must be mutually consistent and converge within solver limits"
        hint = hint or "Review the listed constraint witnesses, references and residuals, then revise the intended constraints."
    elif code.startswith("SCAN_") or category == "scan":
        category = "scan"
        failed = failed or "the source scan and requested processing parameters must remain valid and unchanged"
        hint = hint or "Check the source PLY, its integrity and the documented processing limits."
    elif code.startswith("EXPORT_") or category == "export":
        category = "export"
        failed = failed or "the requested CAD export must pass round-trip and geometry-integrity checks"
        hint = hint or "Inspect the B-rep validity and reported STEP/STL verification measurements before exporting again."
    elif code == "RESOURCE_LIMIT":
        category = category or "resource_limit"
        failed = failed or "the operation must remain within its documented resource budget"
        hint = hint or "Reduce the input size or requested operation scope to the documented limit."
    elif code == "UNSUPPORTED_OPERATION":
        category = category or "unsupported_operation"
        failed = failed or "the requested operation must be supported by the current CADYX capability set"
        hint = hint or "Discover current command capabilities and select a supported operation."
    else:
        category = category or "validation"
        failed = failed or "the request must satisfy the command and document contract"
        hint = hint or "Review the supplied fields and documented limits, then submit a corrected request."
    return category, failed, hint


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {public_text(str(key)): _safe_value(item) for key, item in value.items()}
    if type(value) is float:
        return value if math.isfinite(value) else None
    if value is None or type(value) in {bool, int}:
        return value
    return None


def public_data(value: Any) -> Any:
    """Return a recursively sanitized copy of data crossing an agent boundary."""
    return _safe_value(value)


class CadyxError(Exception):
    code = "CADYX_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None,
                 diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.diagnostics = dict(diagnostics or {})

    def add_diagnostics(self, **values: Any) -> None:
        """Attach trusted operation context without replacing measured evidence."""
        for key, value in values.items():
            if value is not None and key not in self.diagnostics:
                self.diagnostics[key] = value
        category, failed, hint = _classification(self.code, str(self), self.diagnostics)
        self.diagnostics.setdefault("category", category)
        self.diagnostics.setdefault("failed_precondition", failed)
        if hint:
            self.diagnostics.setdefault("correction_hint", hint)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": self.code, "message": public_text(str(self))}
        category, failed, hint = _classification(self.code, str(self), self.diagnostics)
        diagnostics = dict(self.diagnostics)
        diagnostics.setdefault("category", category)
        diagnostics.setdefault("failed_precondition", failed)
        if hint:
            diagnostics.setdefault("correction_hint", hint)
        diagnostics.setdefault("measurements", None)
        result["diagnostics"] = _safe_value(diagnostics)
        return result


class ValidationError(CadyxError):
    code = "VALIDATION_ERROR"
    http_status = 400


class NotFoundError(CadyxError):
    code = "NOT_FOUND"
    http_status = 404


class ConflictError(CadyxError):
    code = "CONFLICT"
    http_status = 409


class LeaseRequiredError(ConflictError):
    code = "EDIT_LOCK_REQUIRED"
