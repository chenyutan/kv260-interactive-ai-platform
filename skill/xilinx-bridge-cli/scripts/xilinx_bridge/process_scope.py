"""Contain and clean up only the descendants created by one opted-in Job.

Windows callers MUST use ``creationflags`` when spawning, then call ``attach``
before waiting. A suspended root is assigned before its first instruction, so
ordinary CreateProcess descendants cannot race assignment. Windows services/WMI
that create processes outside this tree are outside this containment contract.
POSIX callers use start_new_session=True; a child that explicitly changes its
process group/session is outside the process-group cleanup contract.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


class OwnedProcessTree:
    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.creationflags = 0
        self.backend = "disabled"
        self._pid = None
        self._job = None
        self._result = None
        if self.enabled:
            self.backend = "windows_job_object" if sys.platform == "win32" else "posix_process_group"
            if sys.platform == "win32":
                self._init_windows()
                self.creationflags = 0x00000004  # CREATE_SUSPENDED

    def _init_windows(self) -> None:
        import ctypes as c
        from ctypes import wintypes as w

        class BasicLimit(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class IoCounters(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimit(c.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]

        class Accounting(c.Structure):
            _fields_ = [(name, c.c_int64) for name in ("TotalUserTime", "TotalKernelTime",
                        "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
            _fields_ += [(name, w.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses",
                         "ActiveProcesses", "TotalTerminatedProcesses")]

        class ThreadEntry(c.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                        ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]

        k = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            getattr(k, name).argtypes = args
            getattr(k, name).restype = result
        self._c, self._k = c, k
        self._Accounting, self._ThreadEntry = Accounting, ThreadEntry
        self._job = k.CreateJobObjectW(None, None)  # unnamed, non-inheritable
        if not self._job:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE; no breakaway
        if not k.SetInformationJobObject(self._job, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            k.CloseHandle(self._job)
            self._job = None
            raise error

    def _resume_windows_root(self, pid: int) -> None:
        c, k = self._c, self._k
        snapshot = k.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self._ThreadEntry()
            entry.dwSize = c.sizeof(entry)
            present = k.Thread32First(snapshot, c.byref(entry))
            thread_ids = []
            while present:
                if entry.th32OwnerProcessID == pid:
                    thread_ids.append(entry.th32ThreadID)
                present = k.Thread32Next(snapshot, c.byref(entry))
            if len(thread_ids) != 1:
                raise RuntimeError("Owned root must have exactly one suspended startup thread")
            handle = k.OpenThread(0x0002, False, thread_ids[0])  # THREAD_SUSPEND_RESUME
            if not handle:
                raise c.WinError(c.get_last_error())
            try:
                previous = k.ResumeThread(handle)
                if previous != 1:
                    raise RuntimeError(f"Owned root was not created with CREATE_SUSPENDED (count={previous})")
            finally:
                k.CloseHandle(handle)
        finally:
            k.CloseHandle(snapshot)

    def attach(self, proc: subprocess.Popen) -> None:
        if not self.enabled:
            return
        if self._pid is not None or self._result is not None:
            raise RuntimeError("Process scope can attach only one root")
        try:
            if self.backend == "windows_job_object":
                self._pid = proc.pid
                c, k = self._c, self._k
                process = k.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
                if not process:
                    raise c.WinError(c.get_last_error())
                try:
                    if not k.AssignProcessToJobObject(self._job, process):
                        raise c.WinError(c.get_last_error())
                    self._resume_windows_root(proc.pid)
                finally:
                    k.CloseHandle(process)
            else:
                if os.getpgid(proc.pid) != proc.pid:
                    raise RuntimeError("Owned POSIX root must start in a new session/process group")
                self._pid = proc.pid
        except BaseException:
            # This exact Popen is ours. A Windows root is still suspended on
            # assignment failure, and cannot yet have launched any children.
            try:
                proc.kill()
                proc.wait(timeout=5)
            finally:
                self.close()
            raise

    def _active_windows(self) -> int:
        accounting = self._Accounting()
        if not self._k.QueryInformationJobObject(self._job, 1, self._c.byref(accounting),
                                                 self._c.sizeof(accounting), None):
            raise self._c.WinError(self._c.get_last_error())
        return accounting.ActiveProcesses

    @staticmethod
    def _posix_group_members(pgid: int) -> dict:
        """Verify group absence, or Linux group membership excluding dead tasks.

        A delivered SIGKILL is not proof of exit (for example an uninterruptible
        task can remain live). Linux /proc exposes the state of each member.
        On other POSIX systems conservatively require the whole group to vanish.
        """
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return {"live_processes": 0, "zombie_processes": 0, "verification": "group_absent"}
        if not sys.platform.startswith("linux"):
            return {"live_processes": None, "verification": "group_still_present"}
        live, zombies = 0, 0
        try:
            with os.scandir("/proc") as entries:
                for entry in entries:
                    if not entry.name.isdigit():
                        continue
                    try:
                        with open(entry.path + "/stat", encoding="utf-8") as stream:
                            fields = stream.read().rsplit(")", 1)[1].split()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if int(fields[2]) == pgid:
                        if fields[0] in {"Z", "X"}:
                            zombies += 1
                        else:
                            live += 1
        except (OSError, ValueError, IndexError) as exc:
            return {"live_processes": None, "verification": "proc_membership_unavailable", "error": str(exc)}
        return {"live_processes": live, "zombie_processes": zombies,
                "verification": "proc_group_no_live_members" if not live else "proc_group_has_live_members"}

    def close(self) -> dict:
        if self._result is not None:
            return dict(self._result)
        result = {"enabled": self.enabled, "backend": self.backend, "cleanup_complete": True}
        try:
            if self._job:
                if not self._k.TerminateJobObject(self._job, 1):
                    raise self._c.WinError(self._c.get_last_error())
                deadline = time.monotonic() + 5
                while self._active_windows() and time.monotonic() < deadline:
                    time.sleep(0.025)
                result["active_processes"] = self._active_windows()
                result["cleanup_complete"] = result["active_processes"] == 0
            elif self.enabled and self._pid is not None:
                try:
                    os.killpg(self._pid, signal.SIGKILL)
                    result["signal_delivered"] = True
                except ProcessLookupError:
                    result["signal_delivered"] = False
                deadline = time.monotonic() + 5
                membership = self._posix_group_members(self._pid)
                while membership["live_processes"] != 0 and time.monotonic() < deadline:
                    time.sleep(0.025)
                    membership = self._posix_group_members(self._pid)
                result["cleanup_complete"] = membership["live_processes"] == 0
                result["membership"] = membership
        except Exception as exc:
            result["cleanup_complete"] = False
            result["error"] = str(exc)
        finally:
            if self._job:
                if not self._k.CloseHandle(self._job):
                    result["cleanup_complete"] = False
                    result["error"] = "Could not close owned Windows JobObject handle"
                self._job = None
        self._result = result
        return dict(result)
