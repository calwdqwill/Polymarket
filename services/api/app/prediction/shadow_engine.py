"""Forward-only event consumer with two independent research ledgers. No I/O transport."""

from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal as D

from app.prediction.execution_simulator import KEYS, empty_fill
from app.prediction.shadow_models import (
    EventKind,
    ShadowAttempt,
    ShadowEvent,
    ShadowStrategyConfig,
    ShadowWindow,
)
from app.prediction.shadow_storage import ShadowJournalRepository
from app.prediction.target_profit import economics, execute_leg, gross_request


@dataclass
class Ledger:
    window: ShadowWindow
    consumed: dict = field(default_factory=dict)
    armed: bool = True
    pending: ShadowAttempt | None = None
    arrival_done: bool = False


class ShadowEngine:
    """A single source clock/session and active window per engine instance.

    Timers at t run before the next receive event at t. advance(t) is a watermark:
    the caller promises that later events cannot have a receive clock below t.
    """

    def __init__(self, config: ShadowStrategyConfig, repository: ShadowJournalRepository):
        self._config = config
        self.repository = repository
        self.now_ns = -1
        self.ordinal = -1
        self.session = None
        self.books = {}
        self.quality = "UNKNOWN"
        self.spec = None
        self.anchor_ns = 0
        self.anchor_utc = None
        self.end_ns = 0
        self.ledgers: dict[int, Ledger] = {}
        self.last_window_end = None
        self._quote_signature = None
        self._observed_quotes = []
        self.repository.append("config", config.config_hash, config)

    @property
    def config(self):
        return self._config

    def _utc(self, ns):
        return self.anchor_utc + timedelta(microseconds=(ns - self.anchor_ns) // 1000)

    def _valid(self, ns):
        return len(self.books) == 4 and all(b.status_at(ns) == "VALID" for b in self.books.values())

    def _accrue(self, ns):
        if self.spec and self._valid(self.now_ns):
            deadline = min(min(b.freshness_deadline_ns, b.connection_deadline_ns) for b in self.books.values())
            duration = max(0, min(ns, self.end_ns, deadline) - self.now_ns)
            for ledger in self.ledgers.values():
                ledger.window.valid_coverage_ns += duration
                if deadline <= ns and ledger.window.attempts:
                    ledger.armed = False
        self.now_ns = ns

    def advance(self, ns: int):
        if ns < self.now_ns:
            raise ValueError("Regressing watermark")
        while self.spec:
            timers = [(self.end_ns, 0, 0, "end")]
            for ms, ledger in self.ledgers.items():
                if ledger.pending:
                    due = ledger.pending.signal_ns + ms * 1_000_000 * (2 if ledger.arrival_done else 1)
                    timers.append((due, 1 if not ledger.arrival_done else 2, ms,
                                   "ack" if ledger.arrival_done else "arrival"))
            due, _, ms, kind = min(timers)
            if due > ns:
                break
            self._accrue(due)
            if kind == "end":
                self._close("WINDOW_END")
            elif kind == "arrival":
                self._arrive(self.ledgers[ms])
            else:
                self._complete(self.ledgers[ms])
        self._accrue(ns)

    def on_event(self, event: ShadowEvent):
        if self.session is not None and event.source_session != self.session:
            raise ValueError("Clock session changed; start a new experiment")
        if event.ordinal <= self.ordinal or event.monotonic_ns < self.now_ns:
            raise ValueError("Duplicate/reordered receive event")
        if self.spec and event.canonical_market_id == self.spec.canonical_market_id:
            for book in event.books:
                previous = self.books.get(book.key)
                if previous and book.status == "VALID" and previous.status == "VALID" and book.book_ns < previous.book_ns:
                    raise ValueError("Regressing book clock")
        if event.kind in (EventKind.WINDOW_START, EventKind.MARKET_ROTATED):
            if not event.window.start <= event.receive_timestamp < event.window.end:
                raise ValueError("Window start outside active UTC interval")
            if self.last_window_end and event.window.start < self.last_window_end:
                raise ValueError("Cannot replay a closed window")
            if self.spec and event.window.start < self.spec.end:
                raise ValueError("Overlapping/duplicate window start")
        self.advance(event.monotonic_ns)
        self.session, self.ordinal = event.source_session, event.ordinal
        if event.kind == EventKind.MARKET_DISCOVERED:
            self.repository.append("market", event.event_id, event)
            return
        if event.kind in (EventKind.WINDOW_START, EventKind.MARKET_ROTATED):
            if self.spec:
                self._close("MARKET_ROTATED")
            self.spec = event.window
            self.anchor_ns, self.anchor_utc = event.monotonic_ns, event.receive_timestamp
            delta = event.window.end - event.receive_timestamp
            self.end_ns = event.monotonic_ns + (delta.days * 86400_000000 + delta.seconds * 1_000000 + delta.microseconds) * 1000
            self.quality, self.books = event.window.match_quality, {}
            self.ledgers = {
                ms: Ledger(ShadowWindow(event.window, self.config.config_hash, ms, event.receive_timestamp,
                                        match_quality=self.quality))
                for ms, _ in self.config.latency_pairs_ms
            }
            for ledger in self.ledgers.values():
                self._save_window(ledger)
            return
        if not self.spec or event.canonical_market_id != self.spec.canonical_market_id:
            return
        self.quality = event.quality_status
        for ledger in self.ledgers.values():
            ledger.window.match_quality = self.quality
        if event.kind == EventKind.WINDOW_END:
            self._close("WINDOW_END" if event.monotonic_ns >= self.end_ns else "EARLY_WINDOW_END")
            return
        if event.kind in (EventKind.BOOK_INVALID, EventKind.DESYNC, EventKind.CONNECTION_DOWN, EventKind.CONNECTION_UP):
            status = "RECOVERING" if event.kind == EventKind.CONNECTION_UP else "DESYNC"
            for key, book in list(self.books.items()):
                if (event.venue is None or key.startswith(event.venue + ":")) and (
                    event.outcome is None or key.endswith(":" + event.outcome)
                ):
                    self.books[key] = replace(book, status=status)
            self.repository.append("quality", event.event_id, event)
        elif event.kind in (EventKind.BOOK_UPDATE, EventKind.BOOK_VALID):
            for book in event.books:
                old = self.books.get(book.key)
                # Only a verified full state can restore validity after disconnect/DESYNC.
                if event.kind == EventKind.BOOK_UPDATE and (
                    old is None or old.status != "VALID" or old.connection != book.connection
                ):
                    self.books[book.key] = replace(book, status="RECOVERING")
                else:
                    self.books[book.key] = book
        self._evaluate(event)

    def _snapshot(self, ns):
        levels, books = [], {}
        for key, book in sorted(self.books.items()):
            books[key] = dict(ladder=len(levels), status=book.status_at(ns), book_ns=book.book_ns,
                              min_order=book.min_order)
            levels.append((book.asks, ()))
        return dict(source_ns=self.now_ns, ordinal=self.ordinal, quality=self.quality, books=books), levels

    def _quotes(self, snapshot, levels, consumed):
        options = []
        for direction, keys in KEYS.items():
            fills = []
            for key in keys:
                requested = gross_request(snapshot, levels, key, self.config.q, "conservative", consumed)
                fills.append(execute_leg(snapshot, levels, key, self.now_ns, requested, consumed,
                                         "conservative", commit=False))
            if all(f["status"] == "FULL_FILL" for f in fills):
                value = economics(fills, self.config.friction)
                options.append(dict(direction=direction, edge=1 - sum(f["vwap"] for f in fills),
                                    requests={f["key"]: f["requested_q"] for f in fills}, **value))
        return options

    def _evaluate(self, event):
        if self.quality == "NOT_EQUIVALENT" or not self._valid(self.now_ns):
            for ledger in self.ledgers.values():
                if ledger.window.attempts:
                    ledger.armed = False
                if not ledger.window.attempts:
                    ledger.window.failure_reason = "NOT_EQUIVALENT" if self.quality == "NOT_EQUIVALENT" else "invalid_books"
            return
        signature = tuple((k, b.asks, b.min_order) for k, b in sorted(self.books.items()))
        snapshot = levels = None
        if signature != self._quote_signature:
            snapshot, levels = self._snapshot(self.now_ns)
            self._observed_quotes = self._quotes(snapshot, levels, {})
            self._quote_signature = signature
        observed = self._observed_quotes
        utc_delta = self.spec.end - event.receive_timestamp
        utc_tte = D(utc_delta.days * 86400 + utc_delta.seconds) + D(utc_delta.microseconds) / 1_000_000
        tte = min(D(self.end_ns - self.now_ns) / 1_000_000_000, utc_tte)
        for ms, ledger in self.ledgers.items():
            window = ledger.window
            for option in observed:
                old = window.max_edge[option["direction"]]
                window.max_edge[option["direction"]] = option["edge"] if old is None else max(old, option["edge"])
            if ledger.pending or window.target_captured or len(window.attempts) >= self.config.max_attempts:
                continue
            # A loss of depth, an invalid interval or an upper-cap exit cannot rearm.
            if len(observed) == 2 and all(o["edge"] <= self.config.min_edge for o in observed):
                ledger.armed = True
            if tte <= self.config.min_tte_seconds:
                if not window.attempts:
                    window.failure_reason = "TTE_filter"
                continue
            if not ledger.armed:
                continue
            if ledger.consumed:
                if snapshot is None:
                    snapshot, levels = self._snapshot(self.now_ns)
                options = self._quotes(snapshot, levels, ledger.consumed)
            else:
                options = observed
            eligible = [o for o in options if self.config.min_edge < o["edge"] <= self.config.max_edge and o["net"] > 0]
            if not eligible:
                continue
            chosen = min(eligible, key=lambda o: (-o["net"], o["residual_q"], o["direction"]))
            aid = f"{self.config.config_hash[:16]}:{event.canonical_market_id}:{ms}:{len(window.attempts) + 1}"
            attempt = ShadowAttempt(
                attempt_id=aid, signal_id=event.event_id, window_id=event.canonical_market_id,
                strategy_version=self.config.version, config_hash=self.config.config_hash,
                direction=chosen["direction"], q=self.config.q, signal_time=event.receive_timestamp,
                signal_ns=self.now_ns, signal_ordinal=event.ordinal, signal_edge=chosen["edge"],
                predicted_net=chosen["net"], tte=tte, signal_books=tuple(self.books[k] for k in KEYS[chosen["direction"]]),
                scenario_ms=ms, requests=chosen["requests"],
            )
            ledger.pending, ledger.arrival_done, ledger.armed = attempt, False, False
            window.attempts.append(aid)
            window.selected_directions.append(attempt.direction)
            for state in ("SIGNAL_DETECTED", "ATTEMPT_CREATED", "SIMULATED_ORDERS_SENT"):
                self._transition(attempt, state)

    def _transition(self, attempt, state):
        attempt.state = state
        attempt.transitions.append(dict(state=state, monotonic_ns=self.now_ns, timestamp=self._utc(self.now_ns)))
        self.repository.append("attempt", attempt.attempt_id, attempt)

    def _arrive(self, ledger):
        attempt = ledger.pending
        self._transition(attempt, f"ARRIVAL_{attempt.scenario_ms}MS")
        snapshot, levels = self._snapshot(self.now_ns)
        for key in sorted(KEYS[attempt.direction]):
            result = execute_leg(snapshot, levels, key, self.now_ns, attempt.requests[key], ledger.consumed,
                                 "conservative")
            attempt.legs.append(dict(venue=key.split(":")[0], outcome=key.split(":")[1],
                                     arrival_time=self._utc(self.now_ns), arrival_book=self.books.get(key), **result))
        ledger.arrival_done = True
        full = all(f["status"] == "FULL_FILL" for f in attempt.legs)
        state = "FILLED" if full else "PARTIAL" if any(f["filled_q"] > 0 for f in attempt.legs) else "NO_FILL"
        self._transition(attempt, state)

    def _complete(self, ledger, forced_reason=None):
        attempt = ledger.pending
        if not ledger.arrival_done:
            for key in sorted(KEYS[attempt.direction]):
                attempt.legs.append(dict(venue=key.split(":")[0], outcome=key.split(":")[1],
                                         arrival_time=None, arrival_book=None,
                                         **empty_fill(key, self.now_ns, attempt.requests[key], forced_reason)))
        fs = attempt.legs
        value = economics(fs, self.config.friction)
        attempt.matched_q, attempt.residual_q = value["matched_q"], value["residual_q"]
        attempt.cash_fees = sum((f["cash_fee"] for f in fs), D(0))
        attempt.contracts_fees = sum((f["contracts_fee"] for f in fs), D(0))
        attempt.friction, attempt.simulated_net = value["friction"], value["net"]
        # Gross here is the retained-payout floor before cash fees and friction.
        attempt.gross_pnl = attempt.matched_q - sum((f["cost"] for f in fs), D(0))
        window = ledger.window
        window.simulated_net += attempt.simulated_net
        full = all(f["status"] == "FULL_FILL" for f in fs)
        captured = (not forced_reason and full and attempt.residual_q <= self.config.residual_tolerance
                    and attempt.simulated_net >= self.config.target_net and window.simulated_net >= self.config.target_net)
        if captured:
            attempt.result = "TARGET_CAPTURED"
            window.target_captured, window.failure_reason = True, ""
            window.capture_timestamp = self._utc(self.now_ns)
            delta = window.capture_timestamp - window.spec.start
            window.capture_seconds = D(delta.seconds) + D(delta.microseconds) / 1_000_000
        else:
            count = sum(f["retained_q"] > 0 for f in fs)
            attempt.result = (forced_reason or ("one_leg_failure" if count == 1 else "no_fill" if not count
                              else "insufficient_depth" if not full else "prior_losses_or_residual"
                              if attempt.simulated_net >= self.config.target_net else "latency_decay"))
            window.failure_reason = attempt.result
        self._transition(attempt, "TARGET_CAPTURED" if captured else "FAILED")
        ledger.pending = None
        self._save_window(ledger)

    def _save_window(self, ledger):
        window = ledger.window
        self.repository.append("window", f"{window.spec.canonical_market_id}:{window.scenario_ms}", window)

    def _close(self, reason):
        for ledger in self.ledgers.values():
            if ledger.pending:
                self._complete(ledger, reason)
            ledger.window.closed_at = self._utc(self.now_ns)
            ledger.window.close_reason = reason
            if reason != "WINDOW_END" and not ledger.window.target_captured:
                ledger.window.failure_reason = reason
            self._save_window(ledger)
        self.last_window_end = self.spec.end
        self.spec, self.books = None, {}

    def finish(self):
        """Explicit incomplete-stream close; does not invent future coverage or fills."""
        if self.spec:
            self._close("STREAM_ENDED")


class InMemoryEventTransport:
    """Synchronous fixture transport with backpressure; does not retain the event stream."""

    def __init__(self, engine: ShadowEngine):
        self.engine = engine

    def publish(self, event: ShadowEvent):
        self.engine.on_event(event)

    def watermark(self, monotonic_ns: int):
        self.engine.advance(monotonic_ns)
