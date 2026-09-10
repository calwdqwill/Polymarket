import argparse
import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.prediction.live_storage import atomic_json
from app.prediction.service_runtime import OwnerLock, Safety
from app.scripts.collect_prediction import collect


async def run(root, seconds):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    safety = Safety(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    output = root / "raw" / run_id
    identity = dict(
        run_id=run_id,
        output=str(output),
        pid=os.getpid(),
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        process_start_ticks=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
        release=os.environ.get("PREDICTION_RELEASE", "unknown"),
    )
    atomic_json(root / "state" / "current.json", identity)
    await collect(output, seconds=seconds, stop_event=stop, safety=safety, identity=identity)


def main():
    parser = argparse.ArgumentParser(description="Bounded Linux public prediction collector")
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/poly-crypto-prediction"))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 7200:
        parser.error("Staging duration must be 1..7200 seconds")
    with OwnerLock(args.root / "state" / "writer.lock"):
        asyncio.run(run(args.root, args.seconds))


if __name__ == "__main__":
    main()
