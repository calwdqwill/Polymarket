import asyncio
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import atomic_json
from app.prediction.storage import dumps
from app.scripts.collect_prediction import collect
from app.scripts.finish_prediction_research import finish
from app.scripts.prediction_finalization_io import (
    CollectorProcess,
    completed_dataset,
    finalizer_lock,
    publish_status,
    retry_io,
    wait_for_collector,
)

from tests.test_prediction import synthetic_market


def source_hashes(path):
    files = list(path.glob("*.*.jsonl.gz")) + [
        path / n for n in ("run.json", "live.json", "segments.json", "metrics.json")
    ]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


async def synthetic_stream(market, journal, changed, register):
    now, mono = datetime.now(timezone.utc), time.perf_counter_ns()
    venue = market.venue.lower()
    journal.append("markets", market, now, mono)
    if venue == "polymarket":
        adapter = PolymarketBooks(market)
        messages = [
            dict(
                event_type="book",
                asset_id=token,
                timestamp="100",
                bids=[dict(price=".4", size="20")],
                asks=[dict(price=".6", size="30")],
            )
            for token in ("yes", "no")
        ]
        frame = dumps(messages)
    else:
        adapter = LimitlessBooks(market)
        messages = [
            dict(
                marketSlug=market.slug,
                version=1,
                orderbook=dict(bids=[dict(price=".4", size="20")], asks=[dict(price=".6", size="30")]),
            )
        ]
        frame = "42/markets," + dumps(["orderbookUpdate", messages[0]])
    ordinal = journal.append("raw_ws_" + venue, dict(frame=frame, market=market.slug), now, mono, venue)
    for message in messages:
        adapter.apply(message, now, mono)
    books = list(adapter.books.values()) if venue == "polymarket" else [adapter.yes, adapter.no]
    register(books)
    for book in books:
        if venue == "limitless" and book.outcome == "NO":
            continue
        journal.append("checkpoints_" + venue, dict(book=book, raw_ordinal=ordinal), now, mono, venue)
    changed(now, mono, market)
    await asyncio.Event().wait()


async def smoke_collect(path, seconds=2):
    async def discovery(self, now):
        start = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, timezone.utc)
        return [
            replace(
                synthetic_market(v),
                start=start,
                end=start + timedelta(seconds=300),
                slug=v.lower() + "-" + str(int(start.timestamp())),
            )
            for v in ("Polymarket", "Limitless")
        ]

    with (
        patch("app.scripts.collect_prediction.Discovery.collect", discovery),
        patch("app.scripts.collect_prediction.stream_market", synthetic_stream),
    ):
        await collect(path, seconds=seconds)


