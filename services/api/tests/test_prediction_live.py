import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import LiveJournal
from app.prediction.models import Match
from app.prediction.observer import Observer

from tests import test_prediction as fixtures
from tests.test_prediction import NOW, NS, D, synthetic_market


class LivePredictionTest(unittest.TestCase):
    def fixture(self):
        fixture = fixtures.PredictionTest()
        fixture.setUp()
        return fixture

    def test_research_quality_and_non_equivalent_gate(self):
        fixture = self.fixture()
        for quality in ("UNKNOWN", "PROVISIONAL", "EXACT", "NOT_EQUIVALENT"):
            with (
                self.subTest(quality=quality),
                patch("app.prediction.observer.match_markets", return_value=Match(quality)),
            ):
                rows, events = fixture.evaluate()
                self.assertEqual(bool(events), quality != "NOT_EQUIVALENT")
                if quality != "NOT_EQUIVALENT":
                    self.assertTrue(all(r["observed_edge"] == D(".10") for r in rows))
                    self.assertTrue(all(r["classification"] == "observed cross-venue discrepancy" for r in rows))
                else:
                    self.assertTrue(all(r["status"] == "BLOCKED_MATCH" and r["observed_edge"] is None for r in rows))

    def test_timer_never_opens_or_extends_positive_episode(self):
        fixture = self.fixture()
        observer = Observer()
        _, events = observer.evaluate(fixture.a, fixture.b, fixture.books, NOW, NS, observe=False)
        self.assertEqual(events, [])
        fixture.evaluate(observer)
        _, events = observer.evaluate(fixture.a, fixture.b, fixture.books, NOW, NS + 1_000_000_000, observe=False)
        self.assertEqual(events, [])
        closes = observer.close_all(NOW)
        self.assertTrue(all(e["duration_ms"] == 0 for e in closes))

    def test_capacity_not_double_counted_and_peak_edge(self):
        fixture = self.fixture()
        observer = Observer()
        fixture.evaluate(observer)
        fixture.books[fixture.a.venue, fixture.a.market_id, "YES"].asks = {D(".48"): D(200)}
        rows, events = fixture.evaluate(observer, NS + 25_000_000)
        row = rows[0]
        self.assertEqual(row["max_executable_size"], D(100))
        self.assertEqual(row["limiting_venue"], "Polymarket")
        update = next(e for e in events if e["venue_yes"] == "Polymarket" and e["share_size"] == 10)
        self.assertEqual(update["max_edge"], D(".10"))
        self.assertEqual(update["current_edge"], D(".07"))
        self.assertEqual(update["pnl_by_size"]["100"], D(7))
        self.assertIsNone(update["pnl_by_size"]["250"])

    def test_limitless_raw_units_and_independent_session_snapshot(self):
        market = synthetic_market("Limitless")
        market.size_scale = D(1000000)
        adapter = LimitlessBooks(market)
        frame = dict(
            marketSlug="test",
            version=20,
            orderbook=dict(
                tokenId="yes", bids=[dict(price=D(".4"), size=50000000)], asks=[dict(price=D(".6"), size=1000000000)]
            ),
        )
        before = copy.deepcopy(frame)
        adapter.apply(frame, NOW, NS)
        self.assertEqual(frame, before)
        self.assertEqual(adapter.yes.bids, {D(".4"): D(50)})
        self.assertEqual(adapter.no.asks, {D(".6"): D(50)})
        self.assertEqual(adapter.no.bids, {D(".4"): D(1000)})
        adapter.disconnect()
        self.assertEqual(adapter.yes.status, "DESYNC")
        replacement = LimitlessBooks(market)
        frame["version"] = 0
        replacement.apply(frame, NOW, NS + 1)
        self.assertEqual(replacement.yes.status, "VALID")
        frame["version"] = 1
        frame["orderbook"]["tokenId"] = "wrong"
        with self.assertRaises(ValueError):
            replacement.apply(frame, NOW, NS + 2)
        self.assertEqual(replacement.yes.status, "DESYNC")

    def test_poly_advertised_top_disagreement_and_reconnect(self):
        market = synthetic_market("Polymarket")
        adapter = PolymarketBooks(market)
        snapshot = dict(
            event_type="book",
            asset_id="yes",
            timestamp="100",
            bids=[dict(price=".4", size="50")],
            asks=[dict(price=".6", size="50")],
        )
        adapter.apply(snapshot, NOW, NS)
        delta = dict(
            event_type="price_change",
            timestamp="101",
            price_changes=[dict(asset_id="yes", side="BUY", price=".3", size="20", best_bid=".3", best_ask=".6")],
        )
        updates = adapter.apply(delta, NOW, NS + 1)
        self.assertEqual(updates[0].status, "DESYNC")
        replacement = PolymarketBooks(market)
        self.assertEqual(replacement.apply(delta, NOW, NS + 2), [])
        replacement.apply(snapshot, NOW, NS + 3)
        self.assertEqual(replacement.books["yes"].status, "VALID")

    def test_raw_journal_arrival_identity_and_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run")
            mono = journal.started_ns
            journal.append("raw_ws_limitless", dict(frame="2"), NOW, mono, "connection-1")
            journal.append("raw_ws_limitless", dict(frame="2"), NOW, mono + 1, "connection-2")
            journal.flush()
            metrics = journal.metrics()
            journal.close()
            with gzip.open(journal.directory / "raw_ws_limitless.000000.jsonl.gz", "rt", encoding="utf8") as f:
                rows = [json.loads(line) for line in f]
            self.assertEqual([r["local_ordinal"] for r in rows], [1, 2])
            self.assertEqual(rows[0]["session"], rows[1]["session"])
            self.assertEqual(rows[0]["monotonic_received_ns"], mono)
            self.assertEqual(metrics["streams"]["raw_ws_limitless"]["payload_bytes"], 2)
            self.assertGreater(metrics["streams"]["raw_ws_limitless"]["mb_per_hour"], 0)


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_polymarket_disconnect_resubscribe_fresh_snapshot(self):
        import asyncio

        from app.prediction.live_transport import stream_market
        from websockets.asyncio.server import serve

        market = synthetic_market("Polymarket")
        snapshots = [
            dict(event_type="book", asset_id=token, timestamp="100", bids=[], asks=[dict(price=".5", size="100")])
            for token in ["yes", "no"]
        ]
        subscriptions = []
        registered = []
        completed = asyncio.Event()
        observations = []

        async def server(ws):
            subscriptions.append(json.loads(await ws.recv()))
            if len(subscriptions) == 1:
                await ws.send(json.dumps(snapshots))
                await ws.close()
            else:
                await ws.send(
                    json.dumps(
                        dict(
                            event_type="price_change",
                            timestamp="101",
                            price_changes=[dict(asset_id="yes", side="SELL", price=".1", size="50")],
                        )
                    )
                )
                await ws.send(json.dumps(snapshots[0]))
                await ws.send(json.dumps(snapshots[1]))
                await completed.wait()

        def register(books):
            registered.append(books)

        def changed(now, mono, m, observe=True):
            statuses = [b.status for b in registered[-1]]
            observations.append((observe, statuses))
            if len(registered) >= 2 and statuses == ["VALID", "VALID"]:
                completed.set()

        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run")
            async with serve(server, "127.0.0.1", 0) as service:
                url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
                with patch.dict("app.prediction.live_transport.URLS", Polymarket=url):
                    task = asyncio.create_task(stream_market(market, journal, changed, register))
                    try:
                        await asyncio.wait_for(completed.wait(), timeout=7)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        journal.close()
        self.assertGreaterEqual(len(subscriptions), 2)
        self.assertTrue(all(s == dict(assets_ids=["yes", "no"], type="market") for s in subscriptions))
        self.assertTrue(any(not observe and status == ["DESYNC", "DESYNC"] for observe, status in observations))
        self.assertEqual(registered[1][0].asks, {D(".5"): D(100)})

    async def test_limitless_public_engineio_snapshot_and_ping(self):
        import asyncio

        from app.prediction.live_transport import stream_market
        from websockets.asyncio.server import serve

        market = synthetic_market("Limitless")
        market.size_scale = D(1000000)
        completed = asyncio.Event()
        registered = []
        requests = []

        async def server(ws):
            await ws.send("0" + json.dumps(dict(sid="test", pingInterval=25000, pingTimeout=60000)))
            requests.append(await ws.recv())
            await ws.send("40/markets," + json.dumps(dict(sid="namespace")))
            requests.append(await ws.recv())
            await ws.send("2")
            requests.append(await ws.recv())
            frame = dict(
                marketSlug="test",
                version=1,
                orderbook=dict(
                    tokenId="yes", bids=[dict(price=0.4, size=50000000)], asks=[dict(price=0.6, size=100000000)]
                ),
            )
            await ws.send("42/markets," + json.dumps(["orderbookUpdate", frame]))
            await completed.wait()

        def changed(now, mono, m, observe=True):
            if observe:
                completed.set()

        with tempfile.TemporaryDirectory() as directory:
            journal = LiveJournal(Path(directory) / "run")
            async with serve(server, "127.0.0.1", 0) as service:
                url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
                with patch.dict("app.prediction.live_transport.URLS", Limitless=url):
                    task = asyncio.create_task(stream_market(market, journal, changed, lambda b: registered.extend(b)))
                    try:
                        await asyncio.wait_for(completed.wait(), timeout=3)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        journal.close()
        self.assertEqual(requests[0], "40/markets,")
        self.assertEqual(
            json.loads(requests[1].split(",", 1)[1]), ["subscribe_market_prices", {"marketSlugs": ["test"]}]
        )
        self.assertEqual(requests[2], "3")
        self.assertEqual(registered[1].asks, {D(".6"): D(50)})
        self.assertEqual(journal.counts["checkpoints_limitless"], 2)


