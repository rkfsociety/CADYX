"""Initial demonstration commands routed through one atomic command system."""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .kernel import GeometryKernel
from .models import Document


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValidationError(f"{name} must be a finite positive number.")
    return result


class CommandSystem:
    def __init__(self, kernel: GeometryKernel) -> None:
        self.kernel = kernel

    def execute(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(command, dict):
            raise ValidationError("command must be a JSON object.")
        kind = command.get("type")
        if kind == "create_box":
            return self._create_box(document, command)
        if kind == "set_parameter":
            return self._set_parameter(document, command)
        raise ValidationError(f"Unsupported command type: {kind!r}.")

    def _create_box(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        name = command.get("name", "Box")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Box name must be a non-empty string of at most 120 characters.")
        parameters = {
            "width": _number(command.get("width"), "width"),
            "height": _number(command.get("height"), "height"),
            "depth": _number(command.get("depth"), "depth"),
        }
        geometry = self.kernel.create_box(**parameters)
        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        document.objects.extend([
            {
                "id": body_id,
                "type": "body",
                "name": name.strip(),
                "feature_ids": [feature_id],
                "geometry": geometry,
            },
            {
                "id": feature_id,
                "type": "feature",
                "operation": "create_box",
                "body_id": body_id,
                "parameters": parameters,
                "geometry": geometry,
            },
        ])
        return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}

    def _set_parameter(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        feature_id = command.get("feature_id")
        parameter_name = command.get("name")
        if parameter_name not in {"width", "height", "depth"}:
            raise ValidationError("Only box width, height, and depth parameters are supported.")
        feature = next(
            (item for item in document.objects if item.get("id") == feature_id and item.get("type") == "feature"),
            None,
        )
        if feature is None or feature.get("operation") != "create_box":
            raise NotFoundError("Editable box feature was not found.")
        value = _number(command.get("value"), parameter_name)
        parameters = dict(feature["parameters"])
        parameters[parameter_name] = value
        geometry = self.kernel.create_box(**parameters)
        feature["parameters"] = parameters
        feature["geometry"] = geometry
        body = next(
            (item for item in document.objects if item.get("id") == feature.get("body_id") and item.get("type") == "body"),
            None,
        )
        if body is None:
            raise ValidationError("Box feature refers to a missing body.")
        body["geometry"] = geometry
        return {"feature_id": feature_id, "parameter": parameter_name, "value": value, "geometry": geometry}
