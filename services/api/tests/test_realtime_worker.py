import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from app.workers.realtime_worker import (
    RetryConfig,
    detect_candle_gap,
    fetch_latest_report_with_retry,
)


class FakeStreamsClient:
    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def fetch_latest_report(self, *, feed_id: str, price_decimals: int):  # noqa: ANN201
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("temporary network failure")
        return {"feed_id": feed_id, "price_decimals": price_decimals}


class SilentLogger:
    def warning(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


class RealtimeWorkerTest(unittest.TestCase):
    def test_fetch_latest_report_retries_with_backoff(self) -> None:
        client = FakeStreamsClient(failures_before_success=2)
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        report, attempts = asyncio.run(
            fetch_latest_report_with_retry(
                client=client,  # type: ignore[arg-type]
                feed_id="0xabc",
                price_decimals=18,
                retry=RetryConfig(attempts=4, initial_delay_seconds=0.5, max_delay_seconds=2.0),
                logger=SilentLogger(),  # type: ignore[arg-type]
                sleep=sleep,
            )
        )

        self.assertEqual(report["feed_id"], "0xabc")
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [0.5, 1.0])

    def test_fetch_latest_report_raises_after_retry_budget(self) -> None:
        client = FakeStreamsClient(failures_before_success=10)
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        with self.assertRaises(RuntimeError):
            asyncio.run(
                fetch_latest_report_with_retry(
                    client=client,  # type: ignore[arg-type]
                    feed_id="0xabc",
                    price_decimals=18,
                    retry=RetryConfig(attempts=3, initial_delay_seconds=1.0, max_delay_seconds=1.5),
                    logger=SilentLogger(),  # type: ignore[arg-type]
                    sleep=sleep,
                )
            )

        self.assertEqual(client.calls, 3)
        self.assertEqual(delays, [1.0, 1.5])

    def test_detect_candle_gap_reports_missing_windows(self) -> None:
        previous = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
        current = previous + timedelta(minutes=20)

        gap = detect_candle_gap(previous, current)

        self.assertIsNotNone(gap)
        self.assertEqual(gap.missing_candle_count, 3)
        self.assertEqual(gap.previous_candle_start, previous.isoformat())
        self.assertEqual(gap.current_candle_start, current.isoformat())

    def test_detect_candle_gap_ignores_adjacent_windows(self) -> None:
        previous = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
        current = previous + timedelta(minutes=5)

        self.assertIsNone(detect_candle_gap(previous, current))


if __name__ == "__main__":
    unittest.main()
