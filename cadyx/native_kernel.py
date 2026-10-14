"""ctypes bridge to the project-built OCCT adapter, independent of Python's ABI."""

import ctypes
import json
import os
from pathlib import Path
from threading import Lock

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
                        ctypes.c_int, ctypes.c_double, ctypes.c_int,
                        ctypes.c_char_p, ctypes.c_size_t,
                    ]
                    library.cadyx_profile.restype = ctypes.c_void_p
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

    def profile(self, points, *, operation, plane, amount, mesh=False):
        if operation not in {0, 1} or plane not in {"XY", "XZ"}:
            raise ValidationError("Unsupported profile operation or sketch plane.")
        coordinates = (ctypes.c_double * (2 * len(points)))(
            *(coordinate for point in points for coordinate in (point["x"], point["y"]))
        )
        error = ctypes.create_string_buffer(1024)
        result = self.library.cadyx_profile(
            coordinates, len(points), operation, 0 if plane == "XY" else 1,
            amount, int(mesh), error, len(error),
        )
        if not result:
            raise ValidationError(error.value.decode("utf-8", errors="replace"))
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.cadyx_free(result)
