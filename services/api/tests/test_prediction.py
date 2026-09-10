import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.discovery import Discovery, normalize_limitless, normalize_poly
from app.prediction.models import SETTLEMENT_FIELDS, Book, Market, decimal, match_markets, vwap
from app.prediction.observer import Observer, lifetime_bucket
from app.prediction.storage import Journal, dumps

D = Decimal
NOW = datetime(2026, 9, 9, 13, 20, tzinfo=timezone.utc)
NS = 10_000_000_000


def synthetic_market(venue):
    # Explicit synthetic settlement contract, never used by production discovery.
    return Market(
        venue,
        venue + "-test",
        "test",
        "test",
        {"YES": "yes", "NO": "no"},
        NOW,
        NOW + timedelta(minutes=5),
        "BTC",
        True,
        "synthetic fixture",
        dict.fromkeys(SETTLEMENT_FIELDS, "fixture-identical"),
        dict.fromkeys(SETTLEMENT_FIELDS, "test://fixture"),
        D(5),
        D(".01"),
    )


def book(market, outcome, asks):
    result = Book(market.venue, market.market_id, market.canonical_market_id, outcome)
    result.snapshot([], [{"price": p, "size": s} for p, s in asks], NOW, NS)
    return result


class PredictionTest(unittest.TestCase):
    def setUp(self):
        self.a, self.b = synthetic_market("Polymarket"), synthetic_market("Limitless")
        self.books = {}
        for market in (self.a, self.b):
            for outcome in ("YES", "NO"):
                self.books[market.venue, market.market_id, outcome] = book(market, outcome, [(".45", "1000")])

    def evaluate(self, observer=None, ns=NS):
        return (observer or Observer()).evaluate(self.a, self.b, self.books, NOW, ns)

    def test_example_depth_and_pnl(self):
        y = {D(".43"): D(50), D(".44"): D(100), D(".48"): D(400)}
        n = {D(".46"): D(100), D(".47"): D(300), D(".49"): D(500)}
        self.assertEqual(50 * (1 - vwap(y, D(50)) - vwap(n, D(50))), D("5.50"))
        self.assertEqual(vwap(y, D(500)), D(".467"))
        self.assertEqual(vwap(n, D(500)), D(".472"))
        self.assertEqual(500 * (1 - vwap(y, D(500)) - vwap(n, D(500))), D("30.50"))
        self.assertIsNone(vwap(y, D(1000)))

    def test_precision_and_invalid_values(self):
        for value in (0.1, "NaN", "Infinity", True, "broken", None):
            with self.assertRaises(ValueError):
                decimal(value)
        self.assertEqual(decimal("0.1234567890123456789"), D("0.1234567890123456789"))
        with self.assertRaises(ValueError):
            vwap({}, D(0))

    def test_missing_evidence_is_not_exact(self):
        del self.b.evidence["fallback"]
        self.assertEqual(match_markets(self.a, self.b).quality, "UNKNOWN")
        rows, events = self.evaluate()
        self.assertTrue(all(r["status"] == "OPPORTUNITY" for r in rows))
        self.assertTrue(all(r["research_only"] for r in rows))
        self.assertEqual(len(events), 14)

    def test_each_settlement_field_required(self):
        for field in SETTLEMENT_FIELDS:
            with self.subTest(field=field):
                b = copy.deepcopy(self.b)
                b.settlement[field] = None
                self.assertEqual(match_markets(self.a, b).quality, "UNKNOWN")

    def test_each_difference_disqualifies_pair(self):
        for field in SETTLEMENT_FIELDS:
            with self.subTest(field=field):
                b = copy.deepcopy(self.b)
                b.settlement[field] = "different"
                self.assertEqual(match_markets(self.a, b).quality, "NOT_EQUIVALENT")

    def test_window_and_venue_validation(self):
        self.b.end += timedelta(seconds=1)
        self.assertEqual(match_markets(self.a, self.b).quality, "NOT_EQUIVALENT")
        self.assertEqual(match_markets(self.a, self.a).quality, "NOT_EQUIVALENT")

    def test_both_directions_and_all_sizes(self):
        rows, events = self.evaluate()
        self.assertEqual(len(rows), 14)
        self.assertEqual(len(events), 14)
        self.assertEqual(rows[0]["gross_pnl"], D(1))

    def test_depth_is_not_partial_execution(self):
        self.books[self.a.venue, self.a.market_id, "YES"].asks = {D(".1"): D(9)}
        rows, events = self.evaluate()
        self.assertEqual(rows[0]["status"], "NOT_EXECUTABLE")
        self.assertIsNone(rows[0]["gross_pnl"])
        self.assertEqual(len(events), 7)

    def test_stale_closes_without_extending_lifetime(self):
        observer = Observer()
        self.evaluate(observer)
        self.evaluate(observer, NS + 20_000_000)
        rows, events = self.evaluate(observer, NS + 2_000_000_000)
        self.assertTrue(all(r["status"] == "STALE" for r in rows))
        self.assertTrue(all(e["event_type"] == "CLOSE" and e["censored"] for e in events))
        self.assertTrue(all(e["duration_ms"] == D(20) for e in events))
        self.assertFalse(observer.open_events)

    def test_no_edge_closes_and_reappearance_opens_new_episode(self):
        observer = Observer()
        self.evaluate(observer)
        for value in self.books.values():
            value.asks = {D(".6"): D(1000)}
        rows, events = self.evaluate(observer, NS + 50_000_000)
        self.assertTrue(all(r["status"] == "NO_EDGE" for r in rows))
        self.assertTrue(all(not e["censored"] for e in events))
        for value in self.books.values():
            value.asks = {D(".4"): D(1000)}
        _, events = self.evaluate(observer, NS + 60_000_000)
        self.assertTrue(all(e["event_type"] == "OPEN" and e["duration_ms"] == 0 for e in events))

    def test_stop_censors(self):
        observer = Observer()
        self.evaluate(observer)
        self.assertTrue(all(e["censored"] for e in observer.close_all(NOW)))

    def test_expiry_and_minimum(self):
        self.b.min_order_size = None
        rows, events = self.evaluate()
        self.assertEqual(len(events), 14)
        self.assertTrue(all(not r["minimum_verified"] for r in rows))
        self.b.min_order_size = D(50)
        rows, _ = self.evaluate()
        self.assertEqual(rows[0]["status"], "BELOW_MIN_ORDER_SIZE")
        rows, events = Observer().evaluate(self.a, self.b, self.books, self.a.end, NS)
        self.assertFalse(events)
        self.assertTrue(all(r["status"] == "INACTIVE" for r in rows))

    def test_wrong_book_identity(self):
        self.books[self.a.venue, self.a.market_id, "YES"].market_id = "wrong-window"
        rows, _ = self.evaluate()
        self.assertEqual(rows[0]["status"], "DESYNC")

    def test_lifetime_buckets(self):
        self.assertEqual(lifetime_bucket(D("24.999")), "<25 ms")
        self.assertEqual(lifetime_bucket(D(25)), "25-50 ms")
        self.assertEqual(lifetime_bucket(D(5000)), ">=5 sec")

    def test_limitless_mirror_snapshot_version(self):
        adapter = LimitlessBooks(self.b)
        frame = {
            "marketSlug": "test",
            "version": 5,
            "orderbook": {"bids": [{"price": D(".54"), "size": D(100)}], "asks": [{"price": D(".57"), "size": D(50)}]},
        }
        adapter.apply(frame, NOW, NS)
        self.assertEqual(adapter.no.asks, {D(".46"): D(100)})
        self.assertEqual(adapter.no.bids, {D(".43"): D(50)})
        frame["version"] = 4
        self.assertFalse(adapter.apply(frame, NOW, NS + 1))
        frame["version"], frame["orderbook"]["asks"] = 10, []
        adapter.apply(frame, NOW, NS + 2)
        self.assertFalse(adapter.no.bids)

    def test_polymarket_snapshot_delta_removal(self):
        adapter = PolymarketBooks(self.a)
        adapter.apply(
            {
                "event_type": "book",
                "asset_id": "yes",
                "timestamp": "100",
                "bids": [],
                "asks": [{"price": ".5", "size": "100"}],
            },
            NOW,
            NS,
        )
        delta = {
            "event_type": "price_change",
            "timestamp": "101",
            "price_changes": [{"asset_id": "yes", "side": "SELL", "price": ".5", "size": "0"}],
        }
        adapter.apply(delta, NOW, NS + 1)
        self.assertFalse(adapter.books["yes"].asks)
        delta["timestamp"] = "99"
        adapter.apply(delta, NOW, NS + 2)
        self.assertEqual(adapter.books["yes"].status, "VALID")
        delta["price_changes"][0]["size"] = "10"
        adapter.apply(delta, NOW, NS + 3)
        self.assertEqual(adapter.books["yes"].status, "DESYNC")

    def test_delta_without_snapshot_and_disconnect(self):
        adapter = PolymarketBooks(self.a)
        self.assertFalse(
            adapter.apply(
                {
                    "event_type": "price_change",
                    "timestamp": "10",
                    "price_changes": [{"asset_id": "yes", "side": "SELL", "price": ".5", "size": "100"}],
                },
                NOW,
                NS,
            )
        )
        adapter.disconnect()
        self.assertTrue(all(b.status == "DESYNC" for b in adapter.books.values()))

    def test_malformed_snapshot_invalidates(self):
        value = book(self.a, "YES", [(".5", "100")])
        with self.assertRaises(ValueError):
            value.snapshot([], [{"price": "1.1", "size": "10"}], NOW, NS)
        self.assertEqual(value.status, "DESYNC")

    def test_book_serialization(self):
        value = book(self.a, "YES", [(".5", "100")])
        payload = json.loads(dumps(value))
        self.assertEqual(payload["asks"], [{"side": "SELL", "price": "0.5", "size": "100"}])

    def test_live_metadata_fixture_remains_unknown(self):
        path = Path(__file__).parent / "fixtures" / "prediction_metadata.json"
        fixture = json.loads(path.read_text(encoding="utf-8"), parse_float=D)
        a = normalize_poly(fixture["polymarket"], fixture["polymarket_url"])
        b = normalize_limitless(fixture["limitless"], fixture["limitless_url"])
        self.assertEqual(a.start, b.start)
        self.assertEqual(a.settlement["twap_seconds"], "60")
        self.assertEqual(a.settlement["reference"], b.settlement["reference"])
        self.assertEqual(match_markets(a, b).quality, "UNKNOWN")
        self.assertIsNone(b.min_order_size)
        fixture["limitless"]["metadata"]["openPrice"] = "---"
        b = normalize_limitless(fixture["limitless"], fixture["limitless_url"])
        self.assertIsNone(b.settlement["strike"])

    def test_http_error_preserved_and_no_credentials_sent(self):
        async def run():
            def respond(request):
                self.assertNotIn("authorization", request.headers)
                self.assertEqual(request.method, "GET")
                return httpx.Response(404, json={"error": "not found"})

            with tempfile.TemporaryDirectory() as directory:
                journal = Journal(Path(directory) / "run")
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    discovery = Discovery(client, journal)
                    self.assertIsNone(await discovery.get("https://example.test/market"))
                    self.assertEqual(len(discovery.errors), 1)
                self.assertEqual(journal.counts["raw_http"], 1)
                metrics = journal.metrics()
                journal.append("extra", {})
                self.assertNotIn("extra", metrics["rows"])
                self.assertIsNone(metrics["mb_per_hour"])
                with self.assertRaises(FileExistsError):
                    Journal(Path(directory) / "run")

        asyncio.run(run())
