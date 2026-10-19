"""Revision-safe persisted sketch work planes; legacy XY/XZ layout is unchanged."""
from __future__ import annotations

import math
from typing import Any
from .errors import ConflictError, ValidationError

SPATIAL_PLANES = {"YZ", "CUSTOM", "FACE"}


def vector(value: Any, name: str) -> list[float]:
    if (not isinstance(value, list) or len(value) != 3
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValidationError(f"{name} must be three finite coordinates.")
    return [float(x) for x in value]


def cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def normalized_frame(value: Any) -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"origin", "normal", "x_axis"}:
        raise ValidationError("A local frame needs origin, normal and x_axis.")
    origin = vector(value["origin"], "frame.origin")
    normal = vector(value["normal"], "frame.normal")
    x_axis = vector(value["x_axis"], "frame.x_axis")
    from .agent_authoring import _workplane_rotation
    _, _, unit_n, unit_x = _workplane_rotation(normal, x_axis)
    return {"origin": origin, "normal": unit_n, "x_axis": unit_x}


def strict_frame(value: Any) -> dict[str, list[float]]:
    frame = normalized_frame(value)
    for key in ("normal", "x_axis"):
        if any(abs(a - b) > 1e-7 for a, b in zip(value[key], frame[key], strict=True)):
            raise ValidationError("Persisted frame axes must be orthonormal unit vectors.")
    return frame


def face_frame(document: Any, kernel: Any, anchor: dict[str, Any],
               previous: dict[str, Any] | None = None) -> tuple[dict[str, list[float]], str]:
    body = document.find_object(anchor["body_id"], "body")
    if body is None:
        raise ValidationError("Attached sketch source body does not exist.")
    faces = kernel.topology(body["geometry"]).get("faces")
    if not isinstance(faces, list):
        raise ValidationError("Source body has no inspectable faces.")
    matches = [f for f in faces if isinstance(f, dict) and f.get("semantic_key") == anchor["face_key"]]
    if len(matches) != 1:
        if previous is None:
            raise ConflictError("Selected face key is missing or ambiguous; refresh topology.",
                                code="TOPOLOGY_CHANGED")
        old = vector(previous["normal"], "previous face normal")
        matches = [
            f for f in faces if isinstance(f, dict)
            and str(f.get("surface", "")).lower() in {"plane", "planar"}
            and isinstance(f.get("normal"), list) and len(f["normal"]) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in f["normal"])
            and sum(a*b for a,b in zip(old, f["normal"], strict=True)) > 1-1e-6
        ]
        if len(matches) != 1:
            raise ConflictError("Rebuilt source face is not uniquely identifiable.",
                                code="TOPOLOGY_AMBIGUOUS")
    face = matches[0]
    if str(face.get("surface", "")).lower() not in {"plane", "planar"}:
        raise ValidationError("Face-attached sketches require planar analytic faces.")
    frame = normalized_frame({"origin": vector(face.get("center"), "face center"),
                              "normal": vector(face.get("normal"), "face normal"),
                              "x_axis": anchor["x_axis"]})
    if previous is not None:
        normal = vector(previous["normal"], "previous face normal")
        if sum(a*b for a,b in zip(normal,frame["normal"],strict=True)) < 1-1e-6:
            raise ConflictError("Attached face normal flipped; explicitly reattach the sketch.",
                                code="TOPOLOGY_CHANGED")
    key = face.get("semantic_key")
    if not isinstance(key, str) or not key:
        raise ValidationError("Selected planar face has no semantic key.")
    return frame, key


def placement_from_command(document: Any, command: dict[str, Any], kernel: Any) -> dict[str, Any]:
    plane = command.get("plane", "XY")
    if plane not in {"XY", "XZ", "YZ", "CUSTOM", "FACE"}:
        raise ValidationError("Sketch plane must be XY, XZ, YZ, CUSTOM or FACE.")
    section = {"section_normal_axis", "section_offset"} & command.keys()
    if plane in {"XY", "XZ", "YZ"}:
        if "frame" in command or "attachment" in command:
            raise ValidationError("Named sketch planes cannot override their local frame.")
        result: dict[str, Any] = {"plane": plane}
        if section:
            expected = {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]
            value = command.get("section_offset")
            if command.get("section_normal_axis") != expected or type(value) not in (int,float) or not math.isfinite(value):
                raise ValidationError("Named sketch offset needs matching normal axis and finite value.")
            result.update(section_normal_axis=expected, section_offset=float(value))
        return result
    if section:
        raise ValidationError("Custom and face work planes cannot have section offsets.")
    if plane == "CUSTOM":
        if "attachment" in command:
            raise ValidationError("CUSTOM sketch cannot have a face attachment.")
        return {"plane": plane, "frame": normalized_frame(command.get("frame"))}
    if "frame" in command:
        raise ValidationError("FACE placement is derived from topology, not a supplied frame.")
    a = command.get("attachment")
    if (not isinstance(a, dict) or set(a) != {"body_id", "face_key", "x_axis"}
            or not isinstance(a.get("body_id"), str) or not isinstance(a.get("face_key"), str)
            or not a["face_key"]):
        raise ValidationError("FACE attachment needs body_id, face_key, x_axis.")
    anchor = {**a, "x_axis": vector(a["x_axis"], "attachment.x_axis")}
    frame, key = face_frame(document, kernel, anchor)
    anchor["face_key"] = key
    return {"plane": "FACE", "frame": frame, "attachment": anchor}


