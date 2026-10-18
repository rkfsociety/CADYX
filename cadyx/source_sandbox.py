"""Windows AppContainer runner for untrusted CADYX source-change tests.

The sandbox runner is fail-closed: it never falls back to a normal subprocess.
It requires the experimental Windows processmodel API, read-only source/runtime
mounts, a single writable scratch directory, and a bounded Windows Job Object.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .errors import ValidationError


class SourceSandboxUnavailable(RuntimeError):
    """The current host cannot provide the required Windows sandbox."""


def build_sandbox_spec(read_write: Sequence[str], read_only: Sequence[str]) -> bytes:
    """Build the minimal version-0.1.0 SandboxSpec FlatBuffer used by processmodel."""
    if not read_write or not read_only or any(not Path(path).is_absolute() for path in (*read_write, *read_only)):
        raise ValidationError("AppContainer source tests need absolute read/write and read-only directory lists.")
    buffer = bytearray(struct.pack("<I4s", 36, b"SBOX"))
    field_offsets = (4, 8, 0, 9, 0, 10, 0, 12, 16, 0, 0, 0)
    buffer.extend(struct.pack("<HH12H", 26, 20, *field_offsets))
    if len(buffer) != 36:
        raise RuntimeError("SandboxSpec vtable layout is invalid.")
    table_start = len(buffer)
    buffer.extend(struct.pack("<i", table_start - 8))
    buffer.extend(b"\0" * 16)

    def align4() -> None:
        buffer.extend(b"\0" * ((-len(buffer)) % 4))

    def add_string(value: str) -> int:
        encoded = value.encode("utf-8")
        align4()
        start = len(buffer)
        buffer.extend(struct.pack("<I", len(encoded)))
        buffer.extend(encoded)
        buffer.append(0)
        return start

    def add_string_vector(values: Sequence[str]) -> int:
        align4()
        start = len(buffer)
        buffer.extend(struct.pack("<I", len(values)))
        element_positions = []
        for _ in values:
            element_positions.append(len(buffer))
            buffer.extend(b"\0" * 4)
        for value, element_position in zip(values, element_positions, strict=True):
            target = add_string(value)
            struct.pack_into("<I", buffer, element_position, target - element_position)
        return start

    version = add_string("0.1.0")
    rw = add_string_vector(read_write)
    ro = add_string_vector(read_only)
    struct.pack_into("<I", buffer, table_start + 4, version - (table_start + 4))
    buffer[table_start + 8] = 1  # app_container
    buffer[table_start + 9] = 1  # disallow_win32k_system_calls
    buffer[table_start + 10] = 1  # least_privilege
    struct.pack_into("<I", buffer, table_start + 12, rw - (table_start + 12))
    struct.pack_into("<I", buffer, table_start + 16, ro - (table_start + 16))
    return bytes(buffer)


def run_appcontainer_process(
    application: Path,
    arguments: Sequence[str],
    *,
    current_directory: Path,
    read_write: Sequence[Path],
    read_only: Sequence[Path],
    environment: dict[str, str],
    timeout_seconds: float = 120.0,
    memory_limit_bytes: int = 2_000_000_000,
) -> dict[str, Any]:
    """Run one process in a no-network AppContainer and bounded nested Job Object."""
    if os.name != "nt":
        raise SourceSandboxUnavailable("CADYX source-code isolation is available only on Windows hosts.")
    if not application.is_file() or not current_directory.is_dir():
        raise SourceSandboxUnavailable("The isolated test interpreter or scratch directory is unavailable.")
    if timeout_seconds <= 0 or memory_limit_bytes < 64 * 1024 * 1024:
        raise ValidationError("Source-test process limits are invalid.")
    paths_rw = [str(path.resolve()) for path in read_write]
    paths_ro = [str(path.resolve()) for path in read_only]
    specification = build_sandbox_spec(paths_rw, paths_ro)
    try:
        processmodel = ctypes.WinDLL("processmodel.dll", use_last_error=True)
        create_sandbox = processmodel.Experimental_CreateProcessInSandbox
    except (OSError, AttributeError) as exc:
        raise SourceSandboxUnavailable(
            "Windows processmodel.dll does not expose Experimental_CreateProcessInSandbox; no fallback is permitted."
        ) from exc

    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32), ("lpReserved", ctypes.c_wchar_p),
            ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p),
            ("dwX", ctypes.c_uint32), ("dwY", ctypes.c_uint32),
            ("dwXSize", ctypes.c_uint32), ("dwYSize", ctypes.c_uint32),
            ("dwXCountChars", ctypes.c_uint32), ("dwYCountChars", ctypes.c_uint32),
            ("dwFillAttribute", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
            ("wShowWindow", ctypes.c_uint16), ("cbReserved2", ctypes.c_uint16),
            ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.c_void_p),
            ("hStdOutput", ctypes.c_void_p), ("hStdError", ctypes.c_void_p),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p),
                    ("dwProcessId", ctypes.c_uint32), ("dwThreadId", ctypes.c_uint32)]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation), ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel32.ResumeThread.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    safe_names = {
        "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432", "COMPUTERNAME", "COMSPEC", "DRIVERDATA", "HOMEDRIVE",
        "HOMEPATH", "LOCALAPPDATA", "LOGONSERVER", "NUMBER_OF_PROCESSORS", "OS", "PATH",
        "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMW6432", "PUBLIC", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
        "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE", "USERNAME", "USERPROFILE", "WINDIR",
    }
    if any(key.upper() not in safe_names for key in environment):
        raise ValidationError("The AppContainer environment may contain only standard Windows runtime variables.")
    environment_entries = [
        *(f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].casefold())),
    ]
    environment_block = "\0".join(environment_entries) + "\0\0"
    environment_buffer = ctypes.create_unicode_buffer(environment_block)
    command_line = ctypes.create_unicode_buffer(
        subprocess.list2cmdline([str(application), *arguments]),
    )
    startup = StartupInfo()
    startup.cb = ctypes.sizeof(StartupInfo)
    process = ProcessInformation()
    spec_buffer = ctypes.create_string_buffer(specification)
    create_sandbox.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p,
        ctypes.POINTER(StartupInfo), ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(ProcessInformation),
    ]
    create_sandbox.restype = ctypes.c_int
    flags = 0x00000004 | 0x00000400  # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
    identity = "CadyxSource_" + uuid4().hex
    if not create_sandbox(
        str(application), command_line, None, None, 0, flags,
        ctypes.cast(environment_buffer, ctypes.c_void_p), str(current_directory),
        ctypes.byref(startup), identity, ctypes.cast(spec_buffer, ctypes.c_void_p),
        len(specification), ctypes.byref(process),
    ):
        error = ctypes.get_last_error()
        raise SourceSandboxUnavailable(f"Windows refused to create the AppContainer test process (error {error}).")

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        kernel32.TerminateProcess(process.hProcess, 1)
        kernel32.CloseHandle(process.hThread)
        kernel32.CloseHandle(process.hProcess)
        raise SourceSandboxUnavailable("Could not create the bounded source-test Job Object.")
    try:
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.PerProcessUserTimeLimit = int(timeout_seconds * 10_000_000)
        limits.BasicLimitInformation.LimitFlags = 0x00000002 | 0x00000008 | 0x00000100 | 0x00002000
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_limit_bytes
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise SourceSandboxUnavailable("Could not set source-test process and memory limits.")
        if not kernel32.AssignProcessToJobObject(job, process.hProcess):
            error = ctypes.get_last_error()
            raise SourceSandboxUnavailable(f"Could not apply the bounded Job Object (error {error}).")
        if kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
            raise SourceSandboxUnavailable("Could not resume the AppContainer test process.")
        wait_ms = min(0xFFFFFFFE, max(1, int(timeout_seconds * 1000)))
        wait_result = kernel32.WaitForSingleObject(process.hProcess, wait_ms)
        timed_out = wait_result == 0x00000102
        if timed_out:
            kernel32.TerminateJobObject(job, 124)
            kernel32.WaitForSingleObject(process.hProcess, 5000)
        elif wait_result != 0:
            raise SourceSandboxUnavailable(f"Waiting for the AppContainer test process failed (status {wait_result}).")
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)):
            raise SourceSandboxUnavailable("Could not read the AppContainer test process exit code.")
        return {"exit_code": int(exit_code.value), "timed_out": timed_out,
                "process_id": int(process.dwProcessId), "identity": identity}
    finally:
        kernel32.TerminateJobObject(job, 0)
        kernel32.CloseHandle(job)
        kernel32.CloseHandle(process.hThread)
        kernel32.CloseHandle(process.hProcess)


def build_scratch_runtime(source_root: Path, writable_root: Path, runtime_root: Path) -> tuple[Path, list[Path]]:
    """Prepare a private Python runtime view and return read-only mount roots."""
    manifest_path = runtime_root / "manifest.json"
    if not manifest_path.is_file():
        raise SourceSandboxUnavailable("CADYX native runtime manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    python_root = runtime_root / "python"
    if not (python_root / "python.exe").is_file():
        raise SourceSandboxUnavailable("CADYX isolated Python runtime is missing.")
    scratch_python = source_root / ".sandbox-runtime" / "python"
    scratch_python.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(python_root, scratch_python, dirs_exist_ok=True)
    pth_files = list(scratch_python.glob("python*_._pth"))
    if not pth_files:
        pth_files = list(scratch_python.glob("python*._pth"))
    if len(pth_files) != 1:
        raise SourceSandboxUnavailable("The isolated Python runtime path file is ambiguous.")
    pth_files[0].write_text(
        f"{pth_files[0].stem}.zip\n.\n{source_root.resolve()}\n",
        encoding="utf-8",
    )
    remapped = {
        **manifest,
        "dll_directories": [str((runtime_root / relative).resolve()) for relative in manifest["dll_directories"]],
        "bridge": str((runtime_root / manifest["bridge"]).resolve()),
        "scan_bridge": str((runtime_root / manifest["scan_bridge"]).resolve()),
    }
    sandbox_runtime = source_root / ".runtime"
    sandbox_runtime.mkdir(exist_ok=True)
    (sandbox_runtime / "manifest.json").write_text(
        json.dumps(remapped, separators=(",", ":")), encoding="utf-8",
    )
    read_only = [source_root, scratch_python]
    read_only.extend(Path(path) for path in remapped["dll_directories"])
    read_only.extend((Path(remapped["bridge"]).parent, Path(remapped["scan_bridge"]).parent))
    for path in read_only:
        if not path.exists():
            raise SourceSandboxUnavailable(f"Required AppContainer read-only runtime path is missing: {path}.")
    writable_root.mkdir(parents=True, exist_ok=True)
    return scratch_python / "python.exe", list(dict.fromkeys(read_only))
