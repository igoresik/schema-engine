"""A Windows jail for the transform's child process.

The child runs with a restricted, Low-integrity token and inside a Job Object:

- Low integrity: Windows refuses its writes and deletes on anything that isn't marked Low,
  so it can't change or remove your files. Only the work folder is marked Low.
- No extra privileges (``DISABLE_MAX_PRIVILEGE``).
- Job Object: it can't start another process (``os.system`` and ``subprocess`` fail),
  its memory is capped, the desktop is off limits, and it dies if the app does.

Reading is still allowed, and so are network connections. Only the code check and the
socket patch in ``_runner`` stand in the way of those. On other systems nothing here applies.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes as wt
from pathlib import Path

WINDOWS = sys.platform == "win32"
MEMORY_LIMIT = 2 * 1024**3

_LOW_INTEGRITY = "S-1-16-4096"
_TOKEN_DUPLICATE, _TOKEN_QUERY, _TOKEN_ADJUST_DEFAULT, _TOKEN_ASSIGN_PRIMARY = 0x2, 0x8, 0x80, 0x1
_TOKEN_INTEGRITY_LEVEL = 25
_SE_GROUP_INTEGRITY = 0x20
_DISABLE_MAX_PRIVILEGE = 0x1
_CREATE_SUSPENDED, _CREATE_NO_WINDOW, _CREATE_UNICODE_ENVIRONMENT = 0x4, 0x08000000, 0x400
_STARTF_USESTDHANDLES = 0x100
_WAIT_TIMEOUT = 0x102
_JOB_EXTENDED_LIMIT, _JOB_UI_RESTRICTIONS = 9, 4
_LIMIT_ACTIVE_PROCESS, _LIMIT_PROCESS_MEMORY, _LIMIT_KILL_ON_CLOSE, _LIMIT_DIE_ON_EXCEPTION = (
    0x8,
    0x100,
    0x2000,
    0x400,
)
_UI_ALL = (
    0xFF  # handles, clipboard, system parameters, display settings, atoms, desktop, exit windows
)


class JailError(OSError):
    """The jail could not be set up, so nothing was run."""


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wt.LPVOID), ("Attributes", wt.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", wt.LPVOID),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _libs():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    kernel.ResumeThread.argtypes = [wt.HANDLE]
    kernel.ResumeThread.restype = wt.DWORD
    kernel.WaitForSingleObject.restype = wt.DWORD
    advapi.GetLengthSid.argtypes = [wt.LPVOID]
    advapi.GetLengthSid.restype = wt.DWORD
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes = [wt.LPVOID]
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi.CreateRestrictedToken.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.DWORD,
        wt.LPVOID,
        wt.DWORD,
        wt.LPVOID,
        wt.DWORD,
        wt.LPVOID,
        ctypes.POINTER(wt.HANDLE),
    ]
    advapi.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.LPVOID)]
    advapi.SetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
    advapi.CreateProcessAsUserW.argtypes = [
        wt.HANDLE,
        wt.LPCWSTR,
        wt.LPWSTR,
        wt.LPVOID,
        wt.LPVOID,
        wt.BOOL,
        wt.DWORD,
        wt.LPVOID,
        wt.LPCWSTR,
        ctypes.POINTER(_StartupInfo),
        ctypes.POINTER(_ProcessInfo),
    ]
    return kernel, advapi


def _check(ok, what):
    if not ok:
        raise JailError(f"{what} failed (Windows error {ctypes.get_last_error()})")


def mark_low(folder: Path) -> None:
    """Let the jailed child write in this folder (and what is created in it later), nowhere else."""
    result = subprocess.run(
        ["icacls", str(folder), "/setintegritylevel", "(OI)(CI)L"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise JailError(
            f"could not prepare the work folder: {result.stdout.strip() or result.stderr.strip()}"
        )


def _low_token(kernel, advapi):
    token, restricted = wt.HANDLE(), wt.HANDLE()
    _check(
        advapi.OpenProcessToken(
            kernel.GetCurrentProcess(),
            _TOKEN_DUPLICATE | _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT | _TOKEN_ASSIGN_PRIMARY,
            ctypes.byref(token),
        ),
        "OpenProcessToken",
    )
    try:
        _check(
            advapi.CreateRestrictedToken(
                token, _DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None, ctypes.byref(restricted)
            ),
            "CreateRestrictedToken",
        )
    finally:
        kernel.CloseHandle(token)
    sid = wt.LPVOID()
    _check(
        advapi.ConvertStringSidToSidW(_LOW_INTEGRITY, ctypes.byref(sid)), "ConvertStringSidToSid"
    )
    try:
        label = _SidAndAttributes(sid, _SE_GROUP_INTEGRITY)
        _check(
            advapi.SetTokenInformation(
                restricted,
                _TOKEN_INTEGRITY_LEVEL,
                ctypes.byref(label),
                ctypes.sizeof(label) + advapi.GetLengthSid(sid),
            ),
            "SetTokenInformation",
        )
    except JailError:
        kernel.CloseHandle(restricted)
        raise
    finally:
        kernel.LocalFree(sid)
    return restricted


def _job(kernel):
    job = kernel.CreateJobObjectW(None, None)
    _check(job, "CreateJobObject")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        _LIMIT_ACTIVE_PROCESS
        | _LIMIT_PROCESS_MEMORY
        | _LIMIT_KILL_ON_CLOSE
        | _LIMIT_DIE_ON_EXCEPTION
    )
    # a venv's python.exe is a launcher that starts the real interpreter, so it needs two slots
    limits.BasicLimitInformation.ActiveProcessLimit = 2 if sys.prefix != sys.base_prefix else 1
    limits.ProcessMemoryLimit = MEMORY_LIMIT
    _check(
        kernel.SetInformationJobObject(
            job, _JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)
        ),
        "SetInformationJobObject (limits)",
    )
    ui = wt.DWORD(_UI_ALL)
    _check(
        kernel.SetInformationJobObject(
            job, _JOB_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui)
        ),
        "SetInformationJobObject (desktop)",
    )
    return job


def run(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: float, stdout, stderr
) -> int:
    """Run ``command`` in the jail and return its exit code. ``stdout``/``stderr`` are open files.

    Raises ``subprocess.TimeoutExpired`` (the whole job is killed) or ``JailError``
    before anything runs.
    """
    kernel, advapi = _libs()
    token = _low_token(kernel, advapi)
    job = None
    info = _ProcessInfo()
    try:
        job = _job(kernel)
        for handle in (stdout.fileno(), stderr.fileno()):
            os.set_handle_inheritable(_osfhandle(handle), True)
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = _STARTF_USESTDHANDLES
        startup.hStdInput = None
        startup.hStdOutput = wt.HANDLE(_osfhandle(stdout.fileno()))
        startup.hStdError = wt.HANDLE(_osfhandle(stderr.fileno()))
        block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        _check(
            advapi.CreateProcessAsUserW(
                token,
                None,
                line,
                None,
                None,
                True,
                _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT,
                ctypes.cast(block, wt.LPVOID),
                str(cwd),
                ctypes.byref(startup),
                ctypes.byref(info),
            ),
            "CreateProcessAsUser",
        )
        try:
            _check(kernel.AssignProcessToJobObject(job, info.hProcess), "AssignProcessToJobObject")
            _check(kernel.ResumeThread(info.hThread) != 0xFFFFFFFF, "ResumeThread")
        except JailError:
            kernel.TerminateJobObject(job, 1)
            raise
        if kernel.WaitForSingleObject(info.hProcess, int(timeout * 1000)) == _WAIT_TIMEOUT:
            kernel.TerminateJobObject(job, 1)
            kernel.WaitForSingleObject(
                info.hProcess, 5000
            )  # let it release its files before the folder is removed
            raise subprocess.TimeoutExpired(command, timeout)
        code = wt.DWORD()
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        for handle in (info.hProcess, info.hThread, token):
            if handle:
                kernel.CloseHandle(handle)
        if job:
            kernel.CloseHandle(job)  # kill-on-close also ends anything left behind


def _osfhandle(fd: int) -> int:
    import msvcrt

    return msvcrt.get_osfhandle(fd)
