import tempfile
import unittest
from pathlib import Path

from app.prediction.books import PolymarketBooks
from app.prediction.live_storage import LiveJournal, atomic_json
from app.prediction.research_statistics import Episodes, ExactStatistics
from app.scripts.analyze_prediction import records
from app.scripts.analyze_prediction_research import analyze

from tests.test_prediction import NOW, NS, D, synthetic_market


class ResearchTests(unittest.TestCase):
    def test_finalization_stops_before_analysis_on_corrupt_replay(self):
        from unittest.mock import patch

        from app.scripts.finish_prediction_research import finish

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            atomic_json(path / "run.json", dict(stop_at=0, pid=999999, started=NOW))
            atomic_json(path / "live.json", dict(status="STOPPED"))
            atomic_json(path / "segments.json", dict(active=None, closed=[0]))
            atomic_json(path / "metrics.json", {})
            with (
                patch("app.scripts.finish_prediction_research.wait_for_collector"),
                patch(
                    "app.scripts.finish_prediction_research.replay", return_value={"polymarket": {"consistent": False}}
                ),
                patch("app.scripts.finish_prediction_research.analyze") as analyze_mock,
            ):
                with self.assertRaisesRegex(ValueError, "Replay mismatch"):
                    finish(path)
                analyze_mock.assert_not_called()

    def test_transport_health_is_independent_of_snapshot_arrival(self):
        from datetime import timedelta

        from app.scripts.analyze_prediction_research import connection_coverage, epoch_ns

        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run")
            mono = journal.started_ns
            now = NOW.replace(second=0, microsecond=0)
            window = int(now.timestamp()) // 300 * 300
            journal.append(
                "connections",
                dict(event="CONNECTED", venue="Polymarket", market=f"btc-updown-5m-{window}"),
                now,
                mono,
                "c",
            )
            journal.append(
                "connections",
                dict(event="INVALID", venue="Polymarket", market=f"btc-updown-5m-{window}"),
                now + timedelta(seconds=1),
                mono + 1_000_000_000,
                "c",
            )
            journal.close()
            result = connection_coverage(journal.directory, epoch_ns(now), epoch_ns(now + timedelta(seconds=2)))
            self.assertEqual(result[window]["Polymarket_connection_healthy_ns"], 1_000_000_000)

    def test_time_weighting_does_not_follow_update_frequency(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = ExactStatistics(Path(directory) / "cache.sqlite")
            for _ in range(1000):
                stats.add("edge", "A:10", D(".01"), events=1, ns=1)
            stats.add("edge", "A:10", D("-.02"), events=1, ns=9000)
            result = stats.report("edge")["A:10"]
            self.assertEqual(result["event_weighted"]["median"], D(".01"))
            self.assertEqual(result["time_weighted"]["median"], D("-.02"))
            self.assertEqual(result["time_weighted_positive_only"]["weight"], 1000)
            stats.close()

    def test_episode_union_thresholds_integral_and_no_repeated_pnl(self):
        events = []
        episodes = Episodes(events.append)
        negative = [["A", "10", "NO_EDGE", D("-.01")], ["A", "50", "NO_EDGE", D("-.02")]]
        episodes.update(1, 0, 10, negative)
        first = [["A", "10", "OPPORTUNITY", D(".02")], ["A", "50", "OPPORTUNITY", D(".01")]]
        second = [["A", "10", "OPPORTUNITY", D(".01")], ["A", "50", "OPPORTUNITY", D(".005")]]
        episodes.update(1, 10, 30, first)
        episodes.update(1, 30, 40, second)
        episodes.update(1, 40, 50, negative)
        union = [e for e in events if e["quantity"] == "ANY_Q" and e["threshold"] == 0]
        self.assertEqual(len(union), 1)
        event = union[0]
        self.assertEqual(event["end_ns"] - event["start_ns"], 30)
        self.assertEqual(event["mean_time_weighted_edge"], D(".5") / 30)
        self.assertEqual(event["theoretical_maximum_gross_pnl"], D(".50"))
        self.assertEqual(event["capacity_by_threshold"]["0.01"], 10)
        self.assertFalse(event["right_censored"] or event["left_censored"])

    def test_invalid_interval_splits_and_censors_episodes(self):
        events = []
        episodes = Episodes(events.append)
        positive = [["A", "10", "OPPORTUNITY", D(".02")]]
        episodes.update(1, 0, 10, positive)
        episodes.update(1, 10, 30, [], "STALE")
        episodes.update(1, 30, 40, positive)
        episodes.close_all(40, "STOP")
        matches = [e for e in events if e["quantity"] == "10" and e["threshold"] == 0]
        self.assertEqual(len(matches), 2)
        self.assertEqual(sum(e["duration_ms"] for e in matches), D(20) / 1_000_000)
        self.assertTrue(all(e["left_censored"] and e["right_censored"] for e in matches))

    def test_snapshot_restores_desync_without_accepting_intervening_deltas(self):
        adapter = PolymarketBooks(synthetic_market("Polymarket"))
        snapshot = dict(
            event_type="book",
            asset_id="yes",
            timestamp="100",
            bids=[dict(price=".4", size="10")],
            asks=[dict(price=".6", size="20")],
        )
        adapter.apply(snapshot, NOW, NS)
        delta = dict(
            event_type="price_change",
            timestamp="101",
            price_changes=[dict(asset_id="yes", side="BUY", price=".3", size="10", best_bid=".3", best_ask=".6")],
        )
        adapter.apply(delta, NOW, NS + 1)
        self.assertEqual(adapter.books["yes"].status, "DESYNC")
        delta["price_changes"][0]["size"] = "200"
        adapter.apply(delta, NOW, NS + 2)
        self.assertEqual(adapter.books["yes"].bids[D(".3")], 10)
        snapshot["timestamp"] = "102"
        snapshot["bids"] = [dict(price=".3", size="50")]
        adapter.apply(snapshot, NOW, NS + 3)
        self.assertEqual(adapter.books["yes"].status, "VALID")
        self.assertEqual(adapter.books["yes"].bids, {D(".3"): D(50)})

    def test_segments_are_closed_and_replayable_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run", segment_seconds=60)
            first = journal.started_ns
            journal.append("raw_ws_polymarket", dict(frame="PONG"), NOW, first)
            journal.append("raw_ws_polymarket", dict(frame="PONG"), NOW, first + 61_000_000_000)
            journal.close()
            self.assertEqual([r["local_ordinal"] for r in records(journal.directory, "raw_ws_polymarket")], [1, 2])
            self.assertEqual(len(list(journal.directory.glob("raw_ws_polymarket.*.gz"))), 2)

    def test_coverage_clips_at_deadline_without_forward_fill(self):
        from datetime import timedelta

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            journal = LiveJournal(path)
            mono = journal.started_ns
            now = NOW.replace(second=0, microsecond=0)
            window = int(now.timestamp()) // 300 * 300
            books = {
                v + ":" + o: dict(
                    status="VALID",
                    valid_until_ns=mono + 300_000_000,
                    healthy_until_ns=mono + 800_000_000,
                    book_received_ns=mono,
                )
                for v in ("Polymarket", "Limitless")
                for o in ("YES", "NO")
            }
            journal.append(
                "observations",
                dict(
                    window=window,
                    quality="UNKNOWN",
                    books=books,
                    rows=[["A", "10", "OPPORTUNITY", D(".02")]],
                    event_weighted=True,
                ),
                now,
                mono,
            )
            journal.close()
            atomic_json(path / "run.json", dict(started=now))
            atomic_json(path / "live.json", dict(timestamp=now + timedelta(seconds=1), status="STOPPED"))
            atomic_json(path / "metrics.json", dict(duration_seconds=1, streams={}))
            result = analyze(path)
            self.assertEqual(result["cross_venue_valid_coverage"], D(".3"))
            self.assertEqual(result["totals_ns"]["Polymarket_connection_healthy_ns"], 800_000_000)
            self.assertEqual(result["totals_ns"]["Polymarket_STALE_ns"], 700_000_000)
            self.assertEqual(result["edge"]["A:10"]["time_weighted"]["weight"], 300_000_000)


class RecoveryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_mismatch_waits_for_snapshot_without_reconnecting(self):
        import asyncio
        import json
        from unittest.mock import patch

        from app.prediction.live_transport import stream_market
        from websockets.asyncio.server import serve

        market = synthetic_market("Polymarket")
        recovered = asyncio.Event()
        registered, states, subscriptions = [], [], []
        snapshots = [
            dict(
                event_type="book",
                asset_id=token,
                timestamp="100",
                bids=[dict(price=".4", size="20")],
                asks=[dict(price=".6", size="30")],
            )
            for token in ("yes", "no")
        ]

        async def server(ws):
            subscriptions.append(await ws.recv())
            await ws.send(json.dumps(snapshots))
            await ws.send(
                json.dumps(
                    dict(
                        event_type="price_change",
                        timestamp="101",
                        price_changes=[
                            dict(asset_id="yes", side="BUY", price=".3", size="20", best_bid=".3", best_ask=".6")
                        ],
                    )
                )
            )
            await asyncio.sleep(0.02)
            snapshots[0].update(timestamp="102", bids=[dict(price=".3", size="20")])
            await ws.send(json.dumps(snapshots[0]))
            await recovered.wait()

        def changed(now, mono, m, observe=True):
            status = registered[-1][0].status
            if "DESYNC" in states and status == "VALID":
                recovered.set()
            states.append(status)

        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run")
            journal.append("markets", market)
            atomic_json(journal.directory / "run.json", dict(schema_version=2))
            async with serve(server, "127.0.0.1", 0) as service:
                url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
                with patch.dict("app.prediction.live_transport.URLS", Polymarket=url):
                    task = asyncio.create_task(stream_market(market, journal, changed, registered.append))
                    try:
                        await asyncio.wait_for(recovered.wait(), 3)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        journal.close()
            self.assertEqual(len(subscriptions), 1)
            self.assertEqual(journal.counts["desync_diagnostics"], 1)
            self.assertEqual(journal.counts["recoveries"], 1)
            from app.scripts.replay_prediction import replay_checkpoints

            self.assertTrue(replay_checkpoints(journal.directory)["polymarket"]["consistent"])
            import gzip

            file = next(journal.directory.glob("checkpoints_polymarket.*.gz"))
            with gzip.open(file, "rt", encoding="utf8") as handle:
                checkpoints = [json.loads(line) for line in handle]
            checkpoints[0]["payload"]["book"]["asks"][0]["size"] = "999"
            with gzip.open(file, "wt", encoding="utf8") as handle:
                for record in checkpoints:
                    handle.write(json.dumps(record) + "\n")
            self.assertFalse(replay_checkpoints(journal.directory)["polymarket"]["consistent"])


if __name__ == "__main__":
    unittest.main()
