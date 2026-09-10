"""Copy only closed, immutable gzip segments from a running collector for analysis."""

import argparse
import gzip
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.prediction.live_storage import atomic_json


def freeze(source, destination):
    manifest = json.loads((source / "segments.json").read_text())
    closed = set(manifest["closed"])
    if not closed:
        raise ValueError("No finalized segments yet")
    destination.mkdir(parents=True, exist_ok=False)
    counts, logical, stored, hashes = Counter(), Counter(), Counter(), {}
    latest = None
    for file in sorted(source.glob("*.*.jsonl.gz")):
        stream, segment, *_ = file.name.split(".")
        if not segment.isdigit() or int(segment) not in closed:
            continue
        target = destination / file.name
        shutil.copy2(file, target)
        hashes[file.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        stored[stream] += target.stat().st_size
        with gzip.open(target, "rb") as handle:
            for line in handle:
                record = json.loads(line)
                counts[stream] += 1
                logical[stream] += len(line)
                stamp = datetime.fromisoformat(record["received_timestamp"])
                latest = max(stamp, latest) if latest else stamp
    if latest is None:
        raise ValueError("Closed segments contain no records")
    run = json.loads((source / "run.json").read_text())
    seconds = (latest - datetime.fromisoformat(run["started"])).total_seconds()
    atomic_json(destination / "run.json", run)
    atomic_json(destination / "live.json", dict(status="FROZEN_PREFIX", timestamp=latest))
    atomic_json(
        destination / "metrics.json",
        dict(
            duration_seconds=seconds,
            streams={
                stream: dict(
                    messages=counts[stream],
                    bytes=logical[stream],
                    stored_bytes=stored[stream],
                    mb_per_hour=logical[stream] / seconds * 3600 / 1e6,
                )
                for stream in counts
            },
        ),
    )
    atomic_json(
        destination / "prefix.json",
        dict(source=str(source.resolve()), closed_segments=sorted(closed), end=latest, sha256=hashes),
    )
    return dict(directory=str(destination.resolve()), duration_seconds=seconds, segments=len(closed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Freeze closed segments without interrupting live collection")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(args.source, args.destination)))
