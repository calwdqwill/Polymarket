"""Freeze interrupted research without writing to its source directory."""

import argparse
import gzip
import hashlib
import json
import shutil
import zlib
from datetime import datetime, timezone
from pathlib import Path

from app.prediction.live_storage import atomic_json
from app.scripts.freeze_prediction import freeze


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def salvage(data):
    """Recover complete JSONL records from a single interrupted gzip member.

    Offsets refer to decompressed bytes; compressed truncation has no equivalent
    record boundary. A bad compressed block is rejected, never silently skipped.
    """
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = decoder.decompress(data)
    if decoder.unused_data:
        raise ValueError("Unexpected concatenated gzip member or trailing bytes")
    boundary = decoded.rfind(b"\n") + 1
    complete = decoded[:boundary]
    last, count = None, 0
    for line in complete.splitlines():
        last = json.loads(line)
        count += 1
    if decoded[boundary:].strip():
        try:
            json.loads(decoded[boundary:])
        except (ValueError, UnicodeDecodeError):
            pass
        else:
            raise ValueError("Complete JSON without newline requires explicit review")
    return complete, dict(
        gzip_eof=decoder.eof,
        decompressed_bytes=len(decoded),
        truncation_offset_uncompressed=boundary,
        discarded_incomplete_record_bytes=len(decoded) - boundary,
        complete_records=count,
        last_complete_record=last,
        compressed_bytes=len(data),
        compressed_truncation_offset=None,
    )


def recover(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    evidence = destination / "source-metadata"
    evidence.mkdir(exist_ok=False)
    manifest = json.loads((source / "segments.json").read_text())
    files = {}
    for file in sorted(source.iterdir()):
        if file.is_file():
            files[file.name] = dict(sha256=sha256(file), bytes=file.stat().st_size, mtime_ns=file.stat().st_mtime_ns)
            if not file.name.endswith(".jsonl.gz"):
                shutil.copy2(file, evidence / file.name)
    for file in source.parent.glob(source.name + ".*"):
        if file.is_file():
            shutil.copy2(file, evidence / file.name)
    atomic_json(
        destination / "frozen-manifest.json",
        dict(
            source=str(source.resolve()),
            captured_at=datetime.now(timezone.utc),
            segments=manifest,
            files=files,
        ),
    )
    print("Original hashes and metadata preserved", flush=True)
    repaired = destination / "repaired-tail"
    repaired.mkdir()
    tails = {}
    for file in sorted(source.glob(f"*.{manifest['active']:06d}.jsonl.gz")):
        try:
            complete, detail = salvage(file.read_bytes())
            target = repaired / file.name
            with target.open("wb") as handle:
                with gzip.GzipFile(filename="", fileobj=handle, mode="wb", mtime=0) as output:
                    output.write(complete)
            detail.update(
                original_sha256=files[file.name]["sha256"],
                repaired_sha256=sha256(target),
                accepted_for_main_analysis=False,
            )
            tails[file.name] = detail
        except (ValueError, zlib.error) as exc:
            tails[file.name] = dict(
                error=str(exc), original_sha256=files[file.name]["sha256"], accepted_for_main_analysis=False
            )
    atomic_json(destination / "tail-recovery.json", tails)
    print("Active tail inspected; freezing closed segments", flush=True)
    result = freeze(source, destination / "dataset")
    frozen = json.loads((destination / "dataset" / "prefix.json").read_text())
    for name, digest in frozen["sha256"].items():
        if digest != files[name]["sha256"]:
            raise ValueError(f"Source changed during freeze: {name}")
    atomic_json(destination / "freeze-result.json", result)
    metrics = json.loads((destination / "dataset" / "metrics.json").read_text())
    closed_count = sum(v["messages"] for v in metrics["streams"].values())
    tail_count = sum(v.get("complete_records", 0) for v in tails.values())
    last = max(
        (v["last_complete_record"] for v in tails.values() if v.get("last_complete_record")),
        key=lambda r: r["local_ordinal"],
        default=None,
    )
    atomic_json(
        destination / "record-accounting.json",
        dict(
            closed_records=closed_count,
            repaired_tail_records=tail_count,
            total_complete_records=closed_count + tail_count,
            last_ordinal=last["local_ordinal"] if last else None,
            last_record=last,
            scope="Record count compared to final ordinal; not measurement of network or queue drops",
        ),
    )
    print(json.dumps(result), flush=True)


def verify_source(source, destination):
    manifest = json.loads((destination / "frozen-manifest.json").read_text())
    if source.resolve() != Path(manifest["source"]).resolve():
        raise ValueError("Source directory does not match the forensic manifest")
    mismatches = []
    for name, original in manifest["files"].items():
        file = source / name
        if not file.exists() or sha256(file) != original["sha256"]:
            mismatches.append(name)
    result = dict(
        checked_files=len(manifest["files"]),
        mismatches=mismatches,
        unchanged=not mismatches,
        checked_at=datetime.now(timezone.utc),
    )
    atomic_json(destination / "source-integrity.json", result)
    if mismatches:
        raise ValueError(f"Original source changed: {mismatches}")
    print(json.dumps(result, default=str), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        verify_source(args.source, args.destination)
    else:
        recover(args.source, args.destination)
