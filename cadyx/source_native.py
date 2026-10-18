"""Build native source candidates and select only sandbox-verified artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .errors import ValidationError

NATIVE_SOURCE_NAMES = frozenset({
    "kernel.cpp", "kernel_common.cpp", "kernel_primitives.cpp", "kernel_topology.cpp",
    "kernel_delivery.cpp", "kernel_validation.cpp", "advanced.cpp", "advanced_topology.cpp",
    "agent_features.cpp", "kernel_internal.hpp", "kernel_abi.hpp",
})


def source_fingerprint(root: Path, runtime: Path) -> str:
    digest = hashlib.sha256()
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8-sig"))
    digest.update(manifest["occt_version"].encode("ascii"))
    for name in sorted(NATIVE_SOURCE_NAMES | {"CMakeLists.txt"}):
        path = root / "native" / name
        digest.update(name.encode("ascii") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def verified_bridge(root: Path, runtime: Path) -> Path | None:
    # Isolated test runtimes supply their explicit candidate through manifest.json.
    if not (runtime / "source-kernels").is_dir():
        return None
    directory = runtime / "source-kernels" / source_fingerprint(root, runtime)
    marker = directory / "verified.json"
    if not marker.is_file():
        return None
    metadata = json.loads(marker.read_text(encoding="utf-8"))
    bridge = directory / "cadyx_kernel.dll"
    if not bridge.is_file() or hashlib.sha256(bridge.read_bytes()).hexdigest() != metadata.get("sha256"):
        raise RuntimeError("The sandbox-verified native bridge changed; rebuild and test the native source extension.")
    return bridge


def build_candidate(root: Path, work: Path, runtime: Path) -> Path:
    cached = verified_bridge(root, runtime)
    if cached is not None:
        return cached
    cmake = shutil.which("cmake")
    if cmake is None:
        raise ValidationError("Native source extensions require the configured CMake tool.")
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8-sig"))
    sdk = runtime / "occt-sdk"
    open3d = next((runtime / path).parent for path in manifest["dll_directories"]
                  if "open3d" in path.lower())
    build = work / "native-build"
    commands = [
        [cmake, "-S", str(root / "native"), "-B", str(build),
         f"-DOCCT_ROOT={sdk.resolve()}", f"-DOPEN3D_ROOT={open3d.resolve()}"],
        [cmake, "--build", str(build), "--config", "Release", "--target", "cadyx_kernel", "--parallel", "2"],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                    timeout=180, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("Native source candidate compilation timed out.") from exc
        if result.returncode:
            raise ValidationError("Native source candidate compilation failed:\n" + (result.stdout + result.stderr)[-10000:])
    bridge = build / "Release" / "cadyx_kernel.dll"
    if not bridge.is_file():
        raise ValidationError("Native source candidate did not produce the required bridge.")
    return bridge


def publish_verified(root: Path, runtime: Path, bridge: Path) -> None:
    fingerprint = source_fingerprint(root, runtime)
    directory = runtime / "source-kernels" / fingerprint
    directory.mkdir(parents=True, exist_ok=True)
    data = bridge.read_bytes()
    target = directory / "cadyx_kernel.dll"
    # Immutable artifacts remain loadable while another host is still running.
    if target.exists() and target.read_bytes() != data:
        raise ValidationError("A verified native artifact already exists for these sources.")
    if not target.exists():
        temporary = directory / "cadyx_kernel.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, target)
    temporary = directory / "verified.tmp"
    temporary.write_text(json.dumps({"sha256": hashlib.sha256(data).hexdigest()}), encoding="utf-8")
    os.replace(temporary, directory / "verified.json")
