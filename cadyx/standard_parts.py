"""Immutable built-in standard-part presets and deterministic command resolution.

Presets contain no executable downloaded code. Each declared catalog revision is
content-addressed, and every created feature records the resolved origin.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any

from .errors import ValidationError

CATALOG_FORMAT_VERSION = 1
_MAX_MM = 100_000.0
_LICENSES = frozenset({
    "MIT", "BSD-3-Clause", "Apache-2.0", "CC0-1.0", "CC-BY-4.0", "Proprietary",
})

# Do not advertise a manufacturer's specification or licensed geometry unless
# it has actually been supplied and verified. These are CADYX-authored presets.
_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "cadyx.threaded-stud", "version": "1.0.0", "recipe_version": 1,
        "category": "fastener", "name": "Metric threaded stud",
        "template": "metric_thread", "parameters": ["major_diameter", "pitch", "length"],
        "license": "MIT", "source": "CADYX original parametric preset",
        "assumptions": ["mm", "External single-start right-hand modeled thread; no head or nut.",
                        "Not a certified ISO fastener or tolerance-class fit."],
    },
    {
        "id": "cadyx.ring-spacer", "version": "1.0.0", "recipe_version": 1,
        "category": "bearing_profile", "name": "Annular bearing/spacer envelope",
        "template": "annular_extrusion",
        "parameters": ["outer_diameter", "inner_diameter", "length"],
        "license": "MIT", "source": "CADYX original parametric preset",
        "assumptions": ["mm", "One-piece annular reference geometry, not a working rolling bearing.",
                        "No balls, races, seals, fits, internal clearances or rated loads."],
    },
    {
        "id": "cadyx.hex-bar", "version": "1.0.0", "recipe_version": 1,
        "category": "profile", "name": "Regular hexagonal bar",
        "template": "hexagonal_extrusion", "parameters": ["across_flats", "length"],
        "license": "MIT", "source": "CADYX original parametric preset",
        "assumptions": ["mm", "Unchamfered solid regular hexagon; no stock grade or tolerance."],
    },
    {
        "id": "cadyx.rectangular-extrusion", "version": "1.0.0", "recipe_version": 1,
        "category": "extrusion", "name": "Solid rectangular extrusion",
        "template": "box", "parameters": ["width", "height", "length"],
        "license": "MIT", "source": "CADYX original parametric preset",
        "assumptions": ["mm", "Solid section without slots, finish or extrusion-standard certification."],
    },
    {
        "id": "cadyx.staged-step-reference", "version": "1.0.0", "recipe_version": 1,
        "category": "purchased_reference", "name": "Staged purchased STEP reference",
        "template": "staged_step",
        "parameters": ["file", "file_sha256", "supplier", "part_number",
                       "license_id", "usage_rights_confirmed"],
        "license": "User-declared, per-source", "source": "Locally staged user-supplied STEP",
        "assumptions": ["mm in resulting CADYX model; supplied STEP units are handled by the OCCT importer.",
                        "No download or automatic third-party license approval.",
                        "Caller must attest usage rights and pin the exact STEP source hash.",
                        "Geometry is an imported B-rep reference, not a parametric native part."],
    },
)


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(entry: dict[str, Any]) -> dict[str, Any]:
    record = deepcopy(entry)
    record["format_version"] = CATALOG_FORMAT_VERSION
    record["sha256"] = _digest(record)
    return record


_CATALOG = {entry["id"]: _manifest(entry) for entry in _PRESETS}


def list_standard_parts() -> dict[str, Any]:
    """Return owned records; no registry references escape to callers."""
    return {"format_version": CATALOG_FORMAT_VERSION,
            "items": [deepcopy(_CATALOG[key]) for key in sorted(_CATALOG)]}


def _positive_mm(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= _MAX_MM:
        raise ValidationError(f"{name} must be positive finite millimeters, at most {_MAX_MM:g}.")
    return float(value)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ValidationError(f"{name} must contain 1–120 nonblank characters.")
    return value.strip()


def resolve_standard_part(request: dict[str, Any]) -> dict[str, Any]:
    """Validate an exact catalog revision and compile one existing leased command."""
    if not isinstance(request, dict) or set(request) - {
        "catalog_id", "catalog_version", "catalog_sha256", "parameters", "name"
    }:
        raise ValidationError("Catalog resolution accepts catalog_id, catalog_version, catalog_sha256, parameters and optional name.")
    catalog_id = request.get("catalog_id")
    entry = _CATALOG.get(catalog_id) if isinstance(catalog_id, str) else None
    if entry is None:
        raise ValidationError("Unknown standard-part catalog entry.")
    if (request.get("catalog_version") != entry["version"]
            or request.get("catalog_sha256") != entry["sha256"]):
        raise ValidationError("Catalog version or SHA-256 mismatch; refresh the catalog before creating a part.")
    parameters = request.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != set(entry["parameters"]):
        raise ValidationError("Catalog parameters must contain exactly: " + ", ".join(entry["parameters"]) + ".")
    params: dict[str, Any] = {}
    if entry["template"] == "staged_step":
        params["file"] = _required_text(parameters["file"], "file")
        if not params["file"].lower().endswith((".stp", ".step")):
            raise ValidationError("Catalog STEP file must end in .stp or .step.")
        digest = parameters["file_sha256"]
        if not isinstance(digest, str) or re.fullmatch("[a-f0-9]{64}", digest) is None:
            raise ValidationError("file_sha256 must be a lowercase 64-character SHA-256 hex digest.")
        params["file_sha256"] = digest
        params["supplier"] = _required_text(parameters["supplier"], "supplier")
        params["part_number"] = _required_text(parameters["part_number"], "part_number")
        if parameters["license_id"] not in _LICENSES:
            raise ValidationError("Unknown license_id; only the documented explicit license identifiers are supported.")
        if parameters["usage_rights_confirmed"] is not True:
            raise ValidationError("The caller must explicitly confirm rights to use this STEP reference.")
        params["license_id"] = parameters["license_id"]
        params["usage_rights_confirmed"] = True
    else:
        params = {key: _positive_mm(parameters[key], key) for key in entry["parameters"]}
        if entry["template"] == "metric_thread":
            if params["major_diameter"] <= 1.25 * params["pitch"]:
                raise ValidationError("Thread major_diameter must exceed 1.25 times pitch.")
        elif entry["template"] == "annular_extrusion":
            if params["inner_diameter"] >= params["outer_diameter"]:
                raise ValidationError("Ring inner_diameter must be smaller than outer_diameter.")
            if (params["outer_diameter"] - params["inner_diameter"]) <= 1e-6:
                raise ValidationError("Ring radial thickness is too small.")
    name = _required_text(request.get("name", entry["name"]), "Part name")
    command = {
        "type": "create_catalog_part", "catalog_id": entry["id"],
        "catalog_version": entry["version"], "catalog_sha256": entry["sha256"],
        "parameters": params, "name": name,
    }
    return {"catalog": deepcopy(entry),
            "instance_sha256": _digest({"catalog_sha256": entry["sha256"],
                                        "parameters": params}),
            "command": command}


CAPABILITIES = ({
    "type": "create_catalog_part",
    "description": "Create one immutable-versioned built-in standard-part preset or import a pinned staged STEP reference as a single leased atomic command.",
    "required": ["catalog_id", "catalog_version", "catalog_sha256", "parameters"],
    "optional": ["name"],
    "constraints": ["First call GET /api/v1/agent/catalog and POST /api/v1/agent/catalog/resolve.",
                    "Provide the exact version and manifest SHA-256. All dimensions are mm.",
                    "Only data-only built-in templates and explicitly attested staged STEP files are allowed.",
                    "Creates a geometry feature with immutable catalog-origin metadata; does not automatically place an assembly instance."],
    "parameter_schemas": {
        "catalog_id": {"type": "string"}, "catalog_version": {"type": "string"},
        "catalog_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "parameters": {"type": "object"}, "name": {"type": "string"},
    },
},)
ACTIVITY_ACTIONS = {"create_catalog_part": "Создаёт стандартную деталь из каталога"}


def _execute(self: Any, document: Any, command: dict[str, Any], *,
             progress: Any = None, project_id: str | None = None) -> dict[str, Any]:
    if not isinstance(command, dict) or set(command) - {
        "type", "catalog_id", "catalog_version", "catalog_sha256", "parameters", "name"
    }:
        raise ValidationError("Unexpected catalog command fields.")
    resolved = resolve_standard_part({key: value for key, value in command.items() if key != "type"})
    entry, params = resolved["catalog"], resolved["command"]["parameters"]
    name = resolved["command"]["name"]
    template = entry["template"]

    if template == "box":
        result = self._create_box(document, {"name": name, "width": params["width"],
                                              "height": params["height"], "depth": params["length"]})
    elif template == "metric_thread":
        result = self._create_metric_thread(document, {
            "name": name, "major_diameter": params["major_diameter"],
            "pitch": params["pitch"], "length": params["length"],
        }, progress=progress)
    elif template in ("hexagonal_extrusion", "annular_extrusion"):
        from .command_sketches import _handle_create_sketch
        sketch_id = _handle_create_sketch(self, document, {
            "name": name + " profile", "plane": "XY"
        })["sketch_id"]
        if template == "hexagonal_extrusion":
            radius = params["across_flats"] / math.sqrt(3.0)
            points = [
                {"x": radius * math.cos(math.pi * index / 3.0),
                 "y": radius * math.sin(math.pi * index / 3.0)}
                for index in range(6)
            ]
            self._add_sketch_polyline(document, {"sketch_id": sketch_id,
                                                   "points": points, "closed": True}, progress=progress)
        else:
            self._add_sketch_circle(document, {"sketch_id": sketch_id, "x": 0.0, "y": 0.0,
                                                "radius": params["outer_diameter"] / 2.0},
                                     progress=progress)
            self._add_sketch_circle(document, {"sketch_id": sketch_id, "x": 0.0, "y": 0.0,
                                                "radius": params["inner_diameter"] / 2.0},
                                     progress=progress)
        result = self._create_profile_feature(document, {
            "sketch_id": sketch_id, "name": name, "depth": params["length"]
        }, "extrude_sketch", progress=progress)
    elif template == "staged_step":
        result = self._import_step(document, {
            "file": params["file"], "name": name,
        }, project_id=project_id, progress=progress)
        if result["source"]["sha256"] != params["file_sha256"]:
            raise ValidationError("Staged STEP SHA-256 does not match the pinned catalog source.")
    else:
        raise ValidationError("Unsupported catalog template.")

    origin = {
        "catalog_id": entry["id"], "catalog_version": entry["version"],
        "catalog_sha256": entry["sha256"],
        "instance_sha256": resolved["instance_sha256"],
        "parameters_at_creation": deepcopy(params),
        "license": entry["license"], "source": entry["source"],
    }
    # This records creation provenance, not a claim that subsequent edits still
    # match the original preset or source standard.
    feature = next(item for item in document.objects if item.get("id") == result["feature_id"])
    feature["catalog_origin"] = deepcopy(origin)
    return {"body_id": result["body_id"], "feature_id": result["feature_id"],
            "catalog_origin": origin, "source_sketch_id": result.get("source_sketch_id")}
