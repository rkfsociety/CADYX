"""Native configurable single-start trapezoidal thread tools."""
from __future__ import annotations
import base64
import math
import zlib
from .errors import ValidationError
from .native_kernel import NativeKernel

def thread_tool(kernel, *, major_diameter, pitch, length, handedness="right", thread_profile=None):
    if thread_profile is None:
        return kernel.create_metric_thread(major_diameter=major_diameter, pitch=pitch,
                                            length=length, handedness=handedness)
    if (not isinstance(thread_profile, dict) or thread_profile.get("type") != "trapezoidal"
            or set(thread_profile) != {"type", "minor_diameter", "crest_width", "phase_degrees"}):
        raise ValidationError("A trapezoidal thread profile needs type, minor_diameter, crest_width and phase_degrees.")
    values = [major_diameter, thread_profile["minor_diameter"], pitch,
              thread_profile["crest_width"], thread_profile["phase_degrees"], length]
    if any(isinstance(value, bool) or not isinstance(value, (int,float))
           or not math.isfinite(value) for value in values):
        raise ValidationError("Thread profile dimensions must be finite numbers.")
    if (not 0 < values[1] < major_diameter or not 0 < values[3] < pitch
            or length < pitch or handedness not in {"right","left"}):
        raise ValidationError("Thread profile dimensions or handedness are outside the supported range.")
    descriptor = {"kind":"profiled_thread", "major_diameter":major_diameter,
                  "minor_diameter":values[1], "pitch":pitch, "crest_width":values[3],
                  "phase_radians":math.radians(values[4]), "length":length, "handedness":handedness}
    native = NativeKernel()
    brep, metrics = native.export_geometry(descriptor)
    return {"kernel":"occt-v1", "kind":"imported_brep",
            "data":base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
            "volume":metrics["volume"], "brep_valid":True}
