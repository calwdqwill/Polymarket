import gzip
import json
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.prediction.live_storage import LiveJournal, atomic_json
from app.scripts.analyze_prediction_research import analyze
from app.scripts.recover_prediction import salvage
from app.scripts.repair_prediction_episode_timestamps import clock_map
from app.scripts.report_prediction_recovery import scenario
from app.scripts.verify_prediction_observations import verify

from tests.test_prediction import NOW, D, synthetic_market


class InterruptedRecoveryTests(unittest.TestCase):
    def test_episode_utc_uses_local_clock_anchor_including_exact_boundary(self):
        observations = [
            dict(monotonic_received_ns=100, received_timestamp=NOW.isoformat()),
            dict(monotonic_received_ns=200, received_timestamp=(NOW + timedelta(seconds=1)).isoformat()),
        ]
        result = clock_map({150, 200, 250}, iter(observations))
        self.assertEqual(result[250] - result[200], 50)
        self.assertEqual(result[200] - result[150], 999_999_950)

    def test_episode_before_first_observation_is_not_extrapolated_backwards(self):
        with self.assertRaises(ValueError):
            clock_map({50}, iter([dict(monotonic_received_ns=100, received_timestamp=NOW.isoformat())]))

    def test_fixed_quantity_pnl_does_not_use_another_size_or_repeat_observations(self):
        event = dict(max_edge=".02", theoretical_maximum_gross_pnl="999", duration_ms=1000)
        result = scenario([event], D(10), D(2))
        self.assertEqual(result["gross_per_hour"], D(".1"))
        self.assertEqual(result["trades_per_hour"], D(".5"))

    def test_raw_verifier_resubscription_refreshes_same_version_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            journal = LiveJournal(path)
            mono = journal.started_ns
            poly, limit = synthetic_market("Polymarket"), synthetic_market("Limitless")
            poly.slug, limit.slug = "poly-test", "limit-test"
            for market in (poly, limit):
                journal.append("markets", market, NOW, mono)
            poly_frame = [
                dict(
                    event_type="book",
                    asset_id=token,
                    timestamp="100",
                    bids=[dict(price=".4", size="100")],
                    asks=[dict(price=".6", size="100")],
                )
                for token in poly.tokens.values()
            ]
            journal.append("raw_ws_polymarket", dict(frame=json.dumps(poly_frame), market=poly.slug), NOW, mono, "p")
            limit_frame = "42/markets," + json.dumps(
                [
                    "orderbookUpdate",
                    dict(
                        marketSlug=limit.slug,
                        version=1,
                        orderbook=dict(
                            bids=[dict(price=".4", size=str(100 * limit.size_scale))],
                            asks=[dict(price=".6", size=str(100 * limit.size_scale))],
                        ),
                    ),
                ]
            )
            journal.append("raw_ws_limitless", dict(frame=limit_frame, market=limit.slug), NOW, mono, "l")
            journal.append("transport_sent", dict(frame="subscribe_market_prices"), NOW, mono + 1, "l")
            journal.append("raw_ws_limitless", dict(frame=limit_frame, market=limit.slug), NOW, mono + 2, "l")
            journal.append(
                "observations",
                dict(
                    window=int(poly.start.timestamp()),
                    quality="EXACT",
                    rows=[["B", "10", "NO_EDGE", "-0.2"]],
                    books={
                        "Polymarket:YES": dict(book_received_ns=mono),
                        "Limitless:NO": dict(book_received_ns=mono + 2),
                    },
                ),
                NOW,
                mono + 2,
            )
            journal.close()
            self.assertTrue(verify(path)["consistent"])

    def test_late_regression_deferral_excludes_market_without_hiding_priced_rows(self):
        from unittest.mock import patch

        poly, limit = synthetic_market("Polymarket"), synthetic_market("Limitless")
        poly.slug, limit.slug = "poly-test", "limit-test"
        mono = 1_000_000_000
        frame = "42/markets," + json.dumps(
            [
                "orderbookUpdate",
                dict(
                    marketSlug=limit.slug,
                    version=2,
                    orderbook=dict(bids=[dict(price=".4", size="100")], asks=[dict(price=".6", size="100")]),
                ),
            ]
        )
        sources = {
            "markets": [
                dict(local_ordinal=1, monotonic_received_ns=mono, connection=None, payload=poly.__dict__),
                dict(local_ordinal=2, monotonic_received_ns=mono, connection=None, payload=limit.__dict__),
            ],
            "raw_ws_limitless": [
                dict(
                    local_ordinal=3,
                    monotonic_received_ns=mono,
                    connection="l",
                    received_timestamp=NOW.isoformat(),
                    payload=dict(market=limit.slug, frame=frame),
                ),
                dict(
                    local_ordinal=4,
                    monotonic_received_ns=mono + 20_000_000_000,
                    connection="l",
                    received_timestamp=NOW.isoformat(),
                    payload=dict(market=limit.slug, frame=frame.replace('"version": 2', '"version": 1')),
                ),
            ],
            "connections": [
                dict(
                    local_ordinal=5,
                    monotonic_received_ns=mono + 23_000_000_000,
                    connection="l",
                    payload=dict(event="INVALID"),
                )
            ],
        }
        for item in sources["markets"]:
            item["payload"] = dict(
                item["payload"], start=NOW.isoformat(), end=(NOW + timedelta(seconds=300)).isoformat()
            )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "app.scripts.verify_prediction_observations.records",
                side_effect=lambda path, stream: iter(sources.get(stream, [])),
            ),
        ):
            result = verify(Path(directory))
        self.assertEqual(result["strategy_excluded_windows"], [int(NOW.timestamp())])
        self.assertEqual(result["protocol_unsafe_intervals"]["l"]["deferred_invalidation_ms"], 3000)

    def test_missing_gzip_trailer_preserves_every_complete_record(self):
        raw = b'{"n":1}\n{"n":2}\n'
        complete, info = salvage(gzip.compress(raw)[:-8])
        self.assertEqual(complete, raw)
        self.assertEqual(info["complete_records"], 2)
        self.assertFalse(info["gzip_eof"])
        self.assertEqual(info["discarded_incomplete_record_bytes"], 0)

    def test_incomplete_json_tail_is_the_only_removed_content(self):
        raw = b'{"n":1}\n{"n":'
        complete, info = salvage(gzip.compress(raw)[:-8])
        self.assertEqual(complete, b'{"n":1}\n')
        self.assertEqual(info["truncation_offset_uncompressed"], 8)
        self.assertEqual(info["discarded_incomplete_record_bytes"], 5)

    def test_interior_json_or_crc_corruption_is_not_silently_repaired(self):
        with self.assertRaises(ValueError):
            salvage(gzip.compress(b'{"n":1}\nbroken\n'))
        data = bytearray(gzip.compress(b'{"n":1}\n'))
        data[-8] ^= 1
        with self.assertRaises(zlib.error):
            salvage(bytes(data))

    def test_partial_window_is_excluded_from_strategy_but_retained_in_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            journal = LiveJournal(path)
            start = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
            mono = journal.started_ns
            for offset, edge in ((0, "-0.02"), (300, "0.50")):
                now = start + timedelta(seconds=offset)
                current = mono + offset * 1_000_000_000
                health = {
                    v + ":" + o: dict(
                        status="VALID",
                        valid_until_ns=current + 300_000_000_000,
                        healthy_until_ns=current + 300_000_000_000,
                        book_received_ns=current,
                    )
                    for v in ("Polymarket", "Limitless")
                    for o in ("YES", "NO")
                }
                journal.append(
                    "observations",
                    dict(
                        window=int(now.timestamp()),
                        quality="UNKNOWN",
                        books=health,
                        rows=[["A", "10", "OPPORTUNITY" if offset else "NO_EDGE", edge]],
                        event_weighted=True,
                    ),
                    now,
                    current,
                )
            journal.close()
            atomic_json(path / "run.json", dict(started=start))
            atomic_json(path / "live.json", dict(status="FROZEN_PREFIX", timestamp=start + timedelta(seconds=301)))
            atomic_json(path / "metrics.json", dict(duration_seconds=301, streams={}))
            result = analyze(path, full_windows_only=True)
            self.assertEqual(result["duration_seconds"], 301)
            self.assertEqual(result["strategy_duration_seconds"], 300)
            self.assertEqual(result["edge"]["A:10"]["time_weighted_positive_only"]["weight"], 0)
            self.assertFalse(result["episode_counts"])
            self.assertEqual(len(result["windows"]), 2)
            excluded = analyze(path, full_windows_only=True, excluded_windows=[int(start.timestamp())])
            self.assertEqual(excluded["strategy_duration_seconds"], 0)
            self.assertFalse(excluded["edge"])
            self.assertTrue(excluded["windows"][int(start.timestamp())]["replay_excluded"])