class StatisticsTest(unittest.TestCase):
    def test_distribution_interpolates_without_rounding_financial_values(self):
        from app.scripts.analyze_prediction import distribution

        result = distribution([D(".1"), D(".2"), D(".3")])
        self.assertEqual(result["median"], D(".2"))
        self.assertEqual(result["p90"], D(".28"))
        self.assertEqual(result["p95"], D(".29"))
        self.assertEqual(result["count"], 3)
        self.assertIsNone(distribution([])["median"])


class ViewLockTest(unittest.TestCase):
    def test_windows_reader_lock_does_not_interrupt_raw_journal(self):
        from app.prediction.live_storage import atomic_json

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            journal = LiveJournal(path / "run")
            target = path / "live.json"
            target.write_text("previous", encoding="utf8")
            with patch.object(Path, "replace", side_effect=PermissionError("Windows reader lock")):
                self.assertFalse(atomic_json(target, dict(status="LIVE")))
                journal.append("raw_ws_polymarket", dict(frame="PONG"))
                journal.flush()
            self.assertEqual(target.read_text(), "previous")
            self.assertTrue(atomic_json(target, dict(status="LIVE")))
            self.assertEqual(json.loads(target.read_text())["status"], "LIVE")
            self.assertEqual(journal.counts["raw_ws_polymarket"], 1)
            journal.close()


