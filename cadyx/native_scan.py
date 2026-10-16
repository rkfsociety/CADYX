"""ctypes bridge to the CPU Open3D scan processor."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .errors import ValidationError


_NATIVE_PHASES = {
    1: "read_source",
    2: "mesh_cleanup",
    3: "voxel_cleanup",
    4: "outlier_filter",
    5: "estimate_normals",
    6: "reconstruct_surface",
    7: "simplify_mesh",
    8: "serialize_mesh",
}
_PROGRESS_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_int)


class NativeScan:
    _library = None
    _directories = []
    _lock = Lock()

    def __init__(self) -> None:
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
                    library = ctypes.CDLL(str(runtime / manifest["scan_bridge"]))
                    library.cadyx_scan_version.argtypes = []
                    library.cadyx_scan_version.restype = ctypes.c_char_p
                    library.cadyx_scan_process.argtypes = [
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_double,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_double,
                        ctypes.c_int,
                        ctypes.c_double,
                        _PROGRESS_CALLBACK,
                        ctypes.POINTER(ctypes.c_size_t),
                        ctypes.c_char_p,
                        ctypes.c_size_t,
                    ]
                    library.cadyx_scan_process.restype = ctypes.c_void_p
                    library.cadyx_scan_free.argtypes = [ctypes.c_void_p]
                    library.cadyx_scan_free.restype = None
                    actual_version = library.cadyx_scan_version().decode("ascii")
                    if actual_version != manifest["open3d_version"]:
                        raise RuntimeError("Native Open3D version differs from the runtime manifest.")
                except Exception:
                    for directory in directories:
                        directory.close()
                    raise
                self._directories.extend(directories)
                self.__class__._library = library
                self.__class__._version = actual_version
        self.library = self._library

    @property
    def version(self) -> str:
        return self._version

    def process(
        self,
        path: Path,
        parameters: dict,
        *,
        source_has_faces: bool,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> bytes:
        error = ctypes.create_string_buffer(1024)
        output_size = ctypes.c_size_t()
        filename = path.resolve(strict=True).as_posix().encode("utf-8")
        def report(phase_id: int) -> None:
            phase = _NATIVE_PHASES.get(phase_id)
            if phase is not None and progress is not None:
                progress({"phase": phase})

        callback = _PROGRESS_CALLBACK(report)
        result = self.library.cadyx_scan_process(
            filename,
            int(source_has_faces),
            parameters["voxel_size"],
            parameters["target_triangles"],
            parameters["outlier_neighbors"],
            parameters["outlier_std_ratio"],
            parameters["poisson_depth"],
            parameters["poisson_density_quantile"],
            callback,
            ctypes.byref(output_size),
            error,
            len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace") or
                                  "Open3D scan processing failed.")
        try:
            return ctypes.string_at(result, output_size.value)
        finally:
            self.library.cadyx_scan_free(result)
