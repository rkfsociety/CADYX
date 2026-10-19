"""Opt-in, revision-bound cold/warm feature rebuild regression evidence.

Never mutates the saved workspace. This is a bounded, caller-invoked checker,
not a general history replay or a guarantee of STEP byte reproducibility.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
from typing import Any, Callable

from .delivery import geometry_sha256
from .errors import CadyxError
from .models import Document

_ROOTS = {"create_box", "metric_thread", "extrude_profile_on_plane"}
_SKETCH_ROOTS = {"extrude_sketch", "revolve_sketch", "sweep_sketch", "loft_sketches"}
_DERIVED = {
    "fillet_edges", "chamfer_edges", "fuse_bodies", "cut_bodies",
    "linear_pattern_body", "circular_pattern_body", "hole", "boss",
    "pad_sketch_to_body", "rib_from_sketch", "pocket_sketch",
    "through_hole", "counterbore_hole", "countersink_hole", "threaded_hole",
}
_SUPPORTED = _ROOTS | _SKETCH_ROOTS | _DERIVED


def _design_fingerprint(document: Document) -> str:
    """Keep feature order, IDs, references, inputs and named parameter bindings."""
    payload = {
        "id": document.id,
        "version": document.version,
        "model_parameters": document.model_parameters,
        "parameter_bindings": document.parameter_bindings,
        "objects": [
            {key: value for key, value in obj.items() if key != "geometry"}
            for obj in document.objects
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rebuild_once(core: Any, document: Document, checkpoint: Callable[[], None]) -> None:
    """Regenerate supported root features, then their downstream body graph."""
    by_id = {obj["id"]: obj for obj in document.objects}
    roots: set[str] = set()
    for feature in document.objects:
        if feature.get("type") != "feature" or feature.get("operation") not in _ROOTS:
            continue
        checkpoint()
        body = by_id[feature["body_id"]]
        parameters = feature["parameters"]
        if feature["operation"] == "create_box":
            geometry = core.kernel.create_box(**parameters)
            body["geometry"] = geometry
            feature["geometry"] = geometry
        elif feature["operation"] == "metric_thread":
            geometry = core.kernel.create_metric_thread(**parameters)
            body["geometry"] = geometry
            feature["geometry"] = geometry
        else:
            # Reuse the same authoritative Command System feature rebuild path used
            # for parameter edits, rather than duplicating arbitrary-plane geometry.
            core.commands._rebuild_bound_feature(
                document, feature["id"], progress=None, rebuild_derived=False,
            )
        roots.add(body["id"])

    sketch_ids = {
        obj["id"] for obj in document.objects if obj.get("type") == "sketch"
    }
    # This is the same explicit feature-rebuild path used by sketch edits.
    if sketch_ids:
        checkpoint()
        core.commands._rebuild_changed_features(document, sketch_ids)
    if roots:
        checkpoint()
        core.commands._rebuild_derived_features(document, roots)
    checkpoint()
    document.validate()


def _close(a: Any, b: Any, *, abs_tol: float, rel_tol: float) -> bool:
    if type(a) in (int, float) and type(b) in (int, float):
        return (math.isfinite(a) and math.isfinite(b) and
                math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            _close(a[k], b[k], abs_tol=abs_tol, rel_tol=rel_tol) for k in a
        )
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            _close(x, y, abs_tol=abs_tol, rel_tol=rel_tol) for x, y in zip(a, b)
        )
    return type(a) is type(b) and a == b


def run_rebuild_regression(
    core: Any, original: Document, *, max_bodies: int,
    checkpoint: Callable[[], None],
    abs_tol: float = 1e-8, rel_tol: float = 1e-9,
) -> tuple[str, dict[str, Any]]:
    """Compare saved, cold (isolated copy), and warm (repeat) feature results.

    Unknown feature operations fail closed as untested, never as a successful
    rebuilt model. Different descriptor hashes with equivalent metrics are
    untested: equal volumes/bounds do not prove identical shapes.
    """
    bodies = [o for o in original.objects if o.get("type") == "body"]
    features = [o for o in original.objects if o.get("type") == "feature"]
    kernel_name = getattr(core.kernel, "kernel_id", type(core.kernel).__name__)
    native_version: str | None = None
    if kernel_name == "occt-v1":
        try:
            from .native_kernel import NativeKernel
            native_version = NativeKernel().version
        except (RuntimeError, OSError):
            pass
    evidence: dict[str, Any] = {
        "runtime": {"python": platform.python_version(), "kernel": kernel_name,
                    "occt_version": native_version},
        "tolerance": {"absolute_model_units": abs_tol, "relative": rel_tol},
        "scope": "same-process isolated saved-document rebuild; not a fresh OS process or STEP round-trip",
        "body_count": len(bodies), "feature_count": len(features),
        "max_bodies": max_bodies, "max_features": 256, "max_entities": 10000,
    }
    if len(original.objects) > 10000:
        return "untested", {**evidence, "reason": "document entity budget exceeded",
                            "entity_count": len(original.objects)}
    if not bodies or not features:
        return "untested", {**evidence, "reason": "no saved parametric bodies and features to rebuild"}
    if len(bodies) > max_bodies or len(features) > 256:
        return "untested", {**evidence, "reason": "body or feature budget exceeded"}
    unsupported = sorted({str(f.get("operation")) for f in features
                          if f.get("operation") not in _SUPPORTED})
    if unsupported:
        return "untested", {**evidence, "reason": "feature rebuild not implemented for every operation",
                            "unsupported_operations": unsupported[:32], "omitted": max(0, len(unsupported) - 32)}
    modeled_ids = {f.get("body_id") for f in features}
    if any(body["id"] not in modeled_ids for body in bodies):
        return "untested", {**evidence, "reason": "body without a reconstructible feature"}
    if any(f.get("operation") in _SKETCH_ROOTS and
           not f.get("source_sketch_id") and
           f.get("operation") != "loft_sketches" for f in features):
        return "untested", {**evidence, "reason": "sketch-driven feature lacks its saved source sketch"}

    baseline_design = _design_fingerprint(original)
    cold = Document.from_dict(original.to_dict())
    checkpoint()
    try:
        _rebuild_once(core, cold, checkpoint)
        warm = Document.from_dict(cold.to_dict())
        checkpoint()
        _rebuild_once(core, warm, checkpoint)
    except CadyxError as exc:
        return "fail", {**evidence, "reason": "rebuild operation failed",
                        "error": exc.as_dict()}
    stages = (("saved", original), ("cold", cold), ("warm", warm))
    fingerprints = {label: _design_fingerprint(doc) for label, doc in stages}
    evidence["design_sha256"] = fingerprints
    if any(sha != baseline_design for sha in fingerprints.values()):
        return "fail", {**evidence, "reason": "rebuild altered canonical design inputs, IDs or feature graph"}

    measures: dict[str, dict[str, dict[str, Any]]] = {}
    for label, doc in stages:
        measures[label] = {}
        for body in (obj for obj in doc.objects if obj.get("type") == "body"):
            checkpoint()
            geometry = body["geometry"]
            try:
                core.kernel.validate(geometry)
                metrics = core.kernel.metrics(geometry)
            except CadyxError as exc:
                return "fail", {**evidence, "reason": "invalid B-rep or geometry metrics",
                                "phase": label, "body_id": body["id"],
                                "error": exc.as_dict()}
            if metrics.get("brep_valid") is not True:
                return "fail", {**evidence, "reason": "kernel did not verify B-rep validity",
                                "phase": label, "body_id": body["id"]}
            measures[label][body["id"]] = {
                "descriptor_sha256": geometry_sha256(geometry),
                "volume": metrics.get("volume"),
                "bounds": metrics.get("bounds"),
                "topology": metrics.get("topology"),
            }

    diffs: list[dict[str, Any]] = []
    opaque: list[dict[str, Any]] = []
    for body_id in sorted(measures["saved"]):
        references = measures["saved"][body_id]
        for phase in ("cold", "warm"):
            measured = measures[phase][body_id]
            for key in ("volume", "bounds", "topology"):
                if not _close(references[key], measured[key], abs_tol=abs_tol, rel_tol=rel_tol):
                    diffs.append({"body_id": body_id, "phase": phase, "field": key,
                                  "saved": references[key], "rebuilt": measured[key]})
            if measured["descriptor_sha256"] != references["descriptor_sha256"]:
                opaque.append({"body_id": body_id, "phase": phase,
                               "saved_sha256": references["descriptor_sha256"],
                               "rebuilt_sha256": measured["descriptor_sha256"]})
        if measures["cold"][body_id]["descriptor_sha256"] != measures["warm"][body_id]["descriptor_sha256"]:
            # A flaky rebuild is important even if both differ equally from the saved descriptor.
            if not any(item["body_id"] == body_id and item["phase"] == "warm" for item in opaque):
                opaque.append({"body_id": body_id, "phase": "warm",
                               "saved_sha256": measures["cold"][body_id]["descriptor_sha256"],
                               "rebuilt_sha256": measures["warm"][body_id]["descriptor_sha256"]})
    evidence.update({
        "compared_bodies": len(measures["saved"]),
        "metric_differences": diffs[:16],
        "metric_difference_count": len(diffs),
        "descriptor_differences": opaque[:16],
        "descriptor_difference_count": len(opaque),
        "hashes": {phase: {body_id: body["descriptor_sha256"] for body_id, body in bodies.items()}
                   for phase, bodies in measures.items()},
    })
    checkpoint()
    if diffs:
        return "fail", {**evidence, "reason": "rebuild changed B-rep metrics beyond tolerance"}
    if opaque:
        return "untested", {**evidence, "reason": "descriptor hash changed; matching coarse metrics do not establish shape equivalence"}
    return "pass", {**evidence, "method": "saved/cold/warm feature reconstruction and kernel metrics"}
