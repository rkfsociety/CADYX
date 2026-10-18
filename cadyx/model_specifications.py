"""Versioned, declarative model specifications built from existing validations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from .api_contracts import SPECIFICATION_OPERATIONS, VALIDATION_OPERATIONS
from .errors import ValidationError


def validate_specification_shape(specification: Any) -> dict[str, Any]:
    """Validate and return a JSON-safe model specification definition."""
    if not isinstance(specification, dict) or set(specification) != {
        "schema_version", "title", "checks",
    }:
        raise ValidationError(
            "specification must contain only schema_version, title, and checks."
        )
    if type(specification["schema_version"]) is not int or specification["schema_version"] != 1:
        raise ValidationError("Only model specification schema_version 1 is supported.")
    title = specification["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ValidationError("Specification title must contain 1 to 120 characters.")
    checks = specification["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise ValidationError("A specification must contain between 1 and 16 checks.")

    seen_ids: set[str] = set()
    normalized_checks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "operation", "arguments"}:
            raise ValidationError("Each specification check needs exactly id, operation, and arguments.")
        check_id = check["id"]
        operation = check["operation"]
        arguments = check["arguments"]
        if not isinstance(check_id, str) or not check_id.strip() or len(check_id) > 120:
            raise ValidationError("Each specification check id must contain 1 to 120 characters.")
        if check_id in seen_ids:
            raise ValidationError(f"Specification check id {check_id!r} is duplicated.")
        seen_ids.add(check_id)
        if not isinstance(operation, str) or operation not in SPECIFICATION_OPERATIONS:
            allowed = ", ".join(SPECIFICATION_OPERATIONS)
            raise ValidationError(f"Unsupported specification operation; choose one of: {allowed}.")
        if not isinstance(arguments, dict):
            raise ValidationError(f"Arguments for specification check {check_id!r} must be an object.")

        contract = _CHECK_CONTRACTS[operation]
        schema = contract["input_schema"]
        properties = schema["properties"]
        allowed_fields = set(properties) - {"expected_revision", "persist"}
        required_fields = set(schema["required"]) - {"expected_revision", "persist"}
        if not required_fields <= arguments.keys() or not arguments.keys() <= allowed_fields:
            required_text = ", ".join(sorted(required_fields))
            raise ValidationError(
                f"Specification check {check_id!r} for {operation!r} requires only supported "
                f"arguments and must include: {required_text}."
            )
        normalized_checks.append({
            "id": check_id,
            "operation": operation,
            "arguments": dict(arguments),
        })

    normalized = {
        "schema_version": 1,
        "title": title.strip(),
        "checks": normalized_checks,
    }
    try:
        json.dumps(normalized, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Specification values must be finite JSON data.") from exc
    return normalized


def evaluate_specification(
    specification: Any,
    *,
    revision: int,
    evaluate_check: Callable[[str, dict[str, Any], int], dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate all checks against one exact saved document revision."""
    definition = validate_specification_shape(specification)
    results = []
    for check in definition["checks"]:
        evidence = evaluate_check(check["operation"], check["arguments"], revision)
        status = evidence.get("status")
        if status not in ("pass", "fail", "untested"):
            raise ValidationError(
                f"Validation for specification check {check['id']!r} returned no recognized status."
            )
        results.append({
            "id": check["id"],
            "operation": check["operation"],
            "status": status,
            "evidence": evidence,
        })

    counts = {
        status: sum(check["status"] == status for check in results)
        for status in ("pass", "fail", "untested")
    }
    overall_status = (
        "fail" if counts["fail"] else
        "untested" if counts["untested"] else
        "pass"
    )
    encoded_definition = json.dumps(
        definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return {
        "status": overall_status,
        "revision": revision,
        "specification": definition,
        "specification_sha256": hashlib.sha256(encoded_definition).hexdigest(),
        "check_count": len(results),
        "counts": counts,
        "checks": results,
    }


def evaluate_core_check(
    core: Any,
    project_id: str,
    operation: str,
    arguments: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    """Dispatch one supported check through CADYX's existing Core validators."""
    if operation == "dimensions":
        return core.validate_body_dimensions(
            project_id, arguments["body_id"], expected_revision,
            arguments["expected_dimensions"], arguments["tolerance"], persist=False,
        )
    if operation == "clearance":
        return core.validate_body_clearance(
            project_id, arguments["first_body_id"], arguments["second_body_id"],
            expected_revision, arguments["minimum_clearance"], arguments["tolerance"],
            persist=False,
        )
    if operation == "preservation":
        return core.validate_body_preservation(
            project_id, arguments["reference_body_id"], arguments["candidate_body_id"],
            expected_revision, arguments["maximum_lost_volume"], arguments["tolerance"],
            persist=False,
        )
    if operation == "thickness":
        return core.validate_body_thickness(
            project_id, arguments["body_id"], expected_revision,
            arguments["minimum_thickness"], arguments["tolerance"],
            method=arguments.get("method", "sampled"), persist=False,
        )
    raise ValidationError(f"Unsupported specification operation: {operation}.")


_CHECK_CONTRACTS: dict[str, dict[str, Any]] = {
    name: VALIDATION_OPERATIONS[name] for name in SPECIFICATION_OPERATIONS
}