class AnalysisIntegrationTest(unittest.TestCase):
    def test_full_window_buckets_and_staleness_bounded_time(self):
        from datetime import timedelta

        from app.prediction.live_storage import atomic_json
        from app.scripts.analyze_prediction import analyze

        fixture = fixtures.PredictionTest()
        fixture.setUp()
        observer = Observer()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            journal = LiveJournal(path)
            journal.started_ns = NS
            atomic_json(path / "run.json", dict(started=(NOW - timedelta(seconds=1)).isoformat(), max_age_ms=2000))
            atomic_json(
                path / "live.json", dict(timestamp=(NOW + timedelta(seconds=300)).isoformat(), status="STOPPED")
            )
            for offset in (0, 100_000_000):
                if offset:
                    for book in fixture.books.values():
                        book.asks = {D(".6"): D(1000)}
                rows, events = fixture.evaluate(observer, NS + offset)
                journal.append("observations", dict(window=fixture.a.canonical_market_id, rows=rows), NOW, NS + offset)
                for event in events:
                    journal.append("opportunities", event, NOW, NS + offset)
            journal.flush()
            # Use fixed duration for the synthetic statistical fixture.
            atomic_json(path / "metrics.json", dict(duration_seconds=301, streams={}))
            journal.close()
            result = analyze(path)
        self.assertEqual(len(result["full_windows"]), 1)
        self.assertEqual(result["edge_bucket_counts"]["A:10"][".10"], 0)
        self.assertEqual(result["edge_bucket_counts"]["A:10"][".08"], 1)
        self.assertEqual(result["valid_depth_seconds"]["A:10"], D(2))
        self.assertEqual(result["edge_bucket_seconds"]["A:10"]["0"], D(".1"))
        self.assertEqual(result["lifetime_ms"]["A:10"]["count"], 1)
        self.assertEqual(result["censored_lifetime_ms"]["A:10"]["count"], 0)
        self.assertEqual(result["observed_edge"]["A:10"]["median"], D("-.05"))


class CoverageTest(unittest.TestCase):
    def test_disconnect_bounds_time_before_stale_deadline(self):
        from app.scripts.analyze_prediction import remaining_valid_seconds

        row = dict(time_to_expiry="200", book_age_poly="100", book_age_limitless="200")
        self.assertEqual(remaining_valid_seconds(row, NS, 2000, [NS + 100_000_000]), D(".1"))
        self.assertEqual(remaining_valid_seconds(row, NS, 2000, [NS - 1]), D("1.8"))
