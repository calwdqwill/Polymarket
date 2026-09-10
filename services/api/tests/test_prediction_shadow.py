"""Synthetic fixtures only: independent expectations for the forward engine."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from app.prediction.shadow_engine import InMemoryEventTransport, ShadowEngine
from app.prediction.shadow_models import (
    KEYS,
    EventKind,
    ShadowBook,
    ShadowEvent,
    ShadowStrategyConfig,
    WindowSpec,
    canonical,
)
from app.prediction.shadow_replay import compact_results, replay_window, spec_for
from app.prediction.shadow_storage import InMemoryShadowJournal, JsonlShadowJournal
from app.scripts.verify_prediction_shadow import verify

START = datetime(1970, 1, 1, tzinfo=timezone.utc)
CONFIG = ShadowStrategyConfig(datetime(2026, 9, 10, tzinfo=timezone.utc))


class Fixture:
    def __init__(self, repository=None):
        self.repo = repository if repository is not None else InMemoryShadowJournal()
        self.engine = ShadowEngine(CONFIG, self.repo)
        self.transport = InMemoryEventTransport(self.engine)
        self.ordinal = 0
        self.spec = WindowSpec("fixture:BTC:0", START, START + timedelta(seconds=300),
                               (("Polymarket", "fixture-poly"), ("Limitless", "fixture-ll")))
        self.send(0, EventKind.WINDOW_START, window=self.spec)

    def send(self, ms, kind=EventKind.BOOK_VALID, books=(), **kwargs):
        self.ordinal += 1
        event = ShadowEvent(f"fixture:{self.ordinal}", kind, self.spec.canonical_market_id,
                            START + timedelta(milliseconds=ms), ms * 1_000_000, self.ordinal,
                            "synthetic", kwargs.pop("quality_status", "UNKNOWN"), books=books, **kwargs)
        self.transport.publish(event)
        return event

    def books(self, ms, prices=(".40", ".48", ".90", ".90"), sizes=None, deadline_ms=300000, kind=EventKind.BOOK_VALID):
        levels = tuple(ShadowBook(k, ((D(p), D((sizes or ["1000"] * 4)[i])),), "VALID", ms * 1_000_000,
                                  deadline_ms * 1_000_000, deadline_ms * 1_000_000, str(self.ordinal + 1),
                                  "synthetic", k.split(":")[0]) for i, (k, p) in enumerate(zip(KEYS, prices)))
        return self.send(ms, kind, books=levels)

    def advance(self, ms):
        self.transport.watermark(ms * 1_000_000)

    def attempts(self, ms=100):
        latest = {r["key"]: r["payload"] for r in self.repo.records("attempt")}
        return [a for a in latest.values() if a["scenario_ms"] == ms]

    def windows(self, ms=100):
        latest = {r["key"]: r["payload"] for r in self.repo.records("window")}
        return [a for a in latest.values() if a["scenario_ms"] == ms]


class ShadowTests(unittest.TestCase):
    def test_config_immutable_roundtrip_and_hash(self):
        self.assertEqual(CONFIG, ShadowStrategyConfig.from_json(CONFIG.to_json()))
        with self.assertRaises(FrozenInstanceError):
            CONFIG.q = D(11)
        with self.assertRaises(ValueError):
            replace(CONFIG, q=D(11))
        with self.assertRaises(ValueError):
            ShadowStrategyConfig.from_json(CONFIG.to_json().replace('"0.50"', '"0.51"'))
        self.assertNotEqual(CONFIG.config_hash, replace(CONFIG, created_at=START).config_hash)

    def test_first_crossing_and_fees(self):
        f = Fixture()
        f.books(1)
        self.assertEqual(f.attempts()[0]["state"], "SIMULATED_ORDERS_SENT")
        f.advance(501)
        a = f.attempts()[0]
        self.assertEqual(a["state"], "TARGET_CAPTURED")
        legs = {leg["venue"]: leg for leg in a["legs"]}
        self.assertEqual(D(legs["Limitless"]["requested_q"]), D("10.309279"))
        self.assertEqual(D(legs["Limitless"]["contracts_fee"]), D(".309279"))
        self.assertEqual(D(legs["Polymarket"]["cash_fee"]), D(".17472"))
        self.assertEqual(D(a["friction"]), D(".025"))
        self.assertEqual(D(a["simulated_net"]), D(".87656840"))

    def test_target_stops_window_and_no_repeated_signal(self):
        f = Fixture()
        f.books(1)
        for ms in (2, 50, 600, 700, 900):
            f.books(ms)
        f.books(1000, prices=(".5", ".5", ".9", ".9"))
        f.books(1100)
        self.assertEqual(len(f.attempts()), 1)
        self.assertEqual(len(f.attempts(250)), 1)

    def test_edge_open_lower_closed_upper_bounds(self):
        for price, expected in ((".50", 0), (".45", 1), (".449", 0)):
            f = Fixture()
            f.books(1, prices=(".4", price, ".9", ".9"))
            self.assertEqual(len(f.attempts()), expected)

    def test_new_crossing_and_max_three_attempts(self):
        f = Fixture()
        for base in (1, 1001, 2001, 3001):
            f.books(base, prices=(".5", ".5", ".9", ".9"))
            f.books(base + 1)
            f.send(base + 50, EventKind.CONNECTION_DOWN)
            f.advance(base + 600)
        self.assertEqual(len(f.attempts()), 3)
        self.assertEqual(len(f.attempts(250)), 3)
        self.assertTrue(all(a["result"] == "no_fill" for a in f.attempts()))

    def test_invalid_and_upper_cap_do_not_rearm(self):
        for mode in ("invalid", "cap", "depth"):
            f = Fixture()
            f.books(1)
            f.send(50, EventKind.CONNECTION_DOWN)
            f.advance(600)
            if mode == "cap":
                f.books(700, prices=(".3", ".4", ".9", ".9"))
            elif mode == "depth":
                f.books(700, sizes=["1"] * 4)
            else:
                f.send(700, EventKind.CONNECTION_UP)
            f.books(800)
            self.assertEqual(len(f.attempts()), 1, mode)

    def test_gap_after_below_does_not_extend_crossing(self):
        f = Fixture()
        f.books(1)
        f.send(50, EventKind.DESYNC)
        f.advance(600)
        f.books(700, prices=(".5", ".5", ".9", ".9"), deadline_ms=750)
        f.books(800)
        self.assertEqual(len(f.attempts()), 1)

    def test_invalid_blocks_entry_and_update_cannot_restore(self):
        f = Fixture()
        f.books(1, kind=EventKind.BOOK_UPDATE)
        self.assertEqual(f.attempts(), [])
        f.books(2)
        f.send(30, EventKind.CONNECTION_DOWN, venue="Polymarket")
        f.books(40, kind=EventKind.BOOK_UPDATE)
        f.advance(501)
        self.assertEqual(f.attempts()[0]["result"], "one_leg_failure")

    def test_not_equivalent_blocks_signal_and_arrival(self):
        f = Fixture()
        e = f.books(1, prices=(".5", ".5", ".9", ".9"))
        f.send(2, books=e.books, quality_status="NOT_EQUIVALENT")
        self.assertEqual(f.attempts(), [])
        e = f.books(3)
        f.send(50, books=e.books, quality_status="NOT_EQUIVALENT")
        f.advance(503)
        self.assertEqual(f.attempts()[0]["result"], "no_fill")

    def test_future_cannot_change_signal_or_prior_arrival(self):
        f = Fixture()
        f.books(1)
        prefix = canonical(f.attempts())
        f.books(102, prices=(".9", ".9", ".9", ".9"))
        f.advance(501)
        self.assertEqual(D(f.attempts()[0]["legs"][0]["vwap"]), D(".4"))
        self.assertGreater(D(f.attempts()[0]["simulated_net"]), 0)
        self.assertLess(D(f.attempts(250)[0]["simulated_net"]), 0)
        g = Fixture()
        g.books(1)
        self.assertEqual(prefix, canonical(g.attempts()))

    def test_adverse_before_arrival_and_tie_policy(self):
        for change_ms, good in ((100, False), (101, True)):
            f = Fixture()
            f.books(1)
            f.books(change_ms, prices=(".9", ".9", ".9", ".9"))
            f.advance(600)
            self.assertEqual(D(f.attempts()[0]["simulated_net"]) > 0, good)

    def test_partial_and_one_leg_and_zero_fill(self):
        for size, expected in (("5", "insufficient_depth"), ("1000", "one_leg_failure")):
            f = Fixture()
            f.books(1)
            if size == "5":
                f.books(50, sizes=[size] * 4)
            else:
                f.send(50, EventKind.DESYNC, venue="Polymarket")
            f.advance(600)
            a = f.attempts()[0]
            self.assertEqual(a["result"], expected)
            self.assertGreater(D(a["residual_q"]), 0)
            self.assertEqual(a["state"], "FAILED")

    def test_independent_latency_consumption(self):
        f = Fixture()
        f.books(1, sizes=["10.309279", "10", "10", "10.309279"])
        f.advance(501)
        a, b = f.attempts()[0], f.attempts(250)[0]
        self.assertEqual(a["simulated_net"], b["simulated_net"])
        self.assertEqual(a["signal_id"], b["signal_id"])
        self.assertIsNot(f.engine.ledgers[100].consumed, f.engine.ledgers[250].consumed)

    def test_deadline_without_messages_and_coverage(self):
        f = Fixture()
        f.books(1, deadline_ms=75)
        f.advance(300000)
        self.assertEqual(f.attempts()[0]["result"], "no_fill")
        self.assertEqual(f.windows()[0]["valid_coverage_ns"], 74_000_000)

    def test_connection_deadline_independent_from_book_freshness(self):
        f = Fixture()
        e = f.books(1, prices=(".5", ".5", ".9", ".9"))
        bs = tuple(replace(b, asks=((D(".44"), D(100)),), connection_deadline_ns=75_000_000) for b in e.books)
        f.send(2, books=bs)
        f.advance(502)
        self.assertEqual(f.attempts()[0]["result"], "no_fill")

    def test_tte_strict_boundary(self):
        f = Fixture()
        f.books(270000)
        self.assertEqual(f.attempts(), [])

    def test_empty_window_rotation_resets_state(self):
        f = Fixture()
        f.spec = replace(f.spec, canonical_market_id="fixture:BTC:300", start=START + timedelta(seconds=300),
                         end=START + timedelta(seconds=600))
        f.send(300000, EventKind.MARKET_ROTATED, window=f.spec)
        self.assertEqual(len(f.windows()), 2)
        self.assertEqual(f.windows()[0]["failure_reason"], "no_signal")
        self.assertEqual(f.engine.books, {})
        f.books(300001, deadline_ms=600000)
        self.assertEqual(len(f.attempts()), 1)

    def test_reordered_duplicate_and_clock_session_rejected(self):
        f = Fixture()
        event = f.books(1)
        for invalid in (event, replace(event, ordinal=100, monotonic_ns=0, books=()),
                        replace(event, ordinal=100, source_session="other", books=())):
            with self.assertRaises(ValueError):
                f.engine.on_event(invalid)

    def test_future_book_rejected(self):
        f = Fixture()
        e = f.books(1)
        with self.assertRaises(ValueError):
            replace(e, books=(replace(e.books[0], book_ns=2_000_000),))

    def test_invalid_recovery_clock_reset_is_allowed_but_valid_regression_is_not(self):
        f = Fixture()
        e = f.books(10, prices=(".5", ".5", ".9", ".9"))
        with self.assertRaises(ValueError):
            f.send(20, books=(replace(e.books[0], book_ns=0),))
        f.send(21, books=(replace(e.books[0], status="RECOVERING", book_ns=0),))
        self.assertFalse(f.engine._valid(f.engine.now_ns))
        f.books(22)
        self.assertTrue(f.engine._valid(f.engine.now_ns))

    def test_incomplete_stream_retains_fills_but_no_capture(self):
        f = Fixture()
        f.books(1)
        f.advance(150)
        f.engine.finish()
        a, b = f.attempts()[0], f.attempts(250)[0]
        self.assertEqual(a["result"], "STREAM_ENDED")
        self.assertGreater(D(a["simulated_net"]), 0)
        self.assertEqual(D(b["simulated_net"]), 0)
        self.assertEqual(f.windows()[0]["close_reason"], "STREAM_ENDED")

    def test_minimum_blocks_entry(self):
        f = Fixture()
        e = f.books(1, prices=(".5", ".5", ".9", ".9"))
        f.send(2, books=tuple(replace(b, min_order=D(20)) for b in e.books))
        f.books(3, sizes=["1"] * 4)
        self.assertEqual(f.attempts(), [])

    def test_storage_repeat_determinism_and_exclusive_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            with JsonlShadowJournal(path) as repo:
                f = Fixture(repo)
                f.books(1)
                f.advance(300000)
                with self.assertRaises(FileExistsError):
                    JsonlShadowJournal(path)
            g = Fixture()
            g.books(1)
            g.advance(300000)
            self.assertEqual(list(JsonlShadowJournal.read(path)), list(g.repo.records()))
            rows = list(g.repo.records())
            rows[0]["payload"]["q"] = "broken"
            self.assertEqual(next(g.repo.records())["payload"]["q"], "10")
            with path.open("a") as handle:
                handle.write('{"broken"')
            with self.assertRaises(json.JSONDecodeError):
                list(JsonlShadowJournal.read(path))

    def test_direction_b_uses_mirrored_no_once(self):
        f = Fixture()
        f.books(1, prices=(".9", ".9", ".48", ".4"))
        f.advance(501)
        a = f.attempts()[0]
        self.assertEqual(a["direction"], "B")
        self.assertEqual({leg["key"] for leg in a["legs"]}, {"Polymarket:YES", "Limitless:NO"})
        self.assertEqual(len(f.engine.ledgers[100].consumed), 2)

    def test_prior_losses_do_not_disappear_after_good_retry(self):
        f = Fixture()
        f.books(1)
        f.books(50, prices=(".9", ".9", ".9", ".9"))
        f.books(600, prices=(".5", ".5", ".9", ".9"))
        f.books(700)
        f.advance(1200)
        a, b = f.attempts()
        self.assertLess(D(a["simulated_net"]), 0)
        self.assertGreater(D(b["simulated_net"]), D(".5"))
        self.assertEqual(b["result"], "prior_losses_or_residual")
        self.assertFalse(f.engine.ledgers[100].window.target_captured)
        self.assertEqual(f.engine.ledgers[100].window.simulated_net,
                         D(a["simulated_net"]) + D(b["simulated_net"]))

    def test_failed_100ms_does_not_block_pending_250ms(self):
        f = Fixture()
        f.books(1)
        f.send(50, EventKind.CONNECTION_DOWN)
        f.books(150)
        f.advance(501)
        self.assertEqual(f.attempts()[0]["result"], "no_fill")
        self.assertEqual(f.attempts(250)[0]["result"], "TARGET_CAPTURED")
        f.books(600, prices=(".5", ".5", ".9", ".9"))
        f.books(700)
        self.assertEqual(len(f.attempts()), 2)
        self.assertEqual(len(f.attempts(250)), 1)

    def test_early_window_end_closes_pending_and_keeps_empty_next(self):
        f = Fixture()
        f.books(1)
        f.send(50, EventKind.WINDOW_END)
        f.advance(300000)
        self.assertEqual(f.attempts()[0]["result"], "EARLY_WINDOW_END")
        self.assertEqual(f.engine.books, {})
        self.assertEqual(len(f.windows()), 1)

    def test_discovery_is_not_rotation(self):
        f = Fixture()
        next_spec = replace(f.spec, canonical_market_id="next", start=START + timedelta(seconds=300),
                            end=START + timedelta(seconds=600))
        event = ShadowEvent("next", EventKind.MARKET_DISCOVERED, "next", START + timedelta(seconds=1),
                            1_000_000_000, 2, "synthetic", "UNKNOWN", window=next_spec)
        f.engine.on_event(event)
        self.assertEqual(f.engine.spec, f.spec)
        self.assertEqual(len(list(f.repo.records("market"))), 1)

    def test_replay_matches_online_fixture_and_is_deterministic(self):
        data = dict(window=0, levels=[[[[".4", "1000"]], []], [[[".48", "1000"]], []],
                                     [[[".9", "1000"]], []]],
                    books=[[i, "VALID", 1_000_000, 300_000_000_000, None] for i in (0, 1, 2)],
                    rows=[[1_000_000, 1000, 2, "UNKNOWN", 0, 1, 2, 2],
                          [299_000_000_000, 299000000, 3, "UNKNOWN", 0, 1, 2, 2]])
        spec = spec_for(0, {"Polymarket": "synthetic", "Limitless": "synthetic"})
        a = compact_results(replay_window(data, spec, "synthetic", CONFIG))
        b = compact_results(replay_window(data, spec, "synthetic", CONFIG))
        self.assertEqual(a, b)
        f = Fixture()
        f.books(1)
        f.advance(300000)
        self.assertEqual(a["attempts"][0]["simulated_net"], f.attempts()[0]["simulated_net"])
        self.assertTrue(all(w["closed_at"] is not None for w in a["windows"]))

    def test_independent_verifier_accepts_valid_journal_and_rejects_corrupt_cost(self):
        f = Fixture()
        f.books(1)
        f.advance(300000)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            rows = list(f.repo.records())

            def save():
                digest = hashlib.sha256(canonical(rows).encode()).hexdigest()
                (directory / "0.journal.jsonl").write_text("\n".join(canonical(r) for r in rows) + "\n", encoding="utf-8")
                (directory / "config.json").write_text(CONFIG.to_json(), encoding="utf-8")
                (directory / "summary.json").write_text(canonical(dict(status="COMPLETE", deterministic=True,
                    config_hash=CONFIG.config_hash, windows=1, sources={"0.json.gz": {
                        "journal_sha256": digest, "repeat_sha256": digest}})), encoding="utf-8")

            save()
            self.assertEqual(verify(directory)["attempts"], 2)
            last_attempt = [r for r in rows if r["kind"] == "attempt"][-1]
            last_attempt["payload"]["legs"][0]["cost"] = "999"
            save()
            with self.assertRaisesRegex(ValueError, "Cost mismatch"):
                verify(directory)


if __name__ == "__main__":
    unittest.main()
