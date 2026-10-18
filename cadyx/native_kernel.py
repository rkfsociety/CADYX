"""ctypes bridge to the project-built OCCT adapter, independent of Python's ABI."""

import ctypes
import base64
import json
import math
import os
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any
import zlib

from .errors import ValidationError

_PROGRESS_CALLBACK = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double,
)
_PERIODIC_SPLINE_CACHE_MAX_BYTES = 8 * 1024 * 1024
_PERIODIC_SPLINE_CACHE_MAX_ENTRIES = 128
_NATIVE_ABI_VERSION = 1
_periodic_spline_cache: OrderedDict[tuple[bytes, int], tuple[bytes, int]] = OrderedDict()
_periodic_spline_cache_bytes = 0
_periodic_spline_cache_lock = Lock()


class _GraphNode(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("input_a", ctypes.c_int), ("input_b", ctypes.c_int),
        ("operation", ctypes.c_int), ("plane", ctypes.c_int), ("centered", ctypes.c_int),
        ("amount", ctypes.c_double), ("coordinate_offset", ctypes.c_size_t),
        ("coordinate_count", ctypes.c_size_t), ("edge_offset", ctypes.c_size_t),
        ("edge_count", ctypes.c_size_t),
    ]


# Keep every OCCT bridge signature in one place. Optional symbols support runtimes
# built before the corresponding modeling or topology capability was added.
_NATIVE_FUNCTION_SIGNATURES = {
    "cadyx_box": ([ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_int,
                   ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_profile": ([ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_int,
                        ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_periodic_spline_samples": ([ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                                       ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t],
                                      ctypes.c_void_p),
    "cadyx_fuse_profiles": ([ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                              ctypes.c_int, ctypes.c_double, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                              ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_int,
                              ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_graph": ([ctypes.POINTER(_GraphNode), ctypes.c_size_t,
                     ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                     ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                     ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                     ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t],
                    ctypes.c_void_p),
    "cadyx_step_import": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                           ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_section_profile": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                ctypes.c_int, ctypes.c_double, ctypes.c_size_t,
                                ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_export": ([ctypes.POINTER(_GraphNode), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_void_p),
                           ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_distance": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                             ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                             _PROGRESS_CALLBACK, ctypes.c_void_p,
                             ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_preservation": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                 ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                 ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_global_thickness": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                     _PROGRESS_CALLBACK, ctypes.c_void_p,
                                     ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_thickness": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                              ctypes.c_double, ctypes.c_double,
                              _PROGRESS_CALLBACK, ctypes.c_void_p,
                              ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_step_export": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_char_p,
                           ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_stl_export": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                          ctypes.c_double, ctypes.c_double,
                          ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_void_p),
                          ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_modify_imported_brep": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                    ctypes.c_int, ctypes.POINTER(ctypes.c_double),
                                    ctypes.c_size_t, ctypes.c_char_p,
                                    ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_advanced_brep": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                             ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                             ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                             ctypes.POINTER(ctypes.c_size_t), ctypes.c_char_p,
                             ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_agent_feature_brep": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                  ctypes.c_int, ctypes.POINTER(ctypes.c_double),
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
                                  ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_brep_metrics": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                            ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_agent_topology": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                              ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_face_topology": ([ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                             ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    "cadyx_free": ([ctypes.c_void_p], None),
    "cadyx_kernel_version": ([], ctypes.c_char_p),
    "cadyx_kernel_abi_manifest": ([], ctypes.c_char_p),
}
_OPTIONAL_NATIVE_FUNCTIONS = frozenset({
    "cadyx_advanced_brep",
    "cadyx_agent_feature_brep",
    "cadyx_brep_metrics",
    "cadyx_agent_topology",
    "cadyx_face_topology",
})


