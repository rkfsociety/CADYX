"""Windows AppContainer runner for untrusted CADYX source-change tests.

The runner uses the documented CreateProcessW security-capabilities interface,
with a unique AppContainer SID, explicit file ACLs, no capabilities, and a
bounded Job Object. It never falls back to an ordinary process.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .errors import CadyxError, ValidationError


class SourceSandboxUnavailable(CadyxError):
    """The current host cannot provide the required Windows sandbox."""

    code = "SOURCE_SANDBOX_UNAVAILABLE"
    http_status = 503


def _decode_windows_command_output(value: bytes) -> str:
    """Decode localized Windows command output without failing on code-page bytes."""
    return value.decode("mbcs", errors="replace")


def run_appcontainer_process(
    application: Path,
    arguments: Sequence[str],
    *,
    current_directory: Path,
    read_only: Sequence[Path],
    environment: dict[str, str],
    result_files: Sequence[str] = (),
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
    paths_ro = [str(path.resolve()) for path in read_only]
    if any(Path(name).is_absolute() or ".." in Path(name).parts for name in result_files):
        raise ValidationError("AppContainer result file names must stay inside its temporary directory.")
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

    class StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class SecurityCapabilities(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                    ("CapabilityCount", ctypes.c_uint32), ("Reserved", ctypes.c_uint32)]

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", ctypes.c_uint32), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", ctypes.c_int)]

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
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.POINTER(SecurityAttributes), ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p

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
    startup = StartupInfoEx()
    startup.StartupInfo.cb = ctypes.sizeof(StartupInfoEx)
    process = ProcessInformation()
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    profile_name = "CadyxSource_" + uuid4().hex
    package_sid = ctypes.c_void_p()
    create_profile = userenv.CreateAppContainerProfile
    create_profile.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                               ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    create_profile.restype = ctypes.c_long
    result = create_profile(profile_name, "CADYX source tests", "Temporary isolated source tests",
                            None, 0, ctypes.byref(package_sid))
    if result < 0 or not package_sid:
        raise SourceSandboxUnavailable(f"Windows could not create the source-test AppContainer profile (HRESULT 0x{result & 0xFFFFFFFF:08X}).")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    sid_text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(package_sid, ctypes.byref(sid_text)):
        userenv.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
        userenv.DeleteAppContainerProfile.restype = ctypes.c_long
        userenv.DeleteAppContainerProfile(profile_name)
        advapi32.FreeSid(package_sid)
        raise SourceSandboxUnavailable(f"Windows could not format the source-test AppContainer SID (error {ctypes.get_last_error()}).")
    sid = sid_text.value
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))

    get_profile_folder = userenv.GetAppContainerFolderPath
    get_profile_folder.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p)]
    get_profile_folder.restype = ctypes.c_long
    profile_folder = ctypes.c_wchar_p()
    result = get_profile_folder(sid, ctypes.byref(profile_folder))
    if result < 0 or not profile_folder:
        userenv.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
        userenv.DeleteAppContainerProfile.restype = ctypes.c_long
        userenv.DeleteAppContainerProfile(profile_name)
        ctypes.WinDLL("advapi32", use_last_error=True).FreeSid(package_sid)
        raise SourceSandboxUnavailable(f"Windows could not locate the AppContainer scratch profile (HRESULT 0x{result & 0xFFFFFFFF:08X}).")
    container_data = Path(profile_folder.value)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree(ctypes.cast(profile_folder, ctypes.c_void_p))
    try:
        container_temp = container_data / "Temp"
        container_temp.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        userenv.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
        userenv.DeleteAppContainerProfile.restype = ctypes.c_long
        userenv.DeleteAppContainerProfile(profile_name)
        ctypes.WinDLL("advapi32", use_last_error=True).FreeSid(package_sid)
        raise SourceSandboxUnavailable(f"Windows could not prepare the AppContainer scratch directory: {exc}") from exc
    # Pass the caller's host TEMP/LOCALAPPDATA values. Windows redirects these
    # to this profile when it creates the AppContainer token; passing the
    # already redirected path here would apply that redirection twice.

    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    acl_paths: list[Path] = []
    attribute_list = None
    job = None
    diagnostic_handle = None
    delete_profile = userenv.DeleteAppContainerProfile
    delete_profile.argtypes = [ctypes.c_wchar_p]
    delete_profile.restype = ctypes.c_long

    def acl(*args: str) -> None:
        completed = subprocess.run([str(icacls), *args], capture_output=True,
                                   creationflags=0x08000000)
        if completed.returncode != 0:
            detail = _decode_windows_command_output(
                completed.stderr or completed.stdout
            ).strip()[-500:]
            raise SourceSandboxUnavailable(f"Windows could not apply source-test AppContainer ACLs (exit {completed.returncode}): {detail}")

    try:
        for path in dict.fromkeys(Path(item) for item in paths_ro):
            acl_paths.append(path)
            acl(str(path), "/grant", f"*{sid}:(OI)(CI)(RX)", "/T", "/C", "/Q")
        acl_paths.append(container_data)
        acl(str(container_data), "/grant", f"*{sid}:(OI)(CI)(RX)", "/T", "/C", "/Q")
        acl_paths.append(container_temp)
        acl(str(container_temp), "/grant", f"*{sid}:(OI)(CI)(F)", "/T", "/C", "/Q")
        acl(str(container_temp), "/grant", "*S-1-3-0:(OI)(CI)(IO)(F)", "/T", "/C", "/Q")
        acl(str(container_temp), "/setintegritylevel", "(OI)(CI)L", "/T", "/C", "/Q")

        diagnostics_path = container_temp / "process-output.log"
        security_attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, 1)
        diagnostic_handle = kernel32.CreateFileW(
            str(diagnostics_path), 0x40000000, 0x00000001, ctypes.byref(security_attributes),
            2, 0x00000100, None,
        )
        if diagnostic_handle in (None, ctypes.c_void_p(-1).value):
            raise SourceSandboxUnavailable(f"Windows could not open the bounded AppContainer diagnostic channel (error {ctypes.get_last_error()}).")
        kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t)]
        kernel32.InitializeProcThreadAttributeList.restype = ctypes.c_int
        kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t,
                                                       ctypes.c_void_p, ctypes.c_size_t,
                                                       ctypes.c_void_p, ctypes.c_void_p]
        kernel32.UpdateProcThreadAttribute.restype = ctypes.c_int
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
        attribute_list = ctypes.create_string_buffer(attribute_size.value)
        if not kernel32.InitializeProcThreadAttributeList(attribute_list, 2, 0, ctypes.byref(attribute_size)):
            raise SourceSandboxUnavailable(f"Windows could not initialize AppContainer process attributes (error {ctypes.get_last_error()}).")
        startup.lpAttributeList = ctypes.cast(attribute_list, ctypes.c_void_p)
        capabilities = SecurityCapabilities(package_sid, None, 0, 0)
        if not kernel32.UpdateProcThreadAttribute(
            startup.lpAttributeList, 0, 0x00020009, ctypes.byref(capabilities),
            ctypes.sizeof(capabilities), None, None,
        ):
            raise SourceSandboxUnavailable(f"Windows could not attach AppContainer security capabilities (error {ctypes.get_last_error()}).")
        handle_list = (ctypes.c_void_p * 1)(diagnostic_handle)
        if not kernel32.UpdateProcThreadAttribute(
            startup.lpAttributeList, 0, 0x00020002, ctypes.cast(handle_list, ctypes.c_void_p),
            ctypes.sizeof(handle_list), None, None,
        ):
            raise SourceSandboxUnavailable(f"Windows could not restrict inherited diagnostic handles (error {ctypes.get_last_error()}).")
        startup.StartupInfo.dwFlags |= 0x00000100  # STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = None
        startup.StartupInfo.hStdOutput = diagnostic_handle
        startup.StartupInfo.hStdError = diagnostic_handle
        kernel32.CreateProcessW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32,
                                            ctypes.c_void_p, ctypes.c_wchar_p,
                                            ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInformation)]
        kernel32.CreateProcessW.restype = ctypes.c_int
        flags = 0x08000000 | 0x00000004 | 0x00000400 | 0x00080000
        if not kernel32.CreateProcessW(
            str(application), command_line, None, None, 1, flags,
            ctypes.cast(environment_buffer, ctypes.c_void_p), str(current_directory),
            ctypes.byref(startup), ctypes.byref(process),
        ):
            raise SourceSandboxUnavailable(f"Windows refused to create the AppContainer test process (error {ctypes.get_last_error()}).")

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise SourceSandboxUnavailable("Could not create the bounded source-test Job Object.")
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
        collected_files = {}
        for name in result_files:
            candidate = container_temp.joinpath(*Path(name).parts)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            attributes = kernel32.GetFileAttributesW(str(candidate))
            if (resolved.parent != container_temp.resolve() or attributes == 0xFFFFFFFF
                    or attributes & 0x400):  # FILE_ATTRIBUTE_REPARSE_POINT
                raise SourceSandboxUnavailable("An AppContainer test result was not a regular file inside its scratch directory.")
            collected_files[name] = resolved.read_text(encoding="utf-8", errors="replace")
        diagnostics = diagnostics_path.read_text(encoding="utf-8", errors="replace")[-12000:]
        return {"exit_code": int(exit_code.value), "timed_out": timed_out,
                "process_id": int(process.dwProcessId), "identity": profile_name,
                "files": collected_files, "diagnostics": diagnostics}
    finally:
        if process.hProcess:
            kernel32.TerminateProcess(process.hProcess, 1)
        if job:
            kernel32.TerminateJobObject(job, 0)
            kernel32.CloseHandle(job)
        if process.hThread:
            kernel32.CloseHandle(process.hThread)
        if process.hProcess:
            kernel32.CloseHandle(process.hProcess)
        if attribute_list is not None:
            kernel32.DeleteProcThreadAttributeList(attribute_list)
        if diagnostic_handle:
            kernel32.CloseHandle(diagnostic_handle)
        cleanup_errors = []
        for path in reversed(acl_paths):
            completed = subprocess.run([str(icacls), str(path), "/remove:g", f"*{sid}", "/T", "/C", "/Q"],
                                       capture_output=True, creationflags=0x08000000)
            if completed.returncode != 0:
                cleanup_errors.append(_decode_windows_command_output(
                    completed.stderr or completed.stdout
                ).strip()[-300:])
        delete_result = delete_profile(profile_name)
        advapi32.FreeSid(package_sid)
        if cleanup_errors or delete_result < 0:
            raise SourceSandboxUnavailable("Windows could not fully remove temporary AppContainer permissions/profile; scratch data must remain isolated. "
                                           + "; ".join(cleanup_errors[-2:]))


def build_scratch_runtime(source_root: Path, runtime_root: Path) -> tuple[Path, list[Path]]:
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
    from .source_native import verified_bridge
    native_root = source_root if (source_root / "native").is_dir() else Path(__file__).resolve().parent.parent
    tested_bridge = verified_bridge(native_root, runtime_root)
    if tested_bridge is not None:
        remapped["bridge"] = str(tested_bridge.resolve())
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
    return scratch_python / "python.exe", list(dict.fromkeys(read_only))
