"""Immutable revision checkpoints and read-only geometry-aware comparisons.

The archive is a derived, private SQLite sidecar: project.json remains the sole
authoritative committed model. Only snapshots whose history prefix matches that
committed record are readable, including after interrupted writes.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import zlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import ValidationError


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _history_digest(history: list[dict[str, Any]], revision: int) -> str:
    return _digest(history[:revision])


def _revision(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValidationError("revision must be a nonnegative integer.")
    return value


class RevisionArchive:
    """Checkpoints of documents actually admitted to the project save path."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / ".cadyx-revisions.sqlite3"

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS snapshots ("
            "revision INTEGER PRIMARY KEY, document_hash TEXT NOT NULL, "
            "history_hash TEXT NOT NULL, document_zlib BLOB NOT NULL)"
        )

    def record(self, previous: dict[str, Any], updated: dict[str, Any]) -> None:
        if previous["document"]["revision"] == updated["document"]["revision"]:
            return
        # Write ahead of project.json. An interrupted project save leaves an
        # uncommitted row, but the reader checks the committed history prefix.
        with sqlite3.connect(self.path, timeout=10.0) as connection:
            self._initialize(connection)
            for record in (previous, updated):
                document = record["document"]
                revision = _revision(document["revision"])
                payload = _canonical(document)
                connection.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(revision) DO UPDATE SET "
                    "document_hash=excluded.document_hash, "
                    "history_hash=excluded.history_hash, document_zlib=excluded.document_zlib",
                    (revision, hashlib.sha256(payload).hexdigest(),
                     _history_digest(record["history"], revision),
                     sqlite3.Binary(zlib.compress(payload, level=6))),
                )

    def load(self, project: dict[str, Any], revision: int) -> dict[str, Any]:
        revision = _revision(revision)
        current = project["document"]
        if revision == current["revision"]:
            return deepcopy(current)
        if revision > current["revision"] or not self.path.is_file():
            raise ValidationError("The requested revision has no saved model checkpoint.",
                                  code="REVISION_UNAVAILABLE")
        with sqlite3.connect(self.path, timeout=10.0) as connection:
            try:
                record = connection.execute(
                    "SELECT document_hash, history_hash, document_zlib "
                    "FROM snapshots WHERE revision=?", (revision,),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise ValidationError("Revision checkpoint archive cannot be read.",
                                      code="REVISION_UNAVAILABLE") from exc
        if record is None or record[1] != _history_digest(project["history"], revision):
            raise ValidationError("The requested revision has no matching committed checkpoint.",
                                  code="REVISION_UNAVAILABLE")
        try:
            payload = zlib.decompress(record[2])
            if hashlib.sha256(payload).hexdigest() != record[0]:
                raise ValueError("checkpoint hash mismatch")
            document = json.loads(payload)
            if document["revision"] != revision or document["id"] != current["id"]:
                raise ValueError("checkpoint identity mismatch")
        except (ValueError, KeyError, TypeError, zlib.error) as exc:
            raise ValidationError("Revision checkpoint integrity check failed.",
                                  code="REVISION_UNAVAILABLE") from exc
        return document

    def list(self, project: dict[str, Any], *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("offset must be nonnegative and limit must be 1..100.")
        current = project["document"]
        revisions = {current["revision"]: _digest(current)}
        if self.path.is_file():
            with sqlite3.connect(self.path, timeout=10.0) as connection:
                try:
                    records = connection.execute(
                        "SELECT revision, document_hash, history_hash FROM snapshots "
                        "WHERE revision <= ? ORDER BY revision DESC", (current["revision"],),
                    ).fetchall()
                except sqlite3.DatabaseError as exc:
                    raise ValidationError("Revision checkpoint archive cannot be read.",
                                          code="REVISION_UNAVAILABLE") from exc
            for revision, document_hash, history_hash in records:
                if history_hash == _history_digest(project["history"], revision):
                    revisions.setdefault(revision, document_hash)
        ordered = sorted(revisions, reverse=True)
        return {
            "project_id": project["id"], "current_revision": current["revision"],
            "total": len(ordered), "offset": offset, "limit": limit,
            "has_more": offset + limit < len(ordered),
            "checkpoints": [
                {"revision": number, "document_sha256": revisions[number]}
                for number in ordered[offset:offset + limit]
            ],
            "coverage": "saved_checkpoints_only",
            "historical_revisions_before_archive": "unavailable_unless_checkpointed",
        }


def _index(objects: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in objects if item.get("type") == kind}


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    first, second = set(before), set(after)
    return {
        "added": sorted(second - first),
        "removed": sorted(first - second),
        "changed": sorted(key for key in first & second if before[key] != after[key]),
    }


def _assembly_changes(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    answer: dict[str, Any] = {}
    for name in ("parts", "subassemblies", "instances", "mates"):
        left = {item["id"]: item for item in first.get(name, [])}
        right = {item["id"]: item for item in second.get(name, [])}
        answer[name] = _changes(left, right)
    left = {item["id"]: item for item in first.get("instances", [])}
    right = {item["id"]: item for item in second.get("instances", [])}
    answer["moved_instances"] = [
        {"id": key, "before": {"translation": left[key]["translation"], "rotation": left[key]["rotation"]},
         "after": {"translation": right[key]["translation"], "rotation": right[key]["rotation"]}}
        for key in sorted(set(left) & set(right))
        if (left[key]["translation"], left[key]["rotation"]) !=
           (right[key]["translation"], right[key]["rotation"])
    ]
    return answer


def compare_documents(
    first: dict[str, Any], second: dict[str, Any], kernel: Any, *,
    absolute_tolerance: float = 1e-8, relative_tolerance: float = 1e-9,
    max_geometry_pairs: int = 32,
) -> dict[str, Any]:
    """Compare two already validated documents without mutating either."""
    for name, value in (("absolute_tolerance", absolute_tolerance),
                        ("relative_tolerance", relative_tolerance)):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValidationError(f"{name} must be a finite nonnegative number.")
    if first["id"] != second["id"]:
        raise ValidationError("Revision comparisons require the same document identity.")
    left, right = first["objects"], second["objects"]
    left_by_id = {item["id"]: item for item in left}
    right_by_id = {item["id"]: item for item in right}
    object_changes = _changes(left_by_id, right_by_id)
    features_before, features_after = _index(left, "feature"), _index(right, "feature")
    bodies_before, bodies_after = _index(left, "body"), _index(right, "body")
    params_before, params_after = first.get("model_parameters", {}), second.get("model_parameters", {})
    parameters = _changes(params_before, params_after)
    parameters["values"] = [
        {"name": key, "before": params_before.get(key), "after": params_after.get(key)}
        for key in sorted(set(parameters["added"] + parameters["removed"] + parameters["changed"]))
    ]
    changed_bodies = sorted(
        key for key in set(bodies_before) & set(bodies_after)
        if bodies_before[key].get("geometry") != bodies_after[key].get("geometry")
    )
    reports: list[dict[str, Any]] = []
    omitted = max(0, len(changed_bodies) - max_geometry_pairs)
    for body_id in changed_bodies[:max_geometry_pairs]:
        old = bodies_before[body_id]["geometry"]
        new = bodies_after[body_id]["geometry"]
        item: dict[str, Any] = {
            "body_id": body_id, "before_geometry_sha256": _digest(old),
            "after_geometry_sha256": _digest(new),
        }
        if old.get("kernel") == "cadyx-mesh-v1" or new.get("kernel") == "cadyx-mesh-v1":
            item.update(status="untested", reason="Triangle meshes do not support exact OCCT volume comparison.")
        else:
            try:
                metrics_old = kernel.metrics(old)
                metrics_new = kernel.metrics(new)
                volumes = kernel.compare_preserved_volume(old, new)
                required = ("reference_volume", "candidate_volume", "preserved_volume",
                            "lost_volume", "added_volume", "estimated_volume_resolution")
                if any(type(volumes.get(k)) not in (int, float)
                       or not math.isfinite(volumes[k]) or volumes[k] < 0 for k in required):
                    raise ValidationError("Kernel preservation metrics are incomplete.")
                resolution = volumes["estimated_volume_resolution"]
                error = max(abs(volumes["reference_volume"] - volumes["preserved_volume"] - volumes["lost_volume"]),
                            abs(volumes["candidate_volume"] - volumes["preserved_volume"] - volumes["added_volume"]))
                tolerance = max(float(absolute_tolerance),
                                float(relative_tolerance) * max(volumes["reference_volume"],
                                                               volumes["candidate_volume"]))
                item.update({
                    "status": "measured" if error <= resolution else "untested",
                    "method": "OCCT_BRepAlgoAPI_Common_and_Cut",
                    "volumes": {key: volumes[key] for key in required},
                    "estimated_volume_balance_error": error,
                    "volume_change": ("significant" if max(volumes["lost_volume"],
                        volumes["added_volume"]) > tolerance + resolution else "within_resolution_or_tolerance"),
                    "topology_before": metrics_old.get("topology"),
                    "topology_after": metrics_new.get("topology"),
                    "topology_metrics_changed": metrics_old.get("topology") != metrics_new.get("topology"),
                    "topology_correspondence": "untested",
                })
            except (ValidationError, NotImplementedError) as exc:
                item.update(status="untested", reason=str(exc))
        reports.append(item)
    body_inputs = _changes(bodies_before, bodies_after)
    return {
        "schema_version": 1,
        "before": {"revision": first["revision"], "document_sha256": _digest(first)},
        "after": {"revision": second["revision"], "document_sha256": _digest(second)},
        "document_version": second["version"],
        "tolerances": {"absolute_model_volume": float(absolute_tolerance),
                       "relative_volume": float(relative_tolerance)},
        "units": {"length": "model_units", "volume": "model_units^3", "assembly_translation": "mm"},
        "objects": object_changes, "parameters": parameters,
        "parameter_bindings_changed": first.get("parameter_bindings", []) != second.get("parameter_bindings", []),
        "feature_graph": _changes(features_before, features_after),
        "assembly": _assembly_changes(first.get("assembly", {}), second.get("assembly", {})),
        "bodies": body_inputs,
        "geometry": {"reports": reports, "total_changed_body_pairs": len(changed_bodies),
                     "omitted_body_pairs": omitted,
                     "coverage": "per_matching_body_id_not_global_union",
                     "added_removed_bodies": "untested_for_global_volume",
                     "topology_identity_matching": "untested"},
        "visual_highlights": {"added_body_ids": body_inputs["added"],
                              "removed_body_ids": body_inputs["removed"],
                              "changed_body_ids": body_inputs["changed"]},
        "status": "partial" if omitted or any(row["status"] == "untested" for row in reports)
                  else "measured_with_declared_limits",
        "read_only": True,
    }