def _load_native_functions(library):
    try:
        manifest_function = library.cadyx_kernel_abi_manifest
    except AttributeError as exc:
        raise RuntimeError(
            "Native bridge ABI manifest is missing; run scripts/setup-runtime.ps1 to rebuild the bridge."
        ) from exc
    manifest_function.argtypes = []
    manifest_function.restype = ctypes.c_char_p
    try:
        raw_manifest = manifest_function()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Native bridge ABI manifest is invalid; rebuild the native runtime.") from exc

    if not isinstance(manifest, dict):
        raise RuntimeError("Native bridge ABI manifest must be a JSON object.")
    abi_version = manifest.get("abi_version")
    if type(abi_version) is not int or abi_version != _NATIVE_ABI_VERSION:
        raise RuntimeError(
            f"Unsupported native bridge ABI version {abi_version!r}; "
            f"expected {_NATIVE_ABI_VERSION}. Run scripts/setup-runtime.ps1 to rebuild the native runtime."
        )
    exports = manifest.get("exports")
    if (not isinstance(exports, list) or any(not isinstance(name, str) or not name.startswith("cadyx_")
                                             for name in exports)
            or len(exports) != len(set(exports))):
        raise RuntimeError("Native bridge ABI manifest has an invalid export list.")
    exported_names = set(exports)
    if "cadyx_kernel_abi_manifest" not in exported_names:
        raise RuntimeError("Native bridge ABI manifest does not advertise its own symbol.")

    required_names = set(_NATIVE_FUNCTION_SIGNATURES) - _OPTIONAL_NATIVE_FUNCTIONS
    missing_required = sorted(required_names - exported_names)
    if missing_required:
        raise RuntimeError(
            "Native bridge ABI manifest omits required exports: " + ", ".join(missing_required)
        )

    functions = {}
    advertised_functions = {}
    for name in exports:
        try:
            advertised_functions[name] = getattr(library, name)
        except AttributeError as exc:
            raise RuntimeError(f"Native bridge advertises missing export {name!r}.") from exc

    for name, (argtypes, restype) in _NATIVE_FUNCTION_SIGNATURES.items():
        function = advertised_functions.get(name)
        if function is None:
            continue
        function.argtypes = argtypes
        function.restype = restype
        functions[name] = function
    return functions, manifest


