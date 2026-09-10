"""Correct derived episode UTC labels using local observation clock anchors."""

import argparse
import gzip
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from app.prediction.live_storage import atomic_json
from app.prediction.storage import dumps
from app.scripts.analyze_prediction import records
from app.scripts.analyze_prediction_research import epoch_ns
from app.scripts.recover_prediction import sha256


def utc_text(ns):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ns // 1000)).isoformat()


def clock_map(endpoints, observations):
    """Interpolate only within the interval anchored by the preceding observation."""
    pending = iter(sorted(endpoints))
    target = next(pending, None)
    previous, result = None, {}
    for record in observations:
        mono = record["monotonic_received_ns"]
        while target is not None and target < mono:
            if previous is None:
                raise ValueError("Episode starts before the first observation")
            result[target] = epoch_ns(previous["received_timestamp"]) + target - previous["monotonic_received_ns"]
            target = next(pending, None)
        previous = record
        if target is None:
            break
    while target is not None:
        if previous is None:
            raise ValueError("Missing observation timeline")
        result[target] = epoch_ns(previous["received_timestamp"]) + target - previous["monotonic_received_ns"]
        target = next(pending, None)
    return result


def repair(root):
    dataset = root / "dataset"
    original = dataset / "opportunities.jsonl.gz"
    target = root / "opportunities.jsonl.gz"
    if target.exists():
        raise ValueError("Corrected projection already exists; preserve it and use a new output bundle")
    endpoints = {e[field] for e in records(dataset, "opportunities") for field in ("start_ns", "end_ns")}
    mapping = clock_map(endpoints, records(dataset, "observations"))
    count, changed, max_shift = 0, 0, D(0)
    with target.open("wb") as handle, gzip.GzipFile(filename="", fileobj=handle, mode="wb", mtime=0) as output:
        for event in records(dataset, "opportunities"):
            count += 1
            original_utc = {field: event[field] for field in ("start", "end")}
            for field in ("start", "end"):
                corrected = mapping[event[field + "_ns"]]
                max_shift = max(max_shift, abs(D(corrected - epoch_ns(event[field])) / 1_000_000))
                event[field] = utc_text(corrected)
            event["start_time_to_expiry_seconds"] = (
                D((event["window"] + 300) * 1_000_000_000 - mapping[event["start_ns"]]) / 1_000_000_000
            )
            if event["start_time_to_expiry_seconds"] < 0 or event["start_time_to_expiry_seconds"] > 300:
                raise ValueError(f"Episode outside market window after clock correction: {event}")
            changed += any(event[field] != original_utc[field] for field in original_utc)
            output.write((dumps(event) + "\n").encode("utf-8"))
    result = dict(
        episodes=count,
        changed_utc_labels=changed,
        maximum_absolute_utc_shift_ms=max_shift,
        source_sha256=sha256(original),
        corrected_sha256=sha256(target),
        scope="Only UTC labels and start TTE corrected. Monotonic endpoints, lifetime, edge, capacity and episode membership unchanged.",
    )
    atomic_json(root / "episode-timestamp-repair.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(dumps(repair(parser.parse_args().directory)))
