import json
import shutil
import time
from pathlib import Path


class OwnerLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        import fcntl

        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("Prediction writer already owns this contour") from None
        return self

    def __exit__(self, *args):
        self.handle.close()


def memory_available():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable unavailable")


def data_size(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def disk_gate(total, free, used, preflight=False):
    reserve = max(20_000_000_000, total * 0.25)
    if free < reserve or used >= 20_000_000_000:
        raise RuntimeError("Prediction disk safety threshold reached")
    if preflight and free < reserve + (20_000_000_000 - used) + 5_000_000_000:
        raise RuntimeError("Insufficient disk for prediction budget, reserve and runtime buffer")
    return (total - free) / total >= 0.70


class Safety:
    def __init__(self, root):
        self.root = root
        self.initial_bytes = data_size(root)
        self.state_sizes = {p: p.stat().st_size for p in (root / "state").iterdir() if p.is_file()}
        self.last_check = 0
        self.high_cpu_since = None
        self.cpu = None
        usage = shutil.disk_usage(root)
        disk_gate(usage.total, usage.free, self.initial_bytes, preflight=True)
        if memory_available() < 3_000_000_000:
            raise RuntimeError("MemAvailable below 3 GB")

    def __call__(self, journal):
        now = time.monotonic()
        if now - self.last_check < 5:
            return
        self.last_check = now
        usage = shutil.disk_usage(self.root)
        # Closed counters + only active descriptors: no historical directory scan per tick.
        measured = sum(v["stored_bytes"] for v in journal.metrics()["streams"].values())
        sidecars = sum(
            (journal.directory / name).stat().st_size
            for name in (
                "run.json",
                "segments.json",
                "live.json",
                "live.html",
                "metrics.json",
                "live.json.tmp",
                "live.html.tmp",
                "metrics.json.tmp",
            )
            if (journal.directory / name).exists()
        )
        state_growth = sum(
            max(0, p.stat().st_size - self.state_sizes.get(p, 0))
            for p in (self.root / "state").iterdir()
            if p.is_file()
        )
        # Stop early enough to cover one burst between five-second checks.
        budgeted = self.initial_bytes + measured + sidecars + state_growth + 100_000_000
        warning = disk_gate(usage.total, usage.free - 100_000_000, budgeted)
        if memory_available() < 3_000_000_000:
            raise RuntimeError("MemAvailable below 3 GB; controlled prediction stop")
        values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        total, idle = sum(values), values[3] + values[4]
        if self.cpu:
            busy = 1 - (idle - self.cpu[1]) / max(1, total - self.cpu[0])
            if busy >= 0.70:
                self.high_cpu_since = self.high_cpu_since or now
                if now - self.high_cpu_since >= 30:
                    raise RuntimeError("Host CPU >=70% sustained 30s; controlled prediction stop")
            else:
                self.high_cpu_since = None
        self.cpu = total, idle
        if warning:
            journal.append("resource_warnings", dict(reason="FILESYSTEM_70_PERCENT"))


def read_status(state_root):
    pointer = state_root / "current.json"
    if not pointer.exists():
        return dict(status="NOT_STARTED")
    identity = json.loads(pointer.read_text())
    path = Path(identity["output"])
    live = json.loads((path / "live.json").read_text()) if (path / "live.json").exists() else {}
    age = time.time() - (path / "live.json").stat().st_mtime if live else None
    if live.get("status") == "LIVE" and (age is None or age > 10):
        live["status"] = "STALE"
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if identity["boot_id"] != boot:
        live["status"] = "STALE_BOOT"
    return dict(identity=identity, live=live, age_seconds=age)