def validate_placement(sketch: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    plane = sketch.get("plane")
    if plane not in {"XY", "XZ", "YZ", "CUSTOM", "FACE"}:
        raise ValidationError("Unknown sketch plane.")
    if plane in {"XY", "XZ", "YZ"}:
        if "frame" in sketch or "attachment" in sketch:
            raise ValidationError("Named sketch cannot override its frame.")
        if "section_normal_axis" in sketch or "section_offset" in sketch:
            expected = {"XY":"Z","XZ":"Y","YZ":"X"}[plane]
            offset = sketch.get("section_offset")
            if (sketch.get("section_normal_axis") != expected
                    or type(offset) not in (int, float) or not math.isfinite(offset)):
                raise ValidationError("Invalid named sketch offset.")
        return
    if "section_normal_axis" in sketch or "section_offset" in sketch:
        raise ValidationError("Custom sketch cannot have a section offset.")
    strict_frame(sketch.get("frame"))
    if plane == "CUSTOM":
        if "attachment" in sketch:
            raise ValidationError("CUSTOM sketch has a face reference.")
        return
    a = sketch.get("attachment")
    if (not isinstance(a, dict) or set(a) != {"body_id", "face_key", "x_axis"}
            or not isinstance(a.get("face_key"), str) or not a["face_key"]
            or not isinstance(a.get("body_id"), str)
            or by_id.get(a["body_id"], {}).get("type") != "body"):
        raise ValidationError("FACE sketch refers to a missing source body or face key.")
    vector(a["x_axis"], "attachment.x_axis")


def refresh_face(document: Any, sketch: dict[str, Any], kernel: Any) -> None:
    if sketch.get("plane") != "FACE":
        return
    frame, key = face_frame(document, kernel, sketch["attachment"], sketch["frame"])
    sketch["frame"] = frame
    sketch["attachment"]["face_key"] = key


def local_frame(sketch: dict[str, Any]) -> dict[str, list[float]]:
    plane = sketch["plane"]
    if plane in {"CUSTOM", "FACE"}:
        return strict_frame(sketch["frame"])
    offset = float(sketch.get("section_offset", 0.0))
    if plane == "XY":
        return {"origin": [0.0,0.0,offset], "normal": [0.0,0.0,1.0], "x_axis": [1.0,0.0,0.0]}
    if plane == "XZ":
        return {"origin": [0.0,offset,0.0], "normal": [0.0,-1.0,0.0], "x_axis": [1.0,0.0,0.0]}
    return {"origin": [offset,0.0,0.0], "normal": [1.0,0.0,0.0], "x_axis": [0.0,1.0,0.0]}


def world_position(sketch: dict[str, Any], x: float, y: float) -> list[float]:
    f = local_frame(sketch)
    local_y = cross(f["normal"], f["x_axis"])
    return [f["origin"][i]+x*f["x_axis"][i]+y*local_y[i] for i in range(3)]


def kernel_plane(sketch: dict[str, Any], plane: str) -> str:
    return "XY" if sketch["plane"] in SPATIAL_PLANES else plane


def place_geometry(sketch: dict[str, Any], geom: dict[str, Any], kernel: Any) -> dict[str, Any]:
    if sketch["plane"] in SPATIAL_PLANES:
        from .agent_authoring import _agent_native, _workplane_rotation
        f = local_frame(sketch)
        axis, angle, _, _ = _workplane_rotation(f["normal"], f["x_axis"])
        if angle <= 1e-12 and f["origin"] == [0.0, 0.0, 0.0]:
            return geom
        return _agent_native(geom, 2, [*axis, angle, *f["origin"]])
    offset = sketch.get("section_offset", 0.0)
    if offset:
        shift = [0.0,0.0,0.0]
        shift[{"XY":2, "XZ":1}[sketch["plane"]]] = offset
        return kernel.translate_solid(geom, shift)
    return geom