class NativeKernel:
    _library = None
    _functions = None
    _abi_manifest = None
    _directories = []
    _lock = Lock()

    def __init__(self):
        with self._lock:
            if self.__class__._library is None:
                runtime = Path(__file__).resolve().parent.parent / ".runtime"
                manifest_path = runtime / "manifest.json"
                if not manifest_path.is_file():
                    raise RuntimeError("Native runtime is missing; run scripts/setup-runtime.ps1.")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if os.name != "nt":
                    raise RuntimeError("The current native runtime setup supports Windows x64.")
                directories = []
                try:
                    for relative in manifest["dll_directories"]:
                        directories.append(os.add_dll_directory(str(runtime / relative)))
                    from .source_native import verified_bridge
                    bridge = verified_bridge(runtime.parent, runtime) or runtime / manifest["bridge"]
                    library = ctypes.CDLL(str(bridge))
                    functions, abi_manifest = _load_native_functions(library)
                    if functions["cadyx_kernel_version"]().decode("ascii") != manifest["occt_version"]:
                        raise RuntimeError("Native OCCT version differs from the runtime manifest.")
                except Exception:
                    for directory in directories:
                        directory.close()
                    raise
                self._directories.extend(directories)
                self.__class__._library = library
                self.__class__._functions = functions
                self.__class__._abi_manifest = abi_manifest
        self._functions = self.__class__._functions
        self._abi_manifest = self.__class__._abi_manifest
        self._graph_node = _GraphNode
        self.library = self._library

    @property
    def version(self):
        return self.library.cadyx_kernel_version().decode("ascii")

    @staticmethod
    def _native_error(error):
        return error.value.decode("utf-8", errors="replace")

    def _raise_native_error(self, result, error):
        if not result:
            raise ValidationError(self._native_error(error))

    def _free_native_result(self, result):
        if result:
            self._functions["cadyx_free"](result)

    def _json_result(self, result, error):
        self._raise_native_error(result, error)
        try:
            return json.loads(ctypes.string_at(result).decode("utf-8"))
        finally:
            self._free_native_result(result)

    def _bytes_result(self, result, size, error):
        self._raise_native_error(result, error)
        try:
            return bytes(ctypes.string_at(result, size.value))
        finally:
            self._free_native_result(result)

    def _optional_brep_json(
        self,
        symbol,
        brep,
        *,
        invalid_message: str,
        error_capacity: int = 2048,
    ) -> dict[str, Any] | None:
        function = self._functions.get(symbol)
        if function is None:
            return None
        source = self._export_brep_buffer(brep)
        error = ctypes.create_string_buffer(error_capacity)
        result = function(source, len(brep), error, len(error))
        payload = self._json_result(result, error)
        if not isinstance(payload, dict):
            raise ValidationError(invalid_message)
        return payload

    def brep_metrics(self, brep: bytes) -> dict[str, Any] | None:
        """Measure a B-rep directly when this runtime includes the optional helper."""
        return self._optional_brep_json(
            "cadyx_brep_metrics", brep, invalid_message="OCCT returned invalid B-rep metrics."
        )

    def agent_topology(self, brep: bytes) -> dict[str, Any] | None:
        """Return analytic edge and face data when supported by this runtime."""
        return self._optional_brep_json(
            "cadyx_agent_topology", brep, invalid_message="OCCT returned invalid topology metadata."
        )

    def face_topology(self, brep: bytes) -> dict[str, Any] | None:
        """Return basic face data for older bridges that lack combined topology."""
        return self._optional_brep_json(
            "cadyx_face_topology", brep, invalid_message="OCCT returned invalid topology metadata."
        )

    def advanced_brep(
        self,
        brep: bytes,
        operation: int,
        values: list[float],
        indices: list[int] | None = None,
    ) -> tuple[bytes, list[int] | None]:
        function = self._functions.get("cadyx_advanced_brep")
        if function is None:
            raise RuntimeError(
                "CADYX native runtime is stale; run scripts/setup-runtime.ps1 to rebuild advanced modeling support."
            )
        source = self._export_brep_buffer(brep)
        numbers = (ctypes.c_double * len(values))(*values) if values else None
        selected = indices or []
        native_indices = (ctypes.c_int * len(selected))(*selected) if selected else None
        size = ctypes.c_size_t()
        generated_faces = ctypes.create_string_buffer(64 * 1024)
        error = ctypes.create_string_buffer(2048)
        result = function(
            source, len(brep), operation, numbers, len(values), native_indices, len(selected),
            ctypes.byref(size), generated_faces, len(generated_faces), error, len(error),
        )
        self._raise_native_error(result, error)
        try:
            face_indices = json.loads(generated_faces.value) if operation in {2, 4} else None
            if face_indices is not None and (
                not isinstance(face_indices, list) or any(type(index) is not int for index in face_indices)
            ):
                raise ValidationError("OCCT returned invalid generated face indices.")
            return bytes(ctypes.string_at(result, size.value)), face_indices
        finally:
            self._free_native_result(result)

    def agent_feature_brep(self, brep: bytes, operation: int, values: list[float]) -> bytes:
        function = self._functions.get("cadyx_agent_feature_brep")
        if function is None:
            raise RuntimeError(
                "CADYX native runtime is stale; run scripts/setup-runtime.ps1 to rebuild agent authoring support."
            )
        source = self._export_brep_buffer(brep)
        numbers = (ctypes.c_double * len(values))(*values)
        size = ctypes.c_size_t()
        error = ctypes.create_string_buffer(2048)
        result = function(
            source, len(brep), operation, numbers, len(values), ctypes.byref(size), error, len(error),
        )
        return self._bytes_result(result, size, error)

    def box(self, dimensions, *, mesh=False):
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_box(dimensions["width"], dimensions["height"],
                                        dimensions["depth"], int(mesh), error, len(error))
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    def profile(self, points, *, operation, plane, amount, centered=False, mesh=False):
        if operation not in {0, 1} or plane not in {"XY", "XZ"}:
            raise ValidationError("Unsupported profile operation or sketch plane.")
        coordinates = (ctypes.c_double * (2 * len(points)))(
            *(coordinate for point in points for coordinate in (point["x"], point["y"]))
        )
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_profile(
            coordinates, len(points), operation, 0 if plane == "XY" else 1,
            amount, int(centered), int(mesh), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    def periodic_spline_samples(self, points, *, samples_per_span=24):
        global _periodic_spline_cache_bytes

        max_index = (1 << (8 * ctypes.sizeof(ctypes.c_int) - 1)) - 1
        if (not isinstance(points, list) or len(points) < 3
                or len(points) > max_index
                or isinstance(samples_per_span, bool) or not isinstance(samples_per_span, int)
                or samples_per_span < 1 or samples_per_span > ctypes.c_size_t(-1).value):
            raise ValidationError("Periodic spline samples need at least 3 points and a positive sample count.")
        coordinates = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValidationError("Periodic spline coordinates must be finite point pairs.")
            for value in point:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValidationError("Periodic spline coordinates must be finite point pairs.")
                try:
                    normalized = float(value)
                except OverflowError as exc:
                    raise ValidationError("Periodic spline coordinates must be finite point pairs.") from exc
                if not math.isfinite(normalized):
                    raise ValidationError("Periodic spline coordinates must be finite point pairs.")
                coordinates.append(normalized)
        native_coordinates = (ctypes.c_double * len(coordinates))(*coordinates)
        coordinate_key = ctypes.string_at(
            ctypes.addressof(native_coordinates), ctypes.sizeof(native_coordinates),
        )
        cache_key = (coordinate_key, samples_per_span)
        with _periodic_spline_cache_lock:
            cached = _periodic_spline_cache.get(cache_key)
            if cached is not None:
                _periodic_spline_cache.move_to_end(cache_key)
                payload = cached[0]
            else:
                payload = None
        if payload is not None:
            return json.loads(payload)["points"]

        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_periodic_spline_samples(
            native_coordinates, len(points), samples_per_span, error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            payload = ctypes.string_at(result)
        finally:
            self.library.cadyx_free(result)
        sampled_points = json.loads(payload)["points"]
        cache_entry_bytes = len(coordinate_key) + len(payload) + 128
        if cache_entry_bytes <= _PERIODIC_SPLINE_CACHE_MAX_BYTES:
            with _periodic_spline_cache_lock:
                previous = _periodic_spline_cache.pop(cache_key, None)
                if previous is not None:
                    _periodic_spline_cache_bytes -= previous[1]
                while (_periodic_spline_cache
                       and (_periodic_spline_cache_bytes + cache_entry_bytes > _PERIODIC_SPLINE_CACHE_MAX_BYTES
                            or len(_periodic_spline_cache) >= _PERIODIC_SPLINE_CACHE_MAX_ENTRIES)):
                    _, (_, removed_bytes) = _periodic_spline_cache.popitem(last=False)
                    _periodic_spline_cache_bytes -= removed_bytes
                _periodic_spline_cache[cache_key] = (payload, cache_entry_bytes)
                _periodic_spline_cache_bytes += cache_entry_bytes
        return sampled_points

    def fuse_profiles(self, base, tool, *, mesh=False):
        if base.get("profile_curve") or tool.get("profile_curve"):
            return self._geometry_graph({"kind": "fused_profiles", "base": base, "tool": tool}, mesh=mesh)
        def arguments(geometry):
            kind = geometry.get("kind")
            if kind not in {"extruded_profile", "revolved_profile"}:
                raise ValidationError("Boolean fuse currently accepts profile-based OCCT solids.")
            operation = 0 if kind == "extruded_profile" else 1
            plane = geometry["plane"]
            amount = geometry["depth"] if operation == 0 else math.radians(geometry["angle_degrees"])
            centered = geometry.get("centered", False)
            coordinate_array = ctypes.c_double * (2 * len(geometry["profile"]))
            coordinates = coordinate_array(
                *(coordinate for point in geometry["profile"] for coordinate in (point["x"], point["y"]))
            )
            return coordinates, len(geometry["profile"]), operation, 0 if plane == "XY" else 1, amount, int(centered)

        left = arguments(base)
        right = arguments(tool)
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_fuse_profiles(
            *left, *right, int(mesh), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    def geometry_graph(self, geometry, *, mesh=False, topology=False):
        return self._geometry_graph(geometry, mesh=mesh, topology=topology)

    def loft(self, sections):
        if not isinstance(sections, list) or len(sections) < 2:
            raise ValidationError("A loft needs at least two sketch sections.")
        normalized = []
        for section in sections:
            if not isinstance(section, list) or len(section) < 3:
                raise ValidationError("Every loft section needs at least 3 points.")
            normalized.append([{"x": float(point["x"]), "y": float(point["y"])} for point in section])
        return self._geometry_graph({"kind": "lofted_profiles", "sections": normalized},
                                    mesh=False, topology=False)

    def export_geometry(self, geometry):
        return self._geometry_graph(geometry, export=True)

    def minimum_brep_distance(self, first, second, *, progress=None):
        first_buffer = self._export_brep_buffer(first)
        second_buffer = self._export_brep_buffer(second)
        error = ctypes.create_string_buffer(1024)
        callback = _PROGRESS_CALLBACK()
        if progress is not None:
            def report(_user_data, phase, percent):
                progress(phase.decode("ascii", errors="replace"), percent)
            callback = _PROGRESS_CALLBACK(report)
        result = self.library.cadyx_brep_distance(
            first_buffer, len(first), second_buffer, len(second), callback, None, error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result).decode("utf-8"))
        finally:
            self.library.cadyx_free(result)

    def brep_preservation(self, reference, candidate):
        reference_buffer = self._export_brep_buffer(reference)
        candidate_buffer = self._export_brep_buffer(candidate)
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_brep_preservation(
            reference_buffer, len(reference), candidate_buffer, len(candidate), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result).decode("utf-8"))
        finally:
            self.library.cadyx_free(result)

    def brep_thickness(self, geometry, minimum_thickness, tolerance, *, progress=None):
        geometry_buffer = self._export_brep_buffer(geometry)
        error = ctypes.create_string_buffer(1024)
        callback = _PROGRESS_CALLBACK()
        if progress is not None:
            def report(_user_data, phase, percent):
                progress(phase.decode("ascii", errors="replace"), percent)
            callback = _PROGRESS_CALLBACK(report)
        result = self.library.cadyx_brep_thickness(
            geometry_buffer, len(geometry), minimum_thickness, tolerance,
            callback, None, error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result).decode("utf-8"))
        finally:
            self.library.cadyx_free(result)

    def brep_global_thickness(self, geometry, *, progress=None):
        geometry_buffer = self._export_brep_buffer(geometry)
        error = ctypes.create_string_buffer(1024)
        callback = _PROGRESS_CALLBACK()
        if progress is not None:
            def report(_user_data, phase, percent):
                progress(phase.decode("ascii", errors="replace"), percent)
            callback = _PROGRESS_CALLBACK(report)
        result = self.library.cadyx_brep_global_thickness(
            geometry_buffer, len(geometry), callback, None, error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result).decode("utf-8"))
        finally:
            self.library.cadyx_free(result)

    def _geometry_graph(self, geometry, *, mesh=False, topology=False, export=False):
        nodes = []
        coordinates = []
        edge_indices = []
        brep_data = bytearray()

        def append(value):
            kind = value.get("kind")
            if kind == "profile_with_holes":
                return append(value["solid"])
            node = {"input_a": -1, "input_b": -1, "operation": 0, "plane": 1,
                    "centered": 0, "amount": 0.0, "edge_offset": len(edge_indices), "edge_count": 0}
            if kind == "extruded_circle":
                center = value.get("center")
                if not isinstance(center, dict) or set(center) != {"x", "y"}:
                    raise ValidationError("Analytic circular extrusion center is invalid.")
                node.update(type=14, plane=int(value["plane"] == "XZ"),
                            centered=int(value.get("centered", False)), amount=value["depth"],
                            coordinate_offset=len(coordinates), coordinate_count=3)
                coordinates.extend((center["x"], center["y"], value["radius"]))
            elif kind == "box":
                dimensions = value["dimensions"]
                node.update(type=4, coordinate_offset=len(coordinates), coordinate_count=3)
                coordinates.extend(dimensions[key] for key in ("width", "height", "depth"))
            elif kind in {"extruded_profile", "revolved_profile"}:
                operation = int(kind == "revolved_profile")
                amount = value["depth"] if operation == 0 else math.radians(value["angle_degrees"])
                segments = value.get("profile_segments")
                if segments is not None:
                    from .profile_segments import segment_values
                    segments = segment_values(segments)
                    node.update(type=15, operation=operation, plane=int(value["plane"] == "XZ"),
                                centered=int(value.get("centered", False)), amount=amount,
                                coordinate_offset=len(coordinates), coordinate_count=sum(len(segment) for segment in segments))
                    coordinates.extend(coordinate for segment in segments for coordinate in segment)
                    nodes.append(node)
                    return len(nodes) - 1
                profile_curve = value.get("profile_curve")
                if profile_curve not in {None, "closed_catmull_rom", "closed_periodic_bspline"}:
                    raise ValidationError("Unsupported profile curve descriptor.")
                curved = profile_curve in {"closed_catmull_rom", "closed_periodic_bspline"}
                profile = value.get("profile_curve_points") if curved else value["profile"]
                if curved and (not isinstance(profile, list) or len(profile) < 3):
                    raise ValidationError("A closed spline profile needs at least 3 control vertices.")
                node_type = (12 if profile_curve == "closed_catmull_rom" else
                             13 if profile_curve == "closed_periodic_bspline" else 0)
                node.update(type=node_type, operation=operation, plane=int(value["plane"] == "XZ"),
                            centered=int(value.get("centered", False)), amount=amount,
                            coordinate_offset=len(coordinates), coordinate_count=len(profile) * 2)
                for point in profile:
                    for coordinate in (point["x"], point["y"]):
                        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                            raise ValidationError("Profile coordinates must be finite numbers.")
                        try:
                            normalized = float(coordinate)
                        except OverflowError as exc:
                            raise ValidationError("Profile coordinates must be finite numbers.") from exc
                        if not math.isfinite(normalized):
                            raise ValidationError("Profile coordinates must be finite numbers.")
                        coordinates.append(normalized)
            elif kind == "lofted_profiles":
                section_sizes = []
                start = len(coordinates)
                for section, offset in zip(value["sections"], value["section_offsets"], strict=True):
                    section_sizes.append(len(section))
                    coordinates.extend(coord for point in section
                                       for coord in (offset, point["x"], point["y"]))
                node.update(type=8, coordinate_offset=start,
                            coordinate_count=len(coordinates) - start,
                            edge_offset=len(edge_indices), edge_count=len(section_sizes))
                edge_indices.extend(section_sizes)
            elif kind == "ruled_shell":
                patches = value.get("patches")
                if not isinstance(patches, list) or len(patches) < 4:
                    raise ValidationError("A ruled shell needs at least four paired surface patches.")
                start = len(coordinates)
                patch_sizes = []
                for patch in patches:
                    if not isinstance(patch, dict) or set(patch) != {"first", "second"}:
                        raise ValidationError("Ruled shell patches need two point-array rails.")
                    for key in ("first", "second"):
                        rail = patch[key]
                        if not isinstance(rail, list) or len(rail) < 2:
                            raise ValidationError("Ruled shell rails need at least two points.")
                        patch_sizes.append(len(rail))
                        coordinates.extend(coord for point in rail for coord in point)
                edge_offset = len(edge_indices)
                edge_indices.extend(patch_sizes)
                node.update(type=11, amount=value["sewing_tolerance"],
                            coordinate_offset=start, coordinate_count=len(coordinates) - start,
                            edge_offset=edge_offset, edge_count=len(patch_sizes))
            elif kind == "multicontour_loft":
                branches = value.get("branches")
                if not isinstance(branches, list) or len(branches) < 2:
                    raise ValidationError("A multi-contour loft needs at least two segment solids.")
                branch_nodes = [append(branch) for branch in branches]
                edge_offset = len(edge_indices)
                edge_indices.extend(branch_nodes)
                nodes.append({"type": 10, "input_a": -1, "input_b": -1,
                              "operation": 0, "plane": 1, "centered": 0, "amount": 0.0,
                              "edge_offset": edge_offset, "edge_count": len(branch_nodes)})
                return len(nodes) - 1
            elif kind == "swept_pipe":
                node.update(type=1, plane=int(value["plane"] == "XZ"), amount=value["radius"],
                            coordinate_offset=len(coordinates), coordinate_count=len(value["path"]) * 2)
                coordinates.extend(coord for point in value["path"] for coord in (point["x"], point["y"]))
            elif kind == "profiled_thread":
                node.update(type=16, operation=-1 if value["handedness"] == "left" else 1,
                            amount=value["length"], coordinate_offset=len(coordinates), coordinate_count=5)
                coordinates.extend(value[key] for key in (
                    "major_diameter", "minor_diameter", "pitch", "crest_width", "phase_radians"))
            elif kind == "metric_thread":
                node.update(type=5, operation=1 if value["handedness"] == "right" else -1,
                            amount=value["length"], coordinate_offset=len(coordinates), coordinate_count=2)
                coordinates.extend((value["major_diameter"], value["pitch"]))
            elif kind in {"fused_profiles", "fused_solids"}:
                node.update(type=2, input_a=append(value["base"]), input_b=append(value["tool"]))
            elif kind == "cut_solids":
                node.update(type=7, input_a=append(value["base"]), input_b=append(value["tool"]))
            elif kind == "filleted_solid":
                node.update(type=3, input_a=append(value["base"]), amount=value["radius"],
                            edge_offset=len(edge_indices), edge_count=len(value["edge_indices"]))
                edge_indices.extend(value["edge_indices"])
            elif kind == "imported_brep":
                try:
                    compressed = base64.b64decode(value["data"], validate=True)
                    decoder = zlib.decompressobj()
                    restored = decoder.decompress(compressed, 64 * 1024 * 1024 + 1)
                    if (len(restored) > 64 * 1024 * 1024 or not decoder.eof
                            or decoder.unused_data or decoder.unconsumed_tail):
                        raise ValueError("invalid compressed B-rep payload")
                except (KeyError, TypeError, ValueError, zlib.error) as exc:
                    raise ValidationError("Imported B-rep data is invalid or exceeds the 64 MB limit.") from exc
                if not restored or len(brep_data) + len(restored) > 64 * 1024 * 1024:
                    raise ValidationError("Imported B-rep data is empty or exceeds the 64 MB limit.")
                node.update(type=6, coordinate_offset=len(brep_data), coordinate_count=len(restored))
                brep_data.extend(restored)
            else:
                raise ValidationError("Geometry graph contains an unsupported operation.")
            nodes.append(node)
            return len(nodes) - 1

        append(geometry)
        native_nodes = (self._graph_node * len(nodes))()
        for index, node in enumerate(nodes):
            for name, value in node.items():
                setattr(native_nodes[index], name, value)
        native_coordinates = (ctypes.c_double * len(coordinates))(*coordinates) if coordinates else None
        native_edges = (ctypes.c_int * len(edge_indices))(*edge_indices) if edge_indices else None
        native_brep_data = (ctypes.c_ubyte * len(brep_data)).from_buffer_copy(brep_data) if brep_data else None
        error = ctypes.create_string_buffer(1024)
        if export:
            output_size = ctypes.c_size_t()
            metrics_json = ctypes.c_void_p()
            result = self.library.cadyx_brep_export(
                native_nodes, len(nodes), native_coordinates, len(coordinates), native_edges, len(edge_indices),
                native_brep_data, len(brep_data), ctypes.byref(output_size), ctypes.byref(metrics_json),
                error, len(error),
            )
        else:
            result = self.library.cadyx_graph(
                native_nodes, len(nodes), native_coordinates, len(coordinates), native_edges, len(edge_indices),
                native_brep_data, len(brep_data), int(mesh), int(topology), error, len(error),
            )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        if export:
            try:
                return bytes(ctypes.string_at(result, output_size.value)), json.loads(
                    ctypes.string_at(metrics_json).decode("utf-8"),
                )
            finally:
                self.library.cadyx_free(result)
                if metrics_json:
                    self.library.cadyx_free(metrics_json)
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    def import_step(self, source):
        if not isinstance(source, bytes) or not source or len(source) > 64 * 1024 * 1024:
            raise ValidationError("STEP source is empty or exceeds the 64 MB limit.")
        source_buffer = (ctypes.c_ubyte * len(source)).from_buffer_copy(source)
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_step_import(source_buffer, len(source), error, len(error))
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    def section_profile(self, brep, *, normal_axis, offset, max_points):
        if not isinstance(brep, bytes) or not brep or len(brep) > 64 * 1024 * 1024:
            raise ValidationError("Section source B-rep is empty or exceeds the 64 MB limit.")
        axes = {"X": 0, "Y": 1, "Z": 2}
        if normal_axis not in axes:
            raise ValidationError("Section normal axis must be X, Y or Z.")
        if (isinstance(max_points, bool) or not isinstance(max_points, int)
                or max_points < 16 or max_points > ctypes.c_size_t(-1).value):
            raise ValidationError("Section sample limit must be at least 16 and fit the native size type.")
        source = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_section_profile(
            source, len(brep), axes[normal_axis], float(offset), max_points, error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)

    @staticmethod
    def _export_brep_buffer(brep):
        if not isinstance(brep, bytes) or not brep or len(brep) > 64 * 1024 * 1024:
            raise ValidationError("B-rep data is empty or exceeds the 64 MB limit.")
        return (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)

    def export_step_brep(self, brep):
        source = self._export_brep_buffer(brep)
        output_size = ctypes.c_size_t()
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_step_export(
            source, len(brep), ctypes.byref(output_size), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return bytes(ctypes.string_at(result, output_size.value))
        finally:
            self.library.cadyx_free(result)

    def export_stl_brep(self, brep, *, linear_deflection, angular_deflection):
        source = self._export_brep_buffer(brep)
        output_size = ctypes.c_size_t()
        metrics_json = ctypes.c_void_p()
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_stl_export(
            source, len(brep), linear_deflection, angular_deflection,
            ctypes.byref(output_size), ctypes.byref(metrics_json), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return (bytes(ctypes.string_at(result, output_size.value)),
                    json.loads(ctypes.string_at(metrics_json).decode("utf-8")))
        finally:
            self.library.cadyx_free(result)
            if metrics_json:
                self.library.cadyx_free(metrics_json)

    def modify_imported_brep(self, geometry, *, operation, parameters):
        if operation not in {"resize_thread_x", "rigid_transform"}:
            raise ValidationError("Unsupported imported B-rep modification.")
        if not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep":
            raise ValidationError("Only imported OCCT B-reps can be modified.")
        try:
            compressed = base64.b64decode(geometry["data"], validate=True)
            decoder = zlib.decompressobj()
            brep = decoder.decompress(compressed, 64 * 1024 * 1024 + 1)
            if (len(brep) > 64 * 1024 * 1024 or not decoder.eof
                    or decoder.unused_data or decoder.unconsumed_tail):
                raise ValueError("invalid compressed B-rep payload")
        except (KeyError, TypeError, ValueError, zlib.error) as exc:
            raise ValidationError("Imported B-rep data is invalid or exceeds the 64 MB limit.") from exc
        expected_count = 6 if operation == "resize_thread_x" else 7
        if (not isinstance(parameters, (list, tuple)) or len(parameters) != expected_count
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in parameters)):
            raise ValidationError("Imported B-rep modification parameters are invalid.")
        if not brep:
            raise ValidationError("Imported B-rep data is empty.")
        data_buffer = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
        parameter_buffer = (ctypes.c_double * len(parameters))(*(float(value) for value in parameters))
        operation_code = 1 if operation == "resize_thread_x" else 2
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_modify_imported_brep(
            data_buffer, len(brep), operation_code, parameter_buffer, len(parameters), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)
