"""Versioned, JSON-safe scene contract shared by browser and agent clients."""

import math

from .kernel import GeometryKernel
from .models import Document
from .errors import ValidationError


def render_scene(document: Document, kernel: GeometryKernel) -> dict:
    objects = []
    by_id = {item["id"]: item for item in document.objects}
    for item in document.objects:
        kind = item["type"]
        positions, triangles, lines, points = [], [], [], []
        if kind == "body" and item.get("geometry") is not None:
            mesh = kernel.tessellate(item["geometry"])
            positions, triangles = mesh["positions"], mesh["triangles"]
        elif kind == "point":
            positions = [item["x"], item["y"], 0]
            points = [0]
        elif kind == "line":
            for key in ("start_id", "end_id"):
                point = by_id[item[key]]
                positions.extend([point["x"], point["y"], 0])
            lines = [0, 1]
        elif kind == "circle":
            center = by_id[item["center_id"]]
            for index in range(64):
                angle = math.tau * index / 64
                positions.extend([center["x"] + item["radius"] * math.cos(angle),
                                  center["y"] + item["radius"] * math.sin(angle), 0])
                lines.extend([index, (index + 1) % 64])
        else:
            continue
        if not all(math.isfinite(value) for value in positions):
            raise ValidationError("Geometry exceeds the finite viewport coordinate range.")
        objects.append({"object_id": item["id"], "type": kind,
                        "positions": positions, "triangles": triangles,
                        "lines": lines, "points": points})
    coordinates = [item["positions"] for item in objects]
    bounds = None
    if coordinates:
        bounds = {"min": [min(min(values[axis::3]) for values in coordinates) for axis in range(3)],
                  "max": [max(max(values[axis::3]) for values in coordinates) for axis in range(3)]}
        if not math.isfinite(math.hypot(*(high - low for low, high in zip(bounds["min"], bounds["max"])))):
            raise ValidationError("Scene extent exceeds the finite viewport coordinate range.")
    return {"version": 1, "revision": document.revision, "coordinate_system": "XYZ",
            "projection": "orthographic", "bounds": bounds, "objects": objects}
