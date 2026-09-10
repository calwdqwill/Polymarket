"""OS process barriers and retryable I/O used only by the offline finalizer."""

import ctypes
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime

from app.prediction.live_storage import atomic_json


def retry_io(operation, timeout=60, interval=0.5):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except (PermissionError, FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)


def read_json(path):
    return retry_io(lambda: json.loads(path.read_text(encoding="utf-8-sig")))


def publish_status(path, value):
    def publish():
        if not atomic_json(path, value):
            raise PermissionError(f"Could not publish {path}")

    retry_io(publish)


@contextmanager
def finalizer_lock(path):
    """Advisory lock on our own sidecar; process exit releases it, including crashes."""
    with (path / "finalization.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another finalizer already owns this dataset") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another finalizer already owns this dataset") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class CollectorProcess:
    """Retain a Windows process handle, never request terminate/suspend/write rights."""

    def __init__(self, run):
        self.pid = int(run["pid"])
        if self.pid <= 0:
            raise ValueError("Invalid collector PID")
        self.handle = None
        self.absent = False
        if os.name != "nt":
            return
        from ctypes import wintypes as w

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.WaitForSingleObject.restype = w.DWORD
        self.kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        self.handle = self.kernel.OpenProcess(0x00100000 | 0x1000, False, self.pid)
        if not self.handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
                self.absent = True
                return
            raise ctypes.WinError(error)
        try:
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(
                self.handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            created_epoch = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11644473600
            started = datetime.fromisoformat(run["started"]).timestamp()
            if created_epoch > started + 0.01:
                # PID was reused after this collector wrote run.json.
                self.absent = True
                self.close()
            elif started - created_epoch > 60:
                raise ValueError("PID creation time does not match collector run; refusing analysis")
        except BaseException:
            self.close()
            raise

    def is_running(self):
        if self.absent:
            return False
        if os.name == "nt":
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result == 0:
                return False
            if result == 258:  # WAIT_TIMEOUT
                return True
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def wait_for_collector(run, wait, poll_seconds=5):
    with CollectorProcess(run) as process:
        while process.is_running():
            if not wait:
                raise ValueError("Collector is still running; use --wait")
            if time.time() > float(run["stop_at"]) + 600:
                raise TimeoutError("Collector is still running 10 minutes after stop_at; no data files opened")
            time.sleep(poll_seconds)


def completed_dataset(path):
    """Call only after process exit. Never open a .tmp or active raw segment."""
    live = read_json(path / "live.json")
    segments = read_json(path / "segments.json")
    if live.get("status") not in ("STOPPED", "FAILED"):
        raise ValueError("Collector did not publish a terminal live.json; data retained for manual recovery")
    closed = segments.get("closed")
    if (
        "active" not in segments
        or segments["active"] is not None
        or not isinstance(closed, list)
        or not closed
        or any(type(n) is not int or n < 0 for n in closed)
        or len(set(closed)) != len(closed)
    ):
        raise ValueError("Collector has not finalized gzip segments; data retained for manual recovery")
    for file in path.glob("*.*.jsonl.gz"):
        segment = file.name.removesuffix(".jsonl.gz").rsplit(".", 1)[1]
        if not segment.isdigit() or int(segment) not in closed:
            raise ValueError(f"Unclosed or unrecognized segment: {file.name}")
    read_json(path / "metrics.json")
    return live