class FinalizationTests(unittest.TestCase):
    def test_real_collector_closes_gzip_and_pipeline_completes_without_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic"
            asyncio.run(smoke_collect(path))
            self.assertIsNone(json.loads((path / "segments.json").read_text())["active"])
            for file in path.glob("*.*.jsonl.gz"):
                with gzip.open(file, "rb") as handle:
                    handle.read()  # Requires the final gzip trailer, not just a flush.
            before = source_hashes(path)
            with patch("app.scripts.finish_prediction_research.wait_for_collector"):
                result = finish(path)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertTrue((path / "REPORT.md").stat().st_size > 100)
            self.assertEqual(source_hashes(path), before)
            replay = json.loads((path / "replay.json").read_text())
            self.assertTrue(all(v["checkpoints_checked"] > 0 and v["consistent"] for v in replay.values()))

    def test_live_process_refuses_analysis_without_reading_views(self):
        run = dict(pid=os.getpid(), started=datetime.now(timezone.utc), stop_at=time.time() + 10)
        run["started"] = run["started"].isoformat()
        # Current test process may be old; mock identity, exercise the pre-read barrier.
        with (
            patch.object(CollectorProcess, "__init__", return_value=None),
            patch.object(CollectorProcess, "is_running", return_value=True),
            patch.object(CollectorProcess, "close"),
        ):
            with self.assertRaisesRegex(ValueError, "still running"):
                wait_for_collector(run, False)
            run["stop_at"] = time.time() - 601
            with self.assertRaises(TimeoutError):
                wait_for_collector(run, True)

    def test_incomplete_manifest_and_stale_terminal_view_block_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            atomic_json(path / "live.json", dict(status="LIVE"))
            atomic_json(path / "segments.json", dict(active=None, closed=[0]))
            with self.assertRaisesRegex(ValueError, "terminal"):
                completed_dataset(path)
            atomic_json(path / "live.json", dict(status="STOPPED"))
            atomic_json(path / "segments.json", dict(active=0, closed=[]))
            with self.assertRaisesRegex(ValueError, "finalized"):
                completed_dataset(path)
            atomic_json(path / "segments.json", dict(active=None, closed=[0]))
            (path / "raw_ws_polymarket.000001.jsonl.gz").write_bytes(b"unfinished")
            with self.assertRaisesRegex(ValueError, "Unclosed"):
                completed_dataset(path)

    def test_single_finalizer_lock_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with finalizer_lock(path):
                with self.assertRaisesRegex(RuntimeError, "Another finalizer"):
                    with finalizer_lock(path):
                        self.fail("Duplicate acquired lock")
            with finalizer_lock(path):
                pass

    def test_failed_analysis_keeps_sources_and_can_be_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic"
            asyncio.run(smoke_collect(path))
            before = source_hashes(path)
            with patch("app.scripts.finish_prediction_research.wait_for_collector"):
                with patch("app.scripts.finish_prediction_research.analyze", side_effect=RuntimeError("smoke failure")):
                    with self.assertRaisesRegex(RuntimeError, "smoke failure"):
                        finish(path)
                failure = json.loads((path / "finalization.json").read_text())
                self.assertEqual(failure["stage"], "ANALYSIS")
                self.assertIn("Traceback", failure["traceback"])
                self.assertEqual(before, source_hashes(path))
                self.assertEqual(finish(path)["status"], "COMPLETE")
                self.assertEqual(before, source_hashes(path))

    def test_transient_and_persistent_permission_errors(self):
        with patch("app.scripts.prediction_finalization_io.atomic_json", side_effect=[False, True]) as publish:
            with patch("app.scripts.prediction_finalization_io.time.sleep"):
                publish_status(Path("unused"), dict(status="WAITING"))
            self.assertEqual(publish.call_count, 2)
        with self.assertRaises(PermissionError):
            retry_io(lambda: (_ for _ in ()).throw(PermissionError("locked")), timeout=0)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_windows_exclusive_live_file_waits_for_process_exit_then_finalizes(self):
        # A real second process denies every sharing mode on live.json. Its misleading
        # STOPPED + active=None views must not permit finalization before OS exit.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic"
            asyncio.run(smoke_collect(path))
            code = """
import ctypes as c, sys
from ctypes import wintypes as w
k=c.WinDLL('kernel32', use_last_error=True)
k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
k.CreateFileW.restype=w.HANDLE
k.CloseHandle.argtypes=[w.HANDLE]
h=k.CreateFileW(sys.argv[1],0x80000000,0,None,3,0,None)
if h == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
print('LOCKED', flush=True)
sys.stdin.readline()
k.CloseHandle(h)
"""
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", code, str(path / "live.json")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "LOCKED")
                run = json.loads((path / "run.json").read_text())
                run.update(pid=child.pid, started=datetime.now(timezone.utc).isoformat(), stop_at=time.time() + 30)
                atomic_json(path / "run.json", run)
                with self.assertRaises(PermissionError):
                    (path / "live.json").read_text()
                from app.scripts import finish_prediction_research as module

                original_read = module.read_json
                reads = []

                def audited_read(file):
                    reads.append(file.name)
                    return original_read(file)

                with ThreadPoolExecutor() as pool, patch.object(module, "read_json", audited_read):
                    task = pool.submit(finish, path, True)
                    try:
                        deadline = time.monotonic() + 5
                        while not (path / "finalization.json").exists() and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertFalse(task.done())
                        self.assertEqual(reads, ["run.json"])
                        self.assertEqual(json.loads((path / "finalization.json").read_text())["status"], "WAITING")
                    finally:
                        child.stdin.write("release\n")
                        child.stdin.flush()
                        child.wait(timeout=5)
                    self.assertEqual(task.result(timeout=15)["status"], "COMPLETE")
            finally:
                if child.poll() is None:
                    child.communicate("release\n", timeout=5)
                for handle in (child.stdin, child.stdout, child.stderr):
                    handle.close()

    @unittest.skipUnless(os.name == "nt", "Windows process identity")
    def test_windows_pid_reuse_does_not_wait_for_unrelated_process(self):
        with CollectorProcess(dict(pid=os.getpid(), started="2000-01-01T00:00:00+00:00")) as process:
            self.assertFalse(process.is_running())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-collector":
        asyncio.run(smoke_collect(Path(sys.argv[2]), seconds=3))
    else:
        unittest.main()
