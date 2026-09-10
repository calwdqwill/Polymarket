"""Finalize the already-authorized research job after its collector closes the dataset."""

import argparse
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from app.scripts.analyze_prediction_research import analyze
from app.scripts.prediction_finalization_io import (
    completed_dataset,
    finalizer_lock,
    publish_status,
    read_json,
    retry_io,
    wait_for_collector,
)
from app.scripts.replay_prediction import replay
from app.scripts.report_prediction_research import report


def finish(path, wait=False):
    with finalizer_lock(path):
        return _finish(path, wait)


def _finish(path, wait):
    status_path = path / "finalization.json"
    stage = "WAITING"

    def status(value, **extra):
        nonlocal stage
        stage = value
        publish_status(status_path, dict(status=value, pid=os.getpid(), timestamp=datetime.now(timezone.utc), **extra))

    try:
        # run.json is published once. Do not touch changing views or streams until OS exit.
        run = read_json(path / "run.json")
        status("WAITING", collector_pid=run["pid"], wait_basis="OS_PROCESS_EXIT", stop_at=run["stop_at"])
        wait_for_collector(run, wait)
        status("VALIDATING")
        live = completed_dataset(path)
        status("REPLAY")
        replay_result = retry_io(lambda: replay(path))
        publish_status(path / "replay.json", replay_result)
        if not replay_result or not all(v["consistent"] for v in replay_result.values()):
            raise ValueError("Replay mismatch; dataset must not be used for research conclusions")
        status("ANALYSIS")
        analysis = retry_io(lambda: analyze(path))
        publish_status(path / "research-analysis.json", analysis)
        status("REPORT")
        temporary_report = path / "REPORT.md.tmp"
        retry_io(lambda: report(path, temporary_report))
        retry_io(lambda: temporary_report.replace(path / "REPORT.md"))
        result = dict(
            status="COMPLETE",
            pid=os.getpid(),
            completed=datetime.now(timezone.utc),
            collector_status=live["status"],
            duration_seconds=analysis["duration_seconds"],
            coverage=analysis["cross_venue_valid_coverage"],
            decision=analysis["decision"],
        )
        publish_status(status_path, result)
        return result
    except Exception as exc:
        failure = dict(
            status="FAILED",
            pid=os.getpid(),
            stage=stage,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            timestamp=datetime.now(timezone.utc),
        )
        try:
            publish_status(status_path, failure)
        except Exception:
            # stderr still preserves both errors if the status sidecar itself is locked.
            traceback.print_exc()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay, analyze and report a completed collector job")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    from app.prediction.storage import dumps

    print(dumps(finish(args.directory, args.wait)))
