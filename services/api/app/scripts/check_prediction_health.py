import json
import shutil
import subprocess
import time
from pathlib import Path

from app.prediction.live_storage import atomic_json
from app.prediction.service_runtime import memory_available, read_status


def snapshot():
    root = Path("/var/lib/poly-crypto-prediction")
    status = read_status(root / "state")
    usage = shutil.disk_usage(root)
    cgroup = Path("/sys/fs/cgroup/prediction.slice")
    result = dict(
        timestamp=time.time(),
        status=status,
        disk=usage._asdict(),
        mem_available=memory_available(),
        cpu=Path("/proc/stat").read_text().splitlines()[0],
        diskstats=Path("/proc/diskstats").read_text(),
        memory=Path("/proc/meminfo").read_text(),
        pressure={p.name: p.read_text() for p in Path("/proc/pressure").iterdir()},
        cgroup={
            name: (cgroup / name).read_text()
            for name in ("cpu.stat", "memory.current", "memory.events", "memory.stat", "io.stat", "pids.current")
            if (cgroup / name).exists()
        },
    )
    if "identity" in status:
        metrics = Path(status["identity"]["output"]) / "metrics.json"
        if metrics.exists():
            result["metrics"] = json.loads(metrics.read_text())
    result["containers"] = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}} {{.Status}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    with (root / "state" / "health-history.jsonl").open("a") as handle:
        handle.write(json.dumps(result) + "\n")
    atomic_json(root / "state" / "health.json", result)
    if result["mem_available"] < 3_000_000_000 or usage.free < max(20_000_000_000, usage.total * 0.25):
        subprocess.run(["systemctl", "stop", "--no-block", "prediction-collector.service"], check=True)


if __name__ == "__main__":
    snapshot()
