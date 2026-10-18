"""Revision-bound, caller-invoked CAD project preflight composed from existing checks."""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from .delivery import geometry_sha256
from .errors import CadyxError, ConflictError, ValidationError
from .model_specifications import (
    evaluate_core_check, evaluate_specification, validate_specification_shape,
)
from .models import Document


class _Interrupted(Exception):
    """Cooperative check budget or cancellation marker."""


def check_project(
    core: Any, project_id: str, expected_revision: Any, *,
    specification: Any = None, strict: bool = False,
    export_body_id: str | None = None,
    require_assembly: bool = False, require_drawings: bool = False,
    max_bodies: int = 128, time_budget_seconds: float = 30.0,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Return measured pass/fail and explicit unsupported/unexecuted requirements."""
    for key, value in (("strict", strict), ("require_assembly", require_assembly),
                       ("require_drawings", require_drawings)):
        if type(value) is not bool:
            raise ValidationError(f"{key} must be a boolean.")
    if export_body_id is not None and (not isinstance(export_body_id, str) or not export_body_id):
        raise ValidationError("export_body_id must be non-empty text.")
    if type(max_bodies) is not int or not 1 <= max_bodies <= 128:
        raise ValidationError("max_bodies must be an integer from 1 to 128.")
    if type(time_budget_seconds) not in (int, float) or (
        not math.isfinite(time_budget_seconds) or not 0 < time_budget_seconds <= 120
    ):
        raise ValidationError("time_budget_seconds must be finite and between 0 and 120.")
    if cancelled is not None and not callable(cancelled):
        raise ValidationError("cancelled must be callable.")
    if specification is not None:
        specification = validate_specification_shape(specification)

    snapshot = core.workspace.load(project_id)
    document = Document.from_dict(snapshot["document"])
    core._check_revision(document, expected_revision)
    revision = document.revision
    bodies = [item for item in document.objects if item.get("type") == "body"]
    deadline = time.monotonic() + float(time_budget_seconds)
    stages: list[dict[str, Any]] = []

    def checkpoint() -> None:
        if cancelled is not None and cancelled():
            raise _Interrupted("cancelled by caller")
        if time.monotonic() >= deadline:
            raise _Interrupted("time budget exhausted")

    def add(name: str, status: str, method: str, evidence: dict[str, Any],
            *, required: bool = True) -> None:
        stages.append({"id": name, "status": status, "required": required,
                       "method": method, "evidence": evidence})

    # Document.from_dict validates schema, object identities and feature references.
    add("document_graph", "pass", "Document.validate", {
        "document_version": document.version, "object_count": len(document.objects),
        "feature_count": sum(obj.get("type") == "feature" for obj in document.objects),
    })
    geometry_status = "untested"
    try:
        checkpoint()
        if len(bodies) > max_bodies:
            add("geometry", "untested", "kernel.validate", {
                "reason": "body count exceeds max_bodies", "body_count": len(bodies),
                "max_bodies": max_bodies,
            })
        elif not bodies:
            add("geometry", "untested", "kernel.validate", {"reason": "no bodies to check"})
        else:
            try:
                core._validate_geometry_once(project_id, document)
            except CadyxError as exc:
                geometry_status = "fail"
                add("geometry", "fail", "kernel.validate",
                    {"body_count": len(bodies), "error": exc.as_dict()})
            else:
                geometry_status = "pass"
                add("geometry", "pass", "kernel.validate",
                    {"body_count": len(bodies), "coverage": "saved live geometry"})
    except _Interrupted as exc:
        add("geometry", "untested", "kernel.validate", {"reason": str(exc)})

    if specification is None:
        add("specification", "untested", "model_specification_v1",
            {"reason": "not requested"}, required=False)
    else:
        try:
            checkpoint()
            result = evaluate_specification(
                specification, revision=revision,
                evaluate_check=lambda operation, arguments, rev: _run_spec_check(
                    core, project_id, operation, arguments, rev, checkpoint,
                ),
            )
        except _Interrupted as exc:
            add("specification", "untested", "model_specification_v1", {"reason": str(exc)})
        else:
            add("specification", result["status"], "model_specification_v1", result)

    try:
        checkpoint()
        if geometry_status != "pass":
            add("export_eligibility", "untested", "OCCT_BRep_preflight",
                {"reason": "geometry not verified"})
        else:
            targets = [item for item in bodies if item["id"] == export_body_id] if export_body_id else bodies
            if len(targets) != 1:
                add("export_eligibility", "untested", "OCCT_BRep_preflight", {
                    "reason": "select exactly one body for export preflight",
                    "candidate_count": len(targets),
                })
            else:
                target = targets[0]
                geom = target.get("geometry")
                evidence: dict[str, Any] = {"body_id": target["id"]}
                if not isinstance(geom, dict) or geom.get("kernel") == "cadyx-mesh-v1":
                    add("export_eligibility", "fail", "OCCT_BRep_preflight",
                        {**evidence, "reason": "requires OCCT B-rep, not triangle mesh"})
                elif not callable(getattr(core.kernel, "export_delivery", None)):
                    add("export_eligibility", "untested", "OCCT_BRep_preflight",
                        {**evidence, "reason": "delivery exporter not available"})
                else:
                    metrics = core.kernel.metrics(geom)
                    valid = (metrics.get("brep_valid") is True
                             and isinstance(metrics.get("topology"), dict)
                             and metrics["topology"].get("solid_count") == 1
                             and type(metrics.get("volume")) in (int, float)
                             and math.isfinite(metrics["volume"]) and metrics["volume"] > 0)
                    add("export_eligibility", "pass" if valid else "fail",
                        "OCCT_BRep_preflight", {
                            **evidence, "geometry_sha256": geometry_sha256(geom),
                            "source_metrics": metrics,
                            "scope": "eligibility only; STEP/STL round-trip not performed",
                        })
    except _Interrupted as exc:
        add("export_eligibility", "untested", "OCCT_BRep_preflight", {"reason": str(exc)})
    except CadyxError as exc:
        add("export_eligibility", "fail", "OCCT_BRep_preflight", {"error": exc.as_dict()})

    for name, needed in (("assembly", require_assembly), ("drawings", require_drawings)):
        add(name, "untested", "capability_gate",
            {"reason": "unsupported backend" if needed else "not requested"},
            required=needed)

    current = Document.from_dict(core.workspace.load(project_id)["document"])
    if current.revision != revision:
        raise ConflictError("Saved revision changed during project check.", code="REVISION_CONFLICT")
    counts = {
        status: sum(stage["status"] == status for stage in stages if stage["required"])
        for status in ("pass", "fail", "untested")
    }
    status = "fail" if counts["fail"] else "untested" if counts["untested"] else "pass"
    return {
        "schema_version": 1, "project_id": project_id, "revision": revision,
        "status": status, "strict": strict,
        "passed": not counts["fail"] and (not strict or not counts["untested"]),
        "counts": counts, "stages": stages,
        "limits": {"max_bodies": max_bodies, "time_budget_seconds": time_budget_seconds},
    }


def _run_spec_check(
    core: Any, project_id: str, operation: str, arguments: dict[str, Any],
    revision: int, checkpoint: Callable[[], None],
) -> dict[str, Any]:
    checkpoint()
    evidence = evaluate_core_check(core, project_id, operation, arguments, revision)
    checkpoint()
    return evidence
