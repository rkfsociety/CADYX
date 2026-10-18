"""Shared public validation request contracts; Core owns value/geometry validation."""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

SPECIFICATION_OPERATIONS = ("dimensions", "clearance", "preservation", "thickness")

VALIDATION_OPERATIONS: dict[str, dict[str, Any]] = {
    "dimensions": {
        "tool_name": "cadyx_validate_body_dimensions",
        "description": "Read-only axis-aligned bounding-box dimension check bound to an exact "
        "saved revision and geometry hash; optionally persist the result.",
        "method": "POST",
        "path": "/api/v1/agent/projects/{id}/validations/dimensions",
        "input_schema": {
            "type": "object",
            "properties": {
                "body_id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "integer", "minimum": 0},
                "expected_dimensions": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "z": {"type": "number"},
                    },
                    "required": ["x", "y", "z"],
                    "additionalProperties": False,
                },
                "tolerance": {"type": "number", "minimum": 0},
                "persist": {"type": "boolean"},
            },
            "required": [
                "body_id",
                "expected_revision",
                "expected_dimensions",
                "tolerance",
            ],
            "additionalProperties": False,
        },
        "defaults": {"persist": False},
        "field_error": "Dimension validation requires body_id, expected_revision, "
        "expected_dimensions, and tolerance.",
    },
    "clearance": {
        "tool_name": "cadyx_validate_body_clearance",
        "description": "Read-only OCCT minimum-distance and common-volume check for two B-rep "
        "bodies, bound to expected_revision and a combined geometry hash; "
        "classifies interference, streams progress through agent.activity events, "
        "and can persist the result.",
        "method": "POST",
        "path": "/api/v1/agent/projects/{id}/validations/clearance",
        "input_schema": {
            "type": "object",
            "properties": {
                "first_body_id": {"type": "string", "minLength": 1},
                "second_body_id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "integer", "minimum": 0},
                "minimum_clearance": {"type": "number", "minimum": 0},
                "tolerance": {"type": "number", "minimum": 0},
                "persist": {"type": "boolean"},
            },
            "required": [
                "first_body_id",
                "second_body_id",
                "expected_revision",
                "minimum_clearance",
                "tolerance",
            ],
            "additionalProperties": False,
        },
        "defaults": {"persist": False},
        "field_error": "Clearance validation requires both body ids, expected_revision, "
        "minimum_clearance, and tolerance.",
    },
    "preservation": {
        "tool_name": "cadyx_validate_body_preservation",
        "description": "Compare a reference and candidate solid using OCCT common volume; "
        "reports overlapping, lost, and added volume in model units cubed, "
        "with pass/fail/untested against the maximum permitted loss and "
        "tolerance, bound to a saved revision and geometry hash.",
        "method": "POST",
        "path": "/api/v1/agent/projects/{id}/validations/preservation",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference_body_id": {"type": "string", "minLength": 1},
                "candidate_body_id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "integer", "minimum": 0},
                "maximum_lost_volume": {"type": "number", "minimum": 0},
                "tolerance": {"type": "number", "minimum": 0},
                "persist": {"type": "boolean"},
            },
            "required": [
                "reference_body_id",
                "candidate_body_id",
                "expected_revision",
                "maximum_lost_volume",
                "tolerance",
            ],
            "additionalProperties": False,
        },
        "defaults": {"persist": False},
        "field_error": "Preservation validation requires reference_body_id, "
        "candidate_body_id, expected_revision, maximum_lost_volume, and "
        "tolerance.",
    },
    "thickness": {
        "tool_name": "cadyx_validate_body_thickness",
        "description": "Validate a body against minimum_thickness using sampled inward-normal "
        "rays, or method=global_minimum to certify minimum support width for a "
        "convex planar polyhedron. The certified method returns a numeric "
        "enclosure and is untested for curved, non-linear-edged, or non-convex "
        "solids. Revision/hash bound; can persist the result.",
        "method": "POST",
        "path": "/api/v1/agent/projects/{id}/validations/thickness",
        "input_schema": {
            "type": "object",
            "properties": {
                "body_id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "integer", "minimum": 0},
                "minimum_thickness": {"type": "number", "exclusiveMinimum": 0},
                "tolerance": {"type": "number", "minimum": 0},
                "method": {"type": "string", "enum": ["sampled", "global_minimum"]},
                "persist": {"type": "boolean"},
            },
            "required": [
                "body_id",
                "expected_revision",
                "minimum_thickness",
                "tolerance",
            ],
            "additionalProperties": False,
        },
        "defaults": {"persist": False, "method": "sampled"},
        "field_error": "Thickness validation requires body_id, expected_revision, "
        "minimum_thickness, and tolerance; optional fields are method and "
        "persist.",
    },
}


def _specification_check_schema(operation: str) -> dict[str, Any]:
    contract = VALIDATION_OPERATIONS[operation]["input_schema"]
    properties = contract["properties"]
    argument_properties = {
        name: value for name, value in properties.items()
        if name not in {"expected_revision", "persist"}
    }
    required_arguments = [
        name for name in contract["required"]
        if name not in {"expected_revision", "persist"}
    ]
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 120},
            "operation": {"type": "string", "enum": [operation]},
            "arguments": {
                "type": "object",
                "properties": argument_properties,
                "required": required_arguments,
                "additionalProperties": False,
            },
        },
        "required": ["id", "operation", "arguments"],
        "additionalProperties": False,
    }


_SPECIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "checks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 16,
            "items": {"oneOf": [_specification_check_schema(name) for name in SPECIFICATION_OPERATIONS]},
        },
    },
    "required": ["schema_version", "title", "checks"],
    "additionalProperties": False,
}

VALIDATION_OPERATIONS["specification"] = {
    "tool_name": "cadyx_validate_model_specification",
    "description": "Evaluate a versioned declarative model specification using read-only dimension, clearance, preservation, and thickness checks at one exact saved revision; optionally persist the combined report.",
    "method": "POST",
    "path": "/api/v1/agent/projects/{id}/validations/specification",
    "input_schema": {
        "type": "object",
        "properties": {
            "expected_revision": {"type": "integer", "minimum": 0},
            "specification": _SPECIFICATION_SCHEMA,
            "persist": {"type": "boolean"},
        },
        "required": ["expected_revision", "specification"],
        "additionalProperties": False,
    },
    "defaults": {"persist": False},
    "field_error": "Model specification validation requires expected_revision and a version 1 specification.",
}


def validate_request_fields(operation: str, body: dict[str, Any] | None) -> None:
    """Preserve HTTP field/error semantics without duplicating request field lists."""
    contract = VALIDATION_OPERATIONS[operation]
    schema = contract["input_schema"]
    if (
        body is None
        or not set(schema["required"]).issubset(body)
        or set(body) - schema["properties"].keys()
    ):
        raise ValidationError(contract["field_error"])
