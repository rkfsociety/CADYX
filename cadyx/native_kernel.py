"""ctypes bridge to the project-built OCCT adapter, independent of Python's ABI."""

import ctypes
import base64
import json
import math
import os
from pathlib import Path
from threading import Lock
import zlib

from .errors import ValidationError


class NativeKernel:
    _library = None
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
                    library = ctypes.CDLL(str(runtime / manifest["bridge"]))
                    library.cadyx_box.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double,
                                                 ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
                    library.cadyx_box.restype = ctypes.c_void_p
                    library.cadyx_profile.argtypes = [
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_int,
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_profile.restype = ctypes.c_void_p
                    library.cadyx_fuse_profiles.argtypes = [
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_double, ctypes.c_int,
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_int,
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_fuse_profiles.restype = ctypes.c_void_p
                    class GraphNode(ctypes.Structure):
                        _fields_ = [
                            ("type", ctypes.c_int), ("input_a", ctypes.c_int), ("input_b", ctypes.c_int),
                            ("operation", ctypes.c_int), ("plane", ctypes.c_int), ("centered", ctypes.c_int),
                            ("amount", ctypes.c_double), ("coordinate_offset", ctypes.c_size_t),
                            ("coordinate_count", ctypes.c_size_t), ("edge_offset", ctypes.c_size_t),
                            ("edge_count", ctypes.c_size_t),
                        ]
                    self.__class__._graph_node = GraphNode
                    library.cadyx_graph.argtypes = [
                        ctypes.POINTER(GraphNode), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                        ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_graph.restype = ctypes.c_void_p
                    library.cadyx_step_import.argtypes = [
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_step_import.restype = ctypes.c_void_p
                    library.cadyx_section_profile.argtypes = [
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_double, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_section_profile.restype = ctypes.c_void_p
                    library.cadyx_brep_export.argtypes = [
                        ctypes.POINTER(GraphNode), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_brep_export.restype = ctypes.c_void_p
                    library.cadyx_step_export.argtypes = [
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_size_t), ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_step_export.restype = ctypes.c_void_p
                    library.cadyx_stl_export.argtypes = [
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_double, ctypes.c_double,
                        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_stl_export.restype = ctypes.c_void_p
                    library.cadyx_modify_imported_brep.argtypes = [
                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_int,
                        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_modify_imported_brep.restype = ctypes.c_void_p
                    library.cadyx_free.argtypes = [ctypes.c_void_p]
                    library.cadyx_free.restype = None
                    library.cadyx_kernel_version.argtypes = []
                    library.cadyx_kernel_version.restype = ctypes.c_char_p
                    if library.cadyx_kernel_version().decode("ascii") != manifest["occt_version"]:
                        raise RuntimeError("Native OCCT version differs from the runtime manifest.")
                except Exception:
                    for directory in directories:
                        directory.close()
                    raise
                self._directories.extend(directories)
                self.__class__._library = library
        self.library = self._library

    @property
    def version(self):
        return self.library.cadyx_kernel_version().decode("ascii")

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

    def fuse_profiles(self, base, tool, *, mesh=False):
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
        if not isinstance(sections, list) or not 2 <= len(sections) <= 64:
            raise ValidationError("A loft needs between 2 and 64 sketch sections.")
        normalized = []
        for section in sections:
            if not isinstance(section, list) or len(section) < 3:
                raise ValidationError("Every loft section needs at least 3 points.")
            normalized.append([{"x": float(point["x"]), "y": float(point["y"])} for point in section])
        return self._geometry_graph({"kind": "lofted_profiles", "sections": normalized},
                                    mesh=False, topology=False)

    def export_geometry(self, geometry):
        return self._geometry_graph(geometry, export=True)

    def _geometry_graph(self, geometry, *, mesh=False, topology=False, export=False):
        nodes = []
        coordinates = []
        edge_indices = []
        brep_data = bytearray()

        def append(value):
            kind = value.get("kind")
            node = {"input_a": -1, "input_b": -1, "operation": 0, "plane": 1,
                    "centered": 0, "amount": 0.0, "edge_offset": len(edge_indices), "edge_count": 0}
            if kind == "box":
                dimensions = value["dimensions"]
                node.update(type=4, coordinate_offset=len(coordinates), coordinate_count=3)
                coordinates.extend(dimensions[key] for key in ("width", "height", "depth"))
            elif kind in {"extruded_profile", "revolved_profile"}:
                operation = int(kind == "revolved_profile")
                amount = value["depth"] if operation == 0 else math.radians(value["angle_degrees"])
                node.update(type=0, operation=operation, plane=int(value["plane"] == "XZ"),
                            centered=int(value.get("centered", False)), amount=amount,
                            coordinate_offset=len(coordinates), coordinate_count=len(value["profile"]) * 2)
                coordinates.extend(coord for point in value["profile"] for coord in (point["x"], point["y"]))
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
            elif kind == "swept_pipe":
                node.update(type=1, plane=int(value["plane"] == "XZ"), amount=value["radius"],
                            coordinate_offset=len(coordinates), coordinate_count=len(value["path"]) * 2)
                coordinates.extend(coord for point in value["path"] for coord in (point["x"], point["y"]))
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
                or not 16 <= max_points <= 4096):
            raise ValidationError("Section sample limit must be between 16 and 4096 points.")
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
