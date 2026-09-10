import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.prediction.live_storage import LiveJournal
from app.prediction.live_transport import invalidating_connection
from app.prediction.service_runtime import disk_gate


class SafetyTests(unittest.TestCase):
    def test_disk_boundaries(self):
        for free, used in ((24_999_999_999, 0), (40_000_000_000, 20_000_000_000)):
            with self.assertRaises(RuntimeError):
                disk_gate(100_000_000_000, free, used)
        self.assertTrue(disk_gate(100_000_000_000, 29_000_000_000, 0))
        with self.assertRaises(RuntimeError):
            disk_gate(100_000_000_000, 49_000_000_000, 0, preflight=True)

    def test_metrics_do_not_scan_closed_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = LiveJournal(Path(tmp) / "run", segment_seconds=1)
            for i in range(605):
                journal.append("sample", {}, mono_ns=journal.started_ns + i * 1_000_000_000)
            journal.close()
            with patch.object(Path, "glob", side_effect=AssertionError("Historical scan")):
                metrics = journal.metrics()
            self.assertLessEqual(len(journal.bins["sample"]), 300)
            self.assertEqual(metrics["streams"]["sample"]["messages"], 605)
            self.assertGreater(metrics["streams"]["sample"]["stored_bytes"], 0)


class SlowCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_before_slow_close_for_error_and_cancel(self):
        for error in (ValueError("parser"), OSError("transport"), asyncio.CancelledError()):
            invalid = asyncio.Event()
            closing = asyncio.Event()
            release = asyncio.Event()

            class Connection:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    closing.set()
                    await release.wait()

            async def worker():
                async with invalidating_connection(Connection(), invalid.set):
                    raise error

            task = asyncio.create_task(worker())
            await asyncio.wait_for(closing.wait(), 1)
            self.assertTrue(invalid.is_set())
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(type(error)):
                await task
