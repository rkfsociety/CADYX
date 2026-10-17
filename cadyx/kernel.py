"""Geometry-kernel boundary and CADYX kernel adapters."""

from __future__ import annotations

import json
import base64
import math
import zlib
from functools import lru_cache
from typing import Any, Callable, Protocol

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry, validate_mesh_geometry
from .native_kernel import NativeKernel


_MAX_OCCT_THREAD_SECTIONS = (1 << 31) - 2


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]: ...

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]: ...

    def create_extruded_profile_with_holes(
        self, profiles: list[list[dict[str, float]]], *, plane: str, depth: float,
        centered: bool = False,
    ) -> dict[str, Any]: ...

    def create_extruded_circle(
        self, center: dict[str, float], radius: float, *, plane: str, depth: float,
        centered: bool = False,
    ) -> dict[str, Any]: ...

    def translate_solid(self, geometry: dict[str, Any], offset: list[float]) -> dict[str, Any]: ...

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
    ) -> dict[str, Any]: ...
    def create_multicontour_loft(
        self, section_groups: list[list[list[dict[str, float]]]], *, section_offsets: list[float],
    ) -> dict[str, Any]: ...
    def create_ruled_shell(
        self, patches: list[dict[str, list[list[float]]]], *,
        sewing_tolerance: float = 1e-3, compute_volume: bool = True,
    ) -> dict[str, Any]: ...

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
        curve_points: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]: ...

    def fuse_profiles(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]: ...

    def cut_solids(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]: ...

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> dict[str, Any]: ...

    def create_metric_thread(
        self, *, major_diameter: float, pitch: float, length: float, handedness: str = "right",
    ) -> dict[str, Any]: ...

    def create_imported_step(self, source: bytes) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def extract_section_profile(
        self, geometry: dict[str, Any], *, normal_axis: str, offset: float, max_points: int,
    ) -> list[list[dict[str, float]]]: ...

    def export_delivery(
        self, geometry: dict[str, Any], *, include_stl: bool,
        linear_deflection: float, angular_deflection: float,
    ) -> dict[str, Any]: ...

    def modify_imported_brep(
        self, base: dict[str, Any], *, operation: str, parameters: list[float],
    ) -> dict[str, Any]: ...

    def create_fillet(
        self, base: dict[str, Any], *, edge_indices: list[int], radius: float,
    ) -> dict[str, Any]: ...

    def topology(self, geometry: dict[str, Any]) -> dict[str, Any]: ...

    def metrics(self, geometry: dict[str, Any]) -> dict[str, Any]: ...

    def minimum_distance(
        self, first: dict[str, Any], second: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def compare_preserved_volume(
        self, reference: dict[str, Any], candidate: dict[str, Any],
    ) -> dict[str, Any]: ...

    def sample_minimum_thickness(
        self, geometry: dict[str, Any], *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def certify_global_minimum_thickness(
        self, geometry: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def validate(self, geometry: dict[str, Any]) -> None: ...

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]: ...


def _box_dimensions(width: Any, height: Any, depth: Any) -> dict[str, float]:
    dimensions = {"width": width, "height": height, "depth": depth}
    normalized: dict[str, float] = {}
    for name, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{name} must be a number.")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValidationError(f"{name} must be a finite positive number.") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValidationError(f"{name} must be a finite positive number.")
        normalized[name] = number
    return normalized


def _profile_values(profile: Any, plane: Any) -> tuple[list[dict[str, float]], int]:
    if plane not in {"XY", "XZ"}:
        raise ValidationError("Profile plane must be XY or XZ.")
    if not isinstance(profile, list) or len(profile) < 3:
        raise ValidationError("A profile needs at least 3 points.")
    normalized = []
    for point in profile:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValidationError("Every profile point must contain x and y coordinates.")
        values = {}
        for name in ("x", "y"):
            value = point[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError("Profile coordinates must be finite numbers.")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValidationError("Profile coordinates must be finite numbers.") from exc
            if not math.isfinite(value):
                raise ValidationError("Profile coordinates must be finite numbers.")
            values[name] = value
        normalized.append(values)
    area = sum(
        point["x"] * normalized[(index + 1) % len(normalized)]["y"]
        - normalized[(index + 1) % len(normalized)]["x"] * point["y"]
        for index, point in enumerate(normalized)
    )
    if not math.isfinite(area) or abs(area) <= 1e-12:
        raise ValidationError("Profile area must be non-zero.")
    plane_code = 0 if plane == "XY" else 1
    return normalized, plane_code


def _spline_control_values(points: Any, plane: Any) -> list[dict[str, float]]:
    if plane not in {"XY", "XZ"} or not isinstance(points, list) or len(points) < 3:
        raise ValidationError("Closed spline profile controls are invalid.")
    normalized = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValidationError("Every closed spline control must contain x and y coordinates.")
        values = {}
        for name in ("x", "y"):
            value = point[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError("Closed spline control coordinates must be finite numbers.")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValidationError("Closed spline control coordinates must be finite numbers.") from exc
            if not math.isfinite(value):
                raise ValidationError("Closed spline control coordinates must be finite numbers.")
            values[name] = value
        normalized.append(values)
    if any(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= 1e-12
           for a, b in zip(normalized, normalized[1:] + normalized[:1])):
        raise ValidationError("Closed spline control vertices must be distinct.")
    return normalized


def _positive_amount(value: Any, name: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        amount = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{name} must be finite and positive.") from exc
    if not math.isfinite(amount) or amount <= 0 or (maximum is not None and amount > maximum):
        suffix = f" and no greater than {maximum:g}" if maximum is not None else ""
        raise ValidationError(f"{name} must be finite, positive{suffix}.")
    return amount


def _positive_or_signed_coordinate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValidationError("Pipe path coordinates must be finite numbers.") from exc
    if not math.isfinite(number):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    return number


def _finite_coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be finite.")
    return number


def _ruled_patch_values(patches: Any) -> list[dict[str, list[list[float]]]]:
    if not isinstance(patches, list) or len(patches) < 4:
        raise ValidationError("Ruled shell needs at least 4 surface patches.")
    normalized = []
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {"first", "second"}:
            raise ValidationError("Each ruled patch needs exactly two rail point arrays.")
        rails = {}
        for key in ("first", "second"):
            points = patch[key]
            if not isinstance(points, list) or len(points) < 2:
                raise ValidationError("Each ruled rail needs at least 2 points.")
            rail = []
            for point in points:
                if not isinstance(point, list) or len(point) != 3:
                    raise ValidationError("Ruled rail points must be [x, y, z] coordinate triples.")
                rail.append([_finite_coordinate(value, "Ruled rail coordinate") for value in point])
            if any(math.dist(a, b) <= 1e-7 for a, b in zip(rail, rail[1:])):
                raise ValidationError("Ruled rail points must be distinct and consecutive.")
            rails[key] = rail
        normalized.append(rails)
    return normalized


def _loft_contour_signature(profile: list[dict[str, float]]) -> tuple[float, float, float, float]:
    signed_twice_area = 0.0
    centroid_x = centroid_y = 0.0
    for index, point in enumerate(profile):
        following = profile[(index + 1) % len(profile)]
        cross = point["x"] * following["y"] - following["x"] * point["y"]
        signed_twice_area += cross
        centroid_x += (point["x"] + following["x"]) * cross
        centroid_y += (point["y"] + following["y"]) * cross
    area = abs(signed_twice_area) / 2
    if area <= 1e-12:
        raise ValidationError("Loft section area must be non-zero.")
    return area, centroid_x / (3 * signed_twice_area), centroid_y / (3 * signed_twice_area), signed_twice_area


def _match_loft_contours(
    left: list[tuple[list[dict[str, float]], float, float, float]],
    right: list[tuple[list[dict[str, float]], float, float, float]],
) -> list[tuple[int, int]]:
    """Pair contours by centroid proximity with area as a secondary shape cue."""
    all_points = [point for profile, _, _, _ in (*left, *right) for point in profile]
    span = max(
        max(point["x"] for point in all_points) - min(point["x"] for point in all_points),
        max(point["y"] for point in all_points) - min(point["y"] for point in all_points),
        1e-9,
    )

    def cost(a: tuple[list[dict[str, float]], float, float, float],
             b: tuple[list[dict[str, float]], float, float, float]) -> float:
        _, area_a, cx_a, cy_a = a
        _, area_b, cx_b, cy_b = b
        center_distance = math.hypot(cx_a - cx_b, cy_a - cy_b) / span
        area_change = abs(math.log(area_a / area_b))
        return center_distance + 0.25 * area_change

    left_is_shorter = len(left) <= len(right)
    short, long = (left, right) if left_is_shorter else (right, left)
    # Rectangular Hungarian assignment keeps matching polynomial as contour
    # counts grow, instead of the former bitmask search that scaled exponentially.
    row_count, column_count = len(short), len(long)
    costs = [[cost(source, target) for target in long] for source in short]
    row_potentials = [0.0] * (row_count + 1)
    column_potentials = [0.0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        matched_row[0] = row
        current_column = 0
        minimum_cost = [math.inf] * (column_count + 1)
        visited = [False] * (column_count + 1)
        while True:
            visited[current_column] = True
            current_row = matched_row[current_column]
            delta = math.inf
            next_column = 0
            for column in range(1, column_count + 1):
                if visited[column]:
                    continue
                reduced_cost = (costs[current_row - 1][column - 1]
                                - row_potentials[current_row] - column_potentials[column])
                if reduced_cost < minimum_cost[column]:
                    minimum_cost[column] = reduced_cost
                    previous_column[column] = current_column
                if minimum_cost[column] < delta:
                    delta = minimum_cost[column]
                    next_column = column
            for column in range(column_count + 1):
                if visited[column]:
                    row_potentials[matched_row[column]] += delta
                    column_potentials[column] -= delta
                else:
                    minimum_cost[column] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            prior = previous_column[current_column]
            matched_row[current_column] = matched_row[prior]
            current_column = prior
            if current_column == 0:
                break
    assignment = [0] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column]:
            assignment[matched_row[column] - 1] = column - 1
    pairs = ([(i, j) for i, j in enumerate(assignment)] if left_is_shorter
             else [(j, i) for i, j in enumerate(assignment)])
    matched_left = {i for i, _ in pairs}
    matched_right = {j for _, j in pairs}
    for left_index in range(len(left)):
        if left_index not in matched_left:
            right_index = min(range(len(right)), key=lambda j: cost(left[left_index], right[j]))
            pairs.append((left_index, right_index))
    for right_index in range(len(right)):
        if right_index not in matched_right:
            left_index = min(range(len(left)), key=lambda i: cost(left[i], right[right_index]))
            pairs.append((left_index, right_index))
    return pairs


def _resample_loft_profile(profile: list[dict[str, float]], count: int) -> list[dict[str, float]]:
    points = [(point["x"], point["y"]) for point in profile]
    area = sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    lengths = [math.hypot(points[(i + 1) % len(points)][0] - point[0],
                          points[(i + 1) % len(points)][1] - point[1])
               for i, point in enumerate(points)]
    perimeter = sum(lengths)
    if not math.isfinite(perimeter) or perimeter <= 1e-7:
        raise ValidationError("Loft section perimeter must be finite and positive.")
    samples: list[dict[str, float]] = []
    edge = 0
    start = 0.0
    for index in range(count):
        distance = perimeter * index / count
        while edge < len(lengths) - 1 and start + lengths[edge] < distance:
            start += lengths[edge]
            edge += 1
        ratio = (distance - start) / lengths[edge]
        first, second = points[edge], points[(edge + 1) % len(points)]
        samples.append({"x": first[0] + ratio * (second[0] - first[0]),
                        "y": first[1] + ratio * (second[1] - first[1])})
    anchor = min(range(count), key=lambda i: (samples[i]["x"], samples[i]["y"]))
    return samples[anchor:] + samples[:anchor]


def _canonical_loft_profile(profile: list[dict[str, float]]) -> list[dict[str, float]]:
    points = list(profile)
    area = sum(points[i]["x"] * points[(i + 1) % len(points)]["y"]
               - points[(i + 1) % len(points)]["x"] * points[i]["y"]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    anchor = min(range(len(points)), key=lambda i: (points[i]["x"], points[i]["y"]))
    return points[anchor:] + points[:anchor]


def _align_loft_profile(
    profile: list[dict[str, float]], reference: list[dict[str, float]],
) -> list[dict[str, float]]:
    """Rotate a resampled section to keep its vertices aligned with the prior section."""
    if len(profile) != len(reference):
        raise ValidationError("Loft sections must have matching sample counts before alignment.")
    count = len(profile)
    stride = max(1, count // 64)
    sample_indices = list(range(0, count, stride))

    def squared_error(shift: int, indices: list[int]) -> float:
        return sum(
            (profile[(index + shift) % count]["x"] - reference[index]["x"]) ** 2
            + (profile[(index + shift) % count]["y"] - reference[index]["y"]) ** 2
            for index in indices
        )

    coarse = min(range(count), key=lambda shift: squared_error(shift, sample_indices))
    candidates = {(coarse + delta) % count for delta in range(-stride, stride + 1)}
    anchor = min(candidates, key=lambda shift: squared_error(shift, list(range(count))))
    return profile[anchor:] + profile[:anchor]


class OCCTKernelAdapter:
    """Build and validate box B-reps with Open CASCADE Technology."""

    kernel_id = "occt-v1"
    _accepted_kernel_ids = {kernel_id, "primitive-v1"}

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        """Return kernel-derived triangles in model coordinates, without native objects."""
        # Mesh tessellation already verifies the compressed payload while decoding it.
        # Avoid serializing and validating a large scan mesh a second time here.
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            return tessellate_mesh_geometry(geometry)
        self.validate(geometry)
        result = NativeKernel().geometry_graph(geometry, mesh=True)
        mesh = {key: result[key] for key in (
            "positions", "triangles", "edge_positions", "edge_lines", "face_triangle_ranges",
        )}
        selected_faces = geometry.get("selected_face_indices", result.get("generated_face_indices", []))
        mesh["selected_face_indices"] = selected_faces if isinstance(selected_faces, list) else []
        return mesh

    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]:
        dimensions = _box_dimensions(width, height, depth)
        volume = self._box_volume(dimensions)
        return {
            "kernel": self.kernel_id,
            "kind": "box",
            "dimensions": dimensions,
            "volume": volume,
        }

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        return self._create_profile_geometry(profile, plane=plane, operation=0,
                                             amount=_positive_amount(depth, "depth"), centered=centered,
                                             curve_points=curve_points)

    def create_extruded_profile_with_holes(
        self, profiles: list[list[dict[str, float]]], *, plane: str, depth: float,
        centered: bool = False,
    ) -> dict[str, Any]:
        if type(centered) is not bool or not isinstance(profiles, list) or len(profiles) < 2:
            raise ValidationError("A multi-contour extrusion needs an outer contour and at least one hole.")
        normalized = [_profile_values(profile, plane)[0] for profile in profiles]
        def area(profile):
            return sum(point["x"] * profile[(i + 1) % len(profile)]["y"]
                       - profile[(i + 1) % len(profile)]["x"] * point["y"]
                       for i, point in enumerate(profile))
        outer_index = max(range(len(normalized)), key=lambda index: abs(area(normalized[index])))
        outer = normalized[outer_index]
        holes = [profile for index, profile in enumerate(normalized) if index != outer_index]
        def inside(point, contour):
            x, y, contained = point["x"], point["y"], False
            for index, first in enumerate(contour):
                second = contour[(index + 1) % len(contour)]
                if (first["y"] > y) != (second["y"] > y):
                    crossing = (second["x"] - first["x"]) * (y - first["y"]) / (second["y"] - first["y"]) + first["x"]
                    if x < crossing:
                        contained = not contained
            return contained
        if any(not all(inside(point, outer) for point in hole) for hole in holes):
            raise ValidationError("Every inner profile contour must lie completely inside the outer contour.")
        def circle(profile):
            if len(profile) < 16:
                return None
            x = sum(point["x"] for point in profile) / len(profile)
            y = sum(point["y"] for point in profile) / len(profile)
            radii = [math.hypot(point["x"] - x, point["y"] - y) for point in profile]
            radius = sum(radii) / len(radii)
            if radius <= 1e-12 or max(radii) - min(radii) > max(1e-7, radius * 1e-6):
                return None
            return {"x": x, "y": y}, radius
        amount = _positive_amount(depth, "depth")
        outer_circle = circle(outer)
        solid = (self.create_extruded_circle(outer_circle[0], outer_circle[1], plane=plane,
                                             depth=amount, centered=centered) if outer_circle else
                 self.create_extruded_profile(outer, plane=plane, depth=amount, centered=centered))
        for hole in holes:
            hole_circle = circle(hole)
            tool = (self.create_extruded_circle(hole_circle[0], hole_circle[1], plane=plane,
                                                depth=amount, centered=centered) if hole_circle else
                    self.create_extruded_profile(hole, plane=plane, depth=amount, centered=centered))
            solid = self.cut_solids(solid, tool)
        geometry = {"kernel": self.kernel_id, "kind": "profile_with_holes", "plane": plane,
                    "profile": outer, "holes": holes, "depth": amount, "solid": solid,
                    "volume": solid["volume"]}
        if centered:
            geometry["centered"] = True
        return geometry

    def create_extruded_circle(
        self, center: dict[str, float], radius: float, *, plane: str, depth: float,
        centered: bool = False,
    ) -> dict[str, Any]:
        if plane not in {"XY", "XZ"} or type(centered) is not bool:
            raise ValidationError("An analytic circular extrusion needs an XY or XZ plane and boolean centered state.")
        if (not isinstance(center, dict) or set(center) != {"x", "y"}
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in center.values())):
            raise ValidationError("Circle center must have finite x and y coordinates.")
        radius = _positive_amount(radius, "radius")
        depth = _positive_amount(depth, "depth")
        geometry = {"kernel": self.kernel_id, "kind": "extruded_circle", "plane": plane,
                    "center": {"x": float(center["x"]), "y": float(center["y"])},
                    "radius": radius, "depth": depth,
                    "volume": math.pi * radius * radius * depth}
        if centered:
            geometry["centered"] = True
        return geometry

    def translate_solid(self, geometry: dict[str, Any], offset: list[float]) -> dict[str, Any]:
        self.validate(geometry)
        if (not isinstance(offset, list) or len(offset) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in offset)):
            raise ValidationError("A solid translation needs three finite coordinates.")
        native = NativeKernel()
        brep, metrics = native.export_geometry(geometry)
        source = {"kernel": self.kernel_id, "kind": "imported_brep",
                  "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
                  "volume": metrics["volume"], "brep_valid": True}
        return self.modify_imported_brep(source, operation="rigid_transform",
                                         parameters=[0.0, 0.0, 1.0, 0.0, *offset])

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
        curve_points: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        angle = _positive_amount(angle_degrees, "angle_degrees", maximum=360.0)
        return self._create_profile_geometry(profile, plane="XZ", operation=1,
                                             amount=math.radians(angle), angle_degrees=angle,
                                             curve_points=curve_points)

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
        alignment: str = "minimum-displacement-v2",
        compute_volume: bool = True,
    ) -> dict[str, Any]:
        if (not isinstance(sections, list) or len(sections) < 2
                or not isinstance(section_offsets, list) or len(section_offsets) != len(sections)):
            raise ValidationError("A loft needs at least two paired sketch sections and offsets.")
        if alignment not in {
                "independent-start-v1", "minimum-displacement-v1", "minimum-displacement-v2"}:
            raise ValidationError("Loft profile alignment mode is unsupported.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        raw_sections = []
        offsets = []
        for section, offset in zip(sections, section_offsets, strict=True):
            points, _ = _profile_values(section, "XZ")
            raw_sections.append(points)
            offsets.append(_finite_coordinate(offset, "Loft section offset"))
        point_count = max(len(section) for section in raw_sections)
        if alignment == "independent-start-v1":
            if all(len(section) == point_count for section in raw_sections):
                normalized = [_canonical_loft_profile(section) for section in raw_sections]
            else:
                normalized = [_resample_loft_profile(section, point_count) for section in raw_sections]
        else:
            normalized = []
            for section in raw_sections:
                profile = (_resample_loft_profile(section, point_count)
                           if len(section) != point_count else list(section))
                area = sum(profile[i]["x"] * profile[(i + 1) % len(profile)]["y"]
                           - profile[(i + 1) % len(profile)]["x"] * profile[i]["y"]
                           for i in range(len(profile)))
                if area < 0:
                    profile.reverse()
                if normalized:
                    reference = normalized[0] if alignment == "minimum-displacement-v2" else normalized[-1]
                    profile = _align_loft_profile(profile, reference)
                else:
                    profile = _canonical_loft_profile(profile)
                normalized.append(profile)
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")
        payload = {"kernel": self.kernel_id, "kind": "lofted_profiles", "sections": normalized,
                   "section_offsets": offsets, "profile_alignment": alignment}
        payload["volume"] = NativeKernel().geometry_graph(payload)["volume"] if compute_volume else 0.0
        return payload

    def create_multicontour_loft(
        self, section_groups: list[list[list[dict[str, float]]]], *, section_offsets: list[float],
        compute_volume: bool = True,
    ) -> dict[str, Any]:
        """Build and fuse adjacent-station loft branches when section contour counts change."""
        if (not isinstance(section_groups, list) or len(section_groups) < 2
                or not isinstance(section_offsets, list) or len(section_offsets) != len(section_groups)):
            raise ValidationError("A grouped loft needs at least two paired stations and offsets.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        offsets = [_finite_coordinate(value, "Loft section offset") for value in section_offsets]
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")

        normalized_groups: list[list[tuple[list[dict[str, float]], float, float, float]]] = []
        for group in section_groups:
            if not isinstance(group, list) or not group:
                raise ValidationError("Each loft station needs one or more closed contour profiles.")
            normalized_group = []
            for section in group:
                points, _ = _profile_values(section, "XZ")
                area, center_x, center_y, _ = _loft_contour_signature(points)
                normalized_group.append((points, area, center_x, center_y))
            normalized_groups.append(normalized_group)

        all_branches = []
        for index, (left, right) in enumerate(zip(normalized_groups, normalized_groups[1:], strict=False)):
            section_branches = _match_loft_contours(left, right)
            for left_index, right_index in section_branches:
                left_profile, right_profile = left[left_index], right[right_index]
                loft = self.create_lofted_profiles(
                    [left_profile[0], right_profile[0]],
                    section_offsets=[offsets[index], offsets[index + 1]],
                    compute_volume=False,
                )
                all_branches.append(loft)
        if not all_branches:
            raise ValidationError("Grouped loft produced no branch geometry.")
        result = {"kernel": self.kernel_id, "kind": "multicontour_loft",
                  "branches": all_branches, "volume": 0.0}
        result["volume"] = (NativeKernel().geometry_graph(result)["volume"]
                             if compute_volume else 0.0)
        return result

    def create_ruled_shell(
        self, patches: list[dict[str, list[list[float]]]], *,
        sewing_tolerance: float = 1e-3, compute_volume: bool = True,
    ) -> dict[str, Any]:
        normalized = _ruled_patch_values(patches)
        tolerance = _finite_coordinate(sewing_tolerance, "Ruled shell sewing tolerance")
        if not 1e-7 <= tolerance <= 1.0:
            raise ValidationError("Ruled shell sewing tolerance must be between 1e-7 and 1 mm.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        result = {"kernel": self.kernel_id, "kind": "ruled_shell", "patches": normalized,
                  "sewing_tolerance": tolerance, "volume": 0.0}
        if compute_volume:
            result["volume"] = NativeKernel().geometry_graph(result)["volume"]
        return result

    @staticmethod
    def _create_profile_geometry(
        profile: list[dict[str, float]], *, plane: str, operation: int,
        amount: float, angle_degrees: float | None = None, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        normalized, _ = _profile_values(profile, plane)
        amount = _positive_amount(amount, "operation amount")
        if curve_points is None:
            result = NativeKernel().profile(normalized, operation=operation,
                                            plane=plane, amount=amount, centered=centered)
        else:
            curve_points = _spline_control_values(curve_points, plane)
            descriptor = {"kind": "extruded_profile" if operation == 0 else "revolved_profile",
                          "profile": normalized, "profile_curve": "closed_periodic_bspline",
                          "profile_curve_points": curve_points, "plane": plane, "centered": centered}
            descriptor["depth" if operation == 0 else "angle_degrees"] = (
                amount if operation == 0 else angle_degrees)
            result = NativeKernel().geometry_graph(descriptor)
        geometry = {
            "kernel": OCCTKernelAdapter.kernel_id,
            "kind": "extruded_profile" if operation == 0 else "revolved_profile",
            "plane": plane,
            "profile": normalized,
            "volume": result["volume"],
        }
        if curve_points is not None:
            geometry["profile_curve"] = "closed_periodic_bspline"
            geometry["profile_curve_points"] = curve_points
        if operation == 0:
            geometry["depth"] = amount
            if centered:
                geometry["centered"] = True
        else:
            geometry["angle_degrees"] = angle_degrees
        return geometry

    def fuse_profiles(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
        self.validate(base)
        self.validate(tool)
        kind = "fused_profiles" if base.get("kind") in {"extruded_profile", "revolved_profile"} \
            and tool.get("kind") in {"extruded_profile", "revolved_profile"} else "fused_solids"
        result = NativeKernel().geometry_graph({"kind": kind, "base": base, "tool": tool})
        return {
            "kernel": self.kernel_id,
            "kind": kind,
            "base": base,
            "tool": tool,
            "volume": result["volume"],
        }

    def cut_solids(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
        self.validate(base)
        self.validate(tool)
        result = NativeKernel().geometry_graph({"kind": "cut_solids", "base": base, "tool": tool})
        if result["volume"] >= base["volume"] - max(1e-9, base["volume"] * 1e-12):
            raise ValidationError("Boolean cut must remove material from the base solid.")
        return {"kernel": self.kernel_id, "kind": "cut_solids", "base": base,
                "tool": tool, "volume": result["volume"]}

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> dict[str, Any]:
        if plane not in {"XY", "XZ"} or not isinstance(path, list) or len(path) < 3:
            raise ValidationError("A pipe path needs at least 3 points on an XY or XZ sketch.")
        normalized: list[dict[str, float]] = []
        for point in path:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValidationError("Every pipe path point must contain x and y coordinates.")
            values = {axis: _positive_or_signed_coordinate(point[axis]) for axis in ("x", "y")}
            if normalized and math.hypot(values["x"] - normalized[-1]["x"],
                                         values["y"] - normalized[-1]["y"]) <= 1e-7:
                raise ValidationError("Pipe path points must have distinct consecutive endpoints.")
            normalized.append(values)
        radius = _positive_amount(radius, "radius")
        geometry = {"kernel": self.kernel_id, "kind": "swept_pipe", "plane": plane,
                    "path": normalized, "radius": radius}
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def create_metric_thread(
        self, *, major_diameter: float, pitch: float, length: float, handedness: str = "right",
    ) -> dict[str, Any]:
        major_diameter = _positive_amount(major_diameter, "major_diameter")
        pitch = _positive_amount(pitch, "pitch")
        length = _positive_amount(length, "length")
        if length < pitch:
            raise ValidationError("Thread length must be at least one pitch.")
        center_span = length - 2.0 * (5.0 * pitch / 12.0)
        section_count_value = (center_span / pitch) * 24.0
        if not math.isfinite(section_count_value):
            raise ValidationError("Thread length exceeds the section count representable by Open CASCADE.")
        section_count = math.ceil(section_count_value)
        if section_count > _MAX_OCCT_THREAD_SECTIONS:
            raise ValidationError("Thread length exceeds the section count representable by Open CASCADE.")
        if major_diameter <= 1.226869 * pitch:
            raise ValidationError("major_diameter must leave a positive ISO metric thread minor diameter.")
        if not isinstance(handedness, str) or handedness not in {"right", "left"}:
            raise ValidationError("handedness must be 'right' or 'left'.")
        geometry = {
            "kernel": self.kernel_id,
            "kind": "metric_thread",
            "major_diameter": major_diameter,
            "pitch": pitch,
            "length": length,
            "handedness": handedness,
        }
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def create_imported_step(self, source: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
        imported = NativeKernel().import_step(source)
        try:
            brep = base64.b64decode(imported["brep_data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("OCCT returned an invalid imported B-rep.") from exc
        geometry = {
            "kernel": self.kernel_id,
            "kind": "imported_brep",
            "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
            "volume": imported["volume"],
            "brep_valid": imported["brep_valid"],
        }
        metrics = {key: imported[key] for key in (
            "volume", "surface_area", "bounds", "topology", "brep_valid", "repaired",
        )}
        roundtrip = NativeKernel().geometry_graph(geometry)
        bounds_match = all(
            math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
            for side in ("min", "max")
            for a, b in zip(roundtrip["bounds"][side], metrics["bounds"][side], strict=True)
        )
        if (roundtrip["topology"] != metrics["topology"]
                or not bounds_match
                or roundtrip["brep_valid"] != metrics["brep_valid"]
                or not math.isclose(roundtrip["volume"], metrics["volume"], rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(roundtrip["surface_area"], metrics["surface_area"], rel_tol=1e-12, abs_tol=1e-12)):
            raise ValidationError("Imported B-rep did not preserve the STEP model geometry.")
        return geometry, metrics

    def extract_section_profile(
        self, geometry: dict[str, Any], *, normal_axis: str, offset: float, max_points: int,
    ) -> list[list[dict[str, float]]]:
        if not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep":
            raise ValidationError("Section extraction requires an imported OCCT B-rep.")
        try:
            compressed = base64.b64decode(geometry["data"], validate=True)
            decoder = zlib.decompressobj()
            brep = decoder.decompress(compressed, 64 * 1024 * 1024 + 1)
            if (len(brep) > 64 * 1024 * 1024 or not decoder.eof
                    or decoder.unused_data or decoder.unconsumed_tail):
                raise ValueError("invalid compressed B-rep payload")
        except (KeyError, TypeError, ValueError, zlib.error) as exc:
            raise ValidationError("Imported B-rep data is invalid or exceeds the 64 MB limit.") from exc
        result = NativeKernel().section_profile(
            brep, normal_axis=normal_axis, offset=offset, max_points=max_points,
        )
        profiles = result.get("profiles") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get("closed") is not True
                or not isinstance(profiles, list) or not profiles
                or sum(len(profile) for profile in profiles if isinstance(profile, list)) > max_points
                or any(not isinstance(profile, list) or len(profile) < 3
                       or any(not isinstance(point, dict) or set(point) != {"x", "y"}
                              or any(isinstance(point[key], bool) or not isinstance(point[key], (int, float))
                                     or not math.isfinite(point[key]) for key in ("x", "y"))
                              for point in profile)
                       for profile in profiles)):
            raise ValidationError("OCCT returned invalid closed section profiles.")
        return profiles

    def export_delivery(
        self,
        geometry: dict[str, Any],
        *,
        include_stl: bool,
        linear_deflection: float = 0.1,
        angular_deflection: float = 0.5,
    ) -> dict[str, Any]:
        if type(include_stl) is not bool:
            raise ValidationError("stl must be a boolean.")
        linear_deflection = _positive_amount(linear_deflection, "linear_deflection")
        angular_deflection = _positive_amount(angular_deflection, "angular_deflection", maximum=1.0)
        if linear_deflection < 0.001 or angular_deflection < 0.01:
            raise ValidationError("STL deflections must be at least 0.001 linear and 0.01 radians.")
        self.validate(geometry)
        native = NativeKernel()
        brep, source_metrics = native.export_geometry(geometry)
        result: dict[str, Any] = {
            "step": native.export_step_brep(brep),
            "stl": None,
            "metrics": source_metrics,
            "stl_metrics": None,
            "kernel_version": native.version,
        }
        if include_stl:
            result["stl"], result["stl_metrics"] = native.export_stl_brep(
                brep, linear_deflection=linear_deflection, angular_deflection=angular_deflection,
            )
        return result

    def modify_imported_brep(
        self, base: dict[str, Any], *, operation: str, parameters: list[float],
    ) -> dict[str, Any]:
        if not isinstance(base, dict) or base.get("kind") != "imported_brep":
            raise ValidationError("Only imported OCCT B-reps can be modified.")
        modified = NativeKernel().modify_imported_brep(
            base, operation=operation, parameters=parameters,
        )
        try:
            brep = base64.b64decode(modified["brep_data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("OCCT returned an invalid modified B-rep.") from exc
        if (not brep or len(brep) > 64 * 1024 * 1024
                or modified.get("brep_valid") is not True):
            raise ValidationError("OCCT returned an invalid or oversized modified B-rep.")
        return {
            "kernel": self.kernel_id,
            "kind": "imported_brep",
            "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
            "volume": modified["volume"],
            "brep_valid": True,
        }

    def create_fillet(
        self, base: dict[str, Any], *, edge_indices: list[int], radius: float,
    ) -> dict[str, Any]:
        self.validate(base)
        if (not isinstance(edge_indices, list) or not edge_indices
                or any(type(index) is not int or index < 0 for index in edge_indices)
                or len(set(edge_indices)) != len(edge_indices)):
            raise ValidationError("Fillet needs distinct non-negative edge indices.")
        radius = _positive_amount(radius, "radius")
        geometry = {"kernel": self.kernel_id, "kind": "filleted_solid", "base": base,
                    "edge_indices": list(edge_indices), "radius": radius}
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def topology(self, geometry: dict[str, Any]) -> dict[str, Any]:
        from .advanced_topology import enrich_topology

        result = NativeKernel().geometry_graph(geometry, topology=True)
        self._same_saved_volume(geometry, result["volume"])
        return enrich_topology(geometry, result)

    def metrics(self, geometry: dict[str, Any]) -> dict[str, Any]:
        result = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, result["volume"])
        return {key: result[key] for key in (
            "volume", "surface_area", "bounds", "topology", "brep_valid",
        )}

    def minimum_distance(
        self, first: dict[str, Any], second: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if any(isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1"
               for geometry in (first, second)):
            raise ValidationError("Minimum solid clearance requires editable OCCT B-rep bodies, not triangle meshes.")
        self.validate(first)
        self.validate(second)
        native = NativeKernel()
        first_brep, _ = native.export_geometry(first)
        second_brep, _ = native.export_geometry(second)
        return native.minimum_brep_distance(first_brep, second_brep, progress=progress)

    def compare_preserved_volume(
        self, reference: dict[str, Any], candidate: dict[str, Any],
    ) -> dict[str, Any]:
        if any(isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1"
               for geometry in (reference, candidate)):
            raise ValidationError("Preserved-volume validation requires editable OCCT B-rep bodies, not triangle meshes.")
        self.validate(reference)
        self.validate(candidate)
        native = NativeKernel()
        reference_brep, _ = native.export_geometry(reference)
        candidate_brep, _ = native.export_geometry(candidate)
        return native.brep_preservation(reference_brep, candidate_brep)

    def sample_minimum_thickness(
        self, geometry: dict[str, Any], *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("Wall-thickness sampling requires an editable OCCT B-rep, not a triangle mesh.")
        self.validate(geometry)
        native = NativeKernel()
        brep, _ = native.export_geometry(geometry)
        return native.brep_thickness(brep, minimum_thickness, tolerance, progress=progress)

    def certify_global_minimum_thickness(
        self, geometry: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("Global wall-thickness certification requires an editable OCCT B-rep, not a triangle mesh.")
        self.validate(geometry)
        native = NativeKernel()
        brep, _ = native.export_geometry(geometry)
        return native.brep_global_thickness(brep, progress=progress)

    def validate(self, geometry: dict[str, Any]) -> None:
        try:
            descriptor = json.dumps(geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Geometry does not match a supported box format.") from exc
        self._validate_cached(descriptor)

    @lru_cache(maxsize=128)
    def _validate_cached(self, descriptor: str) -> None:
        self._validate_uncached(json.loads(descriptor))

    def _validate_uncached(self, geometry: dict[str, Any]) -> None:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            validate_mesh_geometry(geometry)
            return
        if not isinstance(geometry, dict):
            raise ValidationError("Geometry does not match a supported box format.")
        kernel_id = geometry.get("kernel")
        if not isinstance(kernel_id, str) or kernel_id not in self._accepted_kernel_ids:
            raise ValidationError("Geometry does not match a supported box format.")
        if kernel_id == self.kernel_id and geometry.get("kind") in {"extruded_profile", "revolved_profile"}:
            self._validate_profile_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "profile_with_holes":
            self._validate_profile_with_holes_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "extruded_circle":
            self._validate_extruded_circle_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "lofted_profiles":
            self._validate_loft_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "multicontour_loft":
            self._validate_multicontour_loft_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "ruled_shell":
            self._validate_ruled_shell_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "fused_profiles":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "fused_solids":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "cut_solids":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "swept_pipe":
            self._validate_swept_pipe_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "metric_thread":
            self._validate_metric_thread_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "imported_brep":
            self._validate_imported_brep_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "filleted_solid":
            self._validate_filleted_geometry(geometry)
            return
        if set(geometry) != {"kernel", "kind", "dimensions", "volume"} or geometry.get("kind") != "box":
            raise ValidationError("Geometry does not match a supported box format.")

        dimensions = geometry.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != {"width", "height", "depth"}:
            raise ValidationError("Geometry dimensions are missing or invalid.")
        normalized = _box_dimensions(
            width=dimensions["width"],
            height=dimensions["height"],
            depth=dimensions["depth"],
        )
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Geometry volume is invalid.")

        actual_volume = self._box_volume(normalized)
        if not math.isclose(saved_volume, actual_volume, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    @staticmethod
    def _validate_profile_geometry(geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict):
            raise ValidationError("Profile geometry descriptor is invalid.")
        kind = geometry.get("kind")
        if kind not in {"extruded_profile", "revolved_profile"}:
            raise ValidationError("Profile geometry descriptor is invalid.")
        amount_field = "depth" if kind == "extruded_profile" else "angle_degrees"
        expected_fields = {"kernel", "kind", "plane", "profile", "volume", amount_field}
        if kind == "extruded_profile" and "centered" in geometry:
            expected_fields.add("centered")
        curve_type = geometry.get("profile_curve")
        curved = curve_type in {"closed_catmull_rom", "closed_periodic_bspline"}
        if curved:
            expected_fields.update({"profile_curve", "profile_curve_points"})
        if set(geometry) != expected_fields or geometry.get("kernel") != OCCTKernelAdapter.kernel_id:
            raise ValidationError("Profile geometry descriptor is invalid.")
        profile, _ = _profile_values(geometry["profile"], geometry["plane"])
        if kind == "extruded_profile":
            if geometry["plane"] not in {"XY", "XZ"}:
                raise ValidationError("Profile plane must be XY or XZ.")
            centered = geometry.get("centered", False)
            if type(centered) is not bool:
                raise ValidationError("centered must be a boolean.")
            operation, amount = 0, _positive_amount(geometry["depth"], "depth")
        else:
            if geometry["plane"] != "XZ":
                raise ValidationError("Revolution requires an XZ profile.")
            if "centered" in geometry:
                raise ValidationError("Revolution profiles cannot be centered.")
            angle = _positive_amount(geometry["angle_degrees"], "angle_degrees", maximum=360.0)
            operation, amount = 1, math.radians(angle)
            centered = False
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Saved geometry volume is invalid.")
        if curved:
            _spline_control_values(geometry["profile_curve_points"], geometry["plane"])
            graph = dict(geometry)
            graph.pop("kernel", None)
            graph.pop("volume", None)
            actual = NativeKernel().geometry_graph(graph)["volume"]
        else:
            actual = NativeKernel().profile(
                profile, operation=operation, plane=geometry["plane"], amount=amount, centered=centered,
            )["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    def _validate_profile_with_holes_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) not in ({"kernel", "kind", "plane", "profile", "holes", "depth", "solid", "volume"},
                                  {"kernel", "kind", "plane", "profile", "holes", "depth", "solid", "volume", "centered"})
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "profile_with_holes"
                or not isinstance(geometry.get("holes"), list) or not geometry["holes"]
                or not isinstance(geometry.get("solid"), dict)):
            raise ValidationError("Multi-contour extrusion geometry is invalid.")
        centered = geometry.get("centered", False)
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        expected = self.create_extruded_profile_with_holes(
            [geometry["profile"], *geometry["holes"]], plane=geometry["plane"],
            depth=geometry["depth"], centered=centered,
        )
        comparable = dict(geometry)
        expected.pop("volume", None)
        comparable.pop("volume", None)
        if expected != comparable:
            raise ValidationError("Saved multi-contour extrusion does not match its source profiles.")

    def _validate_extruded_circle_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) not in ({"kernel", "kind", "plane", "center", "radius", "depth", "volume"},
                                  {"kernel", "kind", "plane", "center", "radius", "depth", "volume", "centered"})
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "extruded_circle"
                or geometry.get("plane") not in {"XY", "XZ"}):
            raise ValidationError("Analytic circular extrusion geometry is invalid.")
        center = geometry.get("center")
        if (not isinstance(center, dict) or set(center) != {"x", "y"}
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in center.values())):
            raise ValidationError("Analytic circular extrusion center is invalid.")
        radius = _positive_amount(geometry["radius"], "radius")
        depth = _positive_amount(geometry["depth"], "depth")
        centered = geometry.get("centered", False)
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        expected = self.create_extruded_circle(center, radius, plane=geometry["plane"], depth=depth,
                                               centered=centered)
        expected.pop("volume", None)
        comparable = dict(geometry)
        comparable.pop("volume", None)
        if expected != comparable:
            raise ValidationError("Analytic circular extrusion parameters do not match its saved geometry.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        if not math.isclose(geometry["volume"], actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved analytic circular extrusion volume is invalid.")

    def _validate_fused_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "tool", "volume"}
                or geometry.get("kernel") != self.kernel_id
                or geometry.get("kind") not in {"fused_profiles", "fused_solids", "cut_solids"}):
            raise ValidationError("Boolean solid geometry descriptor is invalid.")
        base, tool = geometry.get("base"), geometry.get("tool")
        if not isinstance(base, dict) or not isinstance(tool, dict):
            raise ValidationError("Fused solid source geometries are invalid.")
        self.validate(base)
        self.validate(tool)
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Saved geometry volume is invalid.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE fused solid.")

    def _validate_loft_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) not in (
                    {"kernel", "kind", "sections", "section_offsets", "volume"},
                    {"kernel", "kind", "sections", "section_offsets", "volume", "profile_alignment"},
                )
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "lofted_profiles"):
            raise ValidationError("Loft geometry descriptor is invalid.")
        rebuilt = self.create_lofted_profiles(
            geometry.get("sections"), section_offsets=geometry.get("section_offsets"),
            alignment=geometry.get("profile_alignment", "independent-start-v1"),
        )
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_multicontour_loft_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "branches", "volume"}
                or geometry.get("kind") != "multicontour_loft"
                or not isinstance(geometry.get("branches"), list) or len(geometry["branches"]) < 2
                or any(not isinstance(branch, dict) or branch.get("kind") != "lofted_profiles"
                       for branch in geometry["branches"])):
            raise ValidationError("Multi-contour loft geometry descriptor is invalid.")
        for branch in geometry["branches"]:
            if (set(branch) != {"kernel", "kind", "sections", "section_offsets", "profile_alignment", "volume"}
                    or branch.get("kernel") != self.kernel_id
                    or not isinstance(branch.get("sections"), list) or len(branch["sections"]) != 2
                    or not isinstance(branch.get("section_offsets"), list)
                    or len(branch["section_offsets"]) != 2
                    or branch.get("profile_alignment") not in {
                        "independent-start-v1", "minimum-displacement-v1", "minimum-displacement-v2"}):
                raise ValidationError("Multi-contour loft branch descriptor is invalid.")
            for profile in branch["sections"]:
                _profile_values(profile, "XZ")
            left, right = (_finite_coordinate(value, "Loft section offset")
                           for value in branch["section_offsets"])
            if right <= left:
                raise ValidationError("Loft section offsets must be strictly increasing.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        self._same_saved_volume(geometry, actual)

    def _validate_ruled_shell_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "patches", "sewing_tolerance", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "ruled_shell"):
            raise ValidationError("Ruled shell geometry descriptor is invalid.")
        patches = _ruled_patch_values(geometry.get("patches"))
        tolerance = _finite_coordinate(geometry.get("sewing_tolerance"), "Ruled shell sewing tolerance")
        if not 1e-7 <= tolerance <= 1.0:
            raise ValidationError("Ruled shell sewing tolerance must be between 1e-7 and 1 mm.")
        rebuilt = {"kernel": self.kernel_id, "kind": "ruled_shell", "patches": patches,
                   "sewing_tolerance": tolerance, "volume": 0.0}
        actual = NativeKernel().geometry_graph(rebuilt)["volume"]
        self._same_saved_volume(geometry, actual)

    def _validate_swept_pipe_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "plane", "path", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "swept_pipe"):
            raise ValidationError("Swept pipe geometry descriptor is invalid.")
        rebuilt = self.create_swept_pipe(geometry.get("path"), plane=geometry.get("plane"),
                                         radius=geometry.get("radius"))
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_metric_thread_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "major_diameter", "pitch", "length", "handedness", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "metric_thread"):
            raise ValidationError("Metric thread geometry descriptor is invalid.")
        rebuilt = self.create_metric_thread(
            major_diameter=geometry.get("major_diameter"),
            pitch=geometry.get("pitch"),
            length=geometry.get("length"),
            handedness=geometry.get("handedness"),
        )
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_imported_brep_geometry(self, geometry: dict[str, Any]) -> None:
        allowed = {"kernel", "kind", "data", "volume", "brep_valid", "selected_face_indices"}
        required = {"kernel", "kind", "data", "volume", "brep_valid"}
        if (not set(geometry) <= allowed or not required <= set(geometry)
                or geometry.get("kernel") != self.kernel_id
                or geometry.get("kind") != "imported_brep"
                or not isinstance(geometry.get("data"), str)
                or type(geometry.get("brep_valid")) is not bool):
            raise ValidationError("Imported B-rep geometry descriptor is invalid.")
        selected_faces = geometry.get("selected_face_indices", [])
        if (not isinstance(selected_faces, list)
                or any(type(index) is not int or index < 0 for index in selected_faces)
                or len(set(selected_faces)) != len(selected_faces)):
            raise ValidationError("Imported B-rep selected-face metadata is invalid.")
        actual = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, actual["volume"])
        if actual["brep_valid"] != geometry["brep_valid"]:
            raise ValidationError("Imported B-rep validity flag does not match the Open CASCADE shape.")

    def _validate_filleted_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "edge_indices", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "filleted_solid"):
            raise ValidationError("Filleted solid geometry descriptor is invalid.")
        self.validate(geometry.get("base"))
        if (not isinstance(geometry.get("edge_indices"), list) or not geometry["edge_indices"]
                or any(type(index) is not int or index < 0 for index in geometry["edge_indices"])
                or len(set(geometry["edge_indices"])) != len(geometry["edge_indices"])):
            raise ValidationError("Fillet edge indices are invalid.")
        radius = _positive_amount(geometry.get("radius"), "radius")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        self._same_saved_volume(geometry, actual)

    @staticmethod
    def _same_saved_volume(geometry: dict[str, Any], actual: float) -> None:
        saved = geometry.get("volume")
        if isinstance(saved, bool) or not isinstance(saved, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved = float(saved)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved) or saved <= 0 or not math.isclose(saved, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    @staticmethod
    def _box_volume(dimensions: dict[str, float]) -> float:
        return NativeKernel().box(dimensions)["volume"]


# Compatibility name for clients of the former Python-binding adapter.
OCPKernelAdapter = OCCTKernelAdapter


class PrimitiveKernelAdapter:
    """Demo adapter; it describes primitives but does not create B-Rep geometry."""

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        self.validate(geometry)
        return OCCTKernelAdapter().tessellate(geometry)

    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]:
        dimensions = {"width": width, "height": height, "depth": depth}
        for name, value in dimensions.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be a number.")
            if not math.isfinite(value) or value <= 0:
                raise ValidationError(f"{name} must be a finite positive number.")
        return {
            "kernel": "primitive-v1",
            "kind": "box",
            "dimensions": dimensions,
            "volume": width * height * depth,
        }

    def validate(self, geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict) or geometry.get("kernel") != "primitive-v1":
            raise ValidationError("Geometry does not match the primitive kernel format.")
        dimensions = geometry.get("dimensions")
        if not isinstance(dimensions, dict):
            raise ValidationError("Geometry dimensions are missing.")
        expected = self.create_box(
            width=dimensions.get("width"),
            height=dimensions.get("height"),
            depth=dimensions.get("depth"),
        )
        if geometry != expected:
            raise ValidationError("Saved geometry descriptor is inconsistent.")

    def minimum_distance(
        self, first: dict[str, Any], second: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure solid clearance.")

    def compare_preserved_volume(
        self, reference: dict[str, Any], candidate: dict[str, Any],
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure preserved solid volume.")

    def sample_minimum_thickness(
        self, geometry: dict[str, Any], *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure solid wall thickness.")

    def certify_global_minimum_thickness(
        self, geometry: dict[str, Any], *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not certify solid wall thickness.")

    def create_extruded_profile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build extruded B-reps.")

    def create_lofted_profiles(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build lofted B-reps.")

    def fuse_profiles(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not fuse B-rep solids.")

    def cut_solids(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not cut B-rep solids.")

    def create_revolved_profile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build revolved B-reps.")
