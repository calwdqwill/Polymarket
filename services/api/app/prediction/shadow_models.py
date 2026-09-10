"""Immutable local shadow contracts. Prices and research amounts are Decimal."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
from enum import StrEnum

from app.prediction.storage import json_default


def canonical(value) -> str:
    return json.dumps(value, default=json_default, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ShadowStrategyConfig:
    created_at: datetime
    strategy_id: str = "micro_arb_v0"
    version: str = "1.0.0"
    q: D = D("10")
    target_net: D = D("0.50")
    min_edge: D = D("0.10")
    max_edge: D = D("0.15")
    min_tte_seconds: int = 30
    latency_pairs_ms: tuple[tuple[int, int], ...] = ((100, 100), (250, 250))
    limitless_fee: D = D("0.03")
    poly_fee_coefficient: D = D("0.07")
    friction: D = D("0.0025")
    max_attempts: int = 3
    max_captures: int = 1
    residual_tolerance: D = D("0.01")
    mode: str = "parallel"
    retry: str = "observed_below_threshold_crossing"
    fee_status: str = "FEE_UNKNOWN"
    q_semantics: str = "retained_target_gross_request_at_signal"
    entry_net_rule: str = "positive_after_fees_and_friction"
    capture_rule: str = "full_pair_residual_tolerance_trade_and_cumulative_target"
    ack_latency_multiplier: int = 2
    cash_fee_rounding: str = "CEILING_0.00001_per_order"
    contracts_rounding: str = "CEILING_0.000001_per_order"
    depletion_rule: str = "persistent_venue_outcome_price_per_window"
    event_tie_rule: str = "timers_before_receive_at_equal_monotonic_ns"
    entry_quality_rule: str = "all_four_books_valid_NOT_EQUIVALENT_blocked"
    residual_valuation: str = "zero_conditional_matched_payout_floor"

    def __post_init__(self):
        if self.created_at.tzinfo is None or self.created_at.utcoffset().total_seconds() != 0:
            raise ValueError("Config creation timestamp must be UTC")
        defaults = {f.name: f.default for f in self.__dataclass_fields__.values() if f.name != "created_at"}
        for name, expected in defaults.items():
            value = getattr(self, name)
            if type(value) is not type(expected) or value != expected:
                raise ValueError(f"Frozen profile cannot change {name}")
        if any(type(pair) is not tuple or any(type(ms) is not int for ms in pair) for pair in self.latency_pairs_ms):
            raise ValueError("Latency scenarios require immutable integer pairs")

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()

    def to_json(self) -> str:
        return canonical(dict(config=asdict(self), config_hash=self.config_hash))

    @classmethod
    def from_json(cls, text: str):
        doc = json.loads(text)
        values = doc["config"]
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        values["latency_pairs_ms"] = tuple(tuple(pair) for pair in values["latency_pairs_ms"])
        for name in ("q", "target_net", "min_edge", "max_edge", "limitless_fee", "poly_fee_coefficient",
                     "friction", "residual_tolerance"):
            values[name] = D(values[name])
        result = cls(**values)
        if doc["config_hash"] != result.config_hash:
            raise ValueError("Config hash mismatch")
        return result


class EventKind(StrEnum):
    MARKET_DISCOVERED = "MARKET_DISCOVERED"
    MARKET_ROTATED = "MARKET_ROTATED"
    BOOK_VALID = "BOOK_VALID"
    BOOK_UPDATE = "BOOK_UPDATE"
    BOOK_INVALID = "BOOK_INVALID"
    DESYNC = "DESYNC"
    CONNECTION_DOWN = "CONNECTION_DOWN"
    CONNECTION_UP = "CONNECTION_UP"
    WINDOW_START = "WINDOW_START"
    WINDOW_END = "WINDOW_END"


KEYS = ("Limitless:YES", "Polymarket:NO", "Polymarket:YES", "Limitless:NO")
QUALITIES = ("EXACT", "PROVISIONAL", "UNKNOWN", "NOT_EQUIVALENT")


@dataclass(frozen=True)
class ShadowBook:
    key: str
    asks: tuple[tuple[D, D], ...]
    status: str
    book_ns: int
    freshness_deadline_ns: int
    connection_deadline_ns: int
    version: str
    source_session: str
    connection: str
    min_order: D | None = None

    def __post_init__(self):
        if self.key not in KEYS or not self.version or not self.source_session or not self.connection:
            raise ValueError("Invalid book identity")
        if self.status not in ("VALID", "STALE", "DESYNC", "RECOVERING", "MISSING", "INVALID"):
            raise ValueError("Invalid book quality")
        if not isinstance(self.asks, tuple) or any(not isinstance(level, tuple) for level in self.asks):
            raise ValueError("Book ladders must be immutable")
        prices = []
        for p, q in self.asks:
            if not isinstance(p, D) or not isinstance(q, D) or not p.is_finite() or not q.is_finite():
                raise ValueError("Finite Decimal book required")
            if not 0 <= p <= 1 or q <= 0:
                raise ValueError("Invalid price/quantity")
            prices.append(p)
        if prices != sorted(set(prices)):
            raise ValueError("Ask levels must be unique and sorted")
        if self.min_order is not None and (not self.min_order.is_finite() or self.min_order < 0):
            raise ValueError("Invalid minimum")

    def status_at(self, ns: int) -> str:
        if self.status == "VALID" and ns >= min(self.freshness_deadline_ns, self.connection_deadline_ns):
            return "STALE"
        return self.status


@dataclass(frozen=True)
class WindowSpec:
    canonical_market_id: str
    start: datetime
    end: datetime
    market_ids: tuple[tuple[str, str], ...]
    match_quality: str = "UNKNOWN"
    asset: str = "BTC"

    def __post_init__(self):
        if self.asset != "BTC" or not self.canonical_market_id or self.match_quality not in QUALITIES:
            raise ValueError("Only BTC 5m research windows are supported")
        for stamp in (self.start, self.end):
            if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError("UTC window timestamps required")
        if (self.end - self.start).total_seconds() != 300 or self.start.timestamp() % 300:
            raise ValueError("Aligned 5m window required")
        if not isinstance(self.market_ids, tuple) or any(not isinstance(p, tuple) for p in self.market_ids):
            raise ValueError("Immutable market identities required")
        if {v for v, _ in self.market_ids} != {"Polymarket", "Limitless"} or len(self.market_ids) != 2:
            raise ValueError("Both venue market IDs required")
        if any(not market_id for _, market_id in self.market_ids):
            raise ValueError("Missing market ID")


@dataclass(frozen=True)
class ShadowEvent:
    event_id: str
    kind: EventKind
    canonical_market_id: str
    receive_timestamp: datetime
    monotonic_ns: int
    ordinal: int
    source_session: str
    quality_status: str
    venue: str | None = None
    outcome: str | None = None
    books: tuple[ShadowBook, ...] = ()
    window: WindowSpec | None = None

    def __post_init__(self):
        if not isinstance(self.kind, EventKind) or self.quality_status not in QUALITIES:
            raise ValueError("Unknown event/quality")
        if not self.event_id or not self.source_session or not self.canonical_market_id:
            raise ValueError("Missing envelope identity")
        if self.receive_timestamp.tzinfo is None or self.receive_timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("UTC receive timestamp required")
        if self.monotonic_ns < 0 or self.ordinal < 0:
            raise ValueError("Negative event clock")
        if self.venue not in (None, "Polymarket", "Limitless") or self.outcome not in (None, "YES", "NO"):
            raise ValueError("Unsupported venue/outcome")
        if not isinstance(self.books, tuple) or len({b.key for b in self.books}) != len(self.books):
            raise ValueError("Atomic immutable book batch required")
        for book in self.books:
            if book.book_ns > self.monotonic_ns or book.source_session != self.source_session:
                raise ValueError("Future book or foreign clock session")
            if self.venue and not book.key.startswith(self.venue + ":"):
                raise ValueError("Venue does not match book")
            if self.outcome and not book.key.endswith(":" + self.outcome):
                raise ValueError("Outcome does not match book")
        if self.window and self.window.canonical_market_id != self.canonical_market_id:
            raise ValueError("Window identity mismatch")
        if self.kind in (EventKind.WINDOW_START, EventKind.MARKET_ROTATED, EventKind.MARKET_DISCOVERED):
            if self.window is None:
                raise ValueError("Market lifecycle requires window metadata")


@dataclass
class ShadowAttempt:
    attempt_id: str
    signal_id: str
    window_id: str
    strategy_version: str
    config_hash: str
    direction: str
    q: D
    signal_time: datetime
    signal_ns: int
    signal_ordinal: int
    signal_edge: D
    predicted_net: D
    tte: D
    signal_books: tuple[ShadowBook, ...]
    scenario_ms: int
    requests: dict[str, D]
    state: str = "WAITING"
    transitions: list[dict] = field(default_factory=list)
    legs: list[dict] = field(default_factory=list)
    matched_q: D = D(0)
    residual_q: D = D(0)
    cash_fees: D = D(0)
    contracts_fees: D = D(0)
    friction: D = D(0)
    gross_pnl: D = D(0)
    simulated_net: D = D(0)
    result: str = "PENDING"


@dataclass
class ShadowWindow:
    spec: WindowSpec
    config_hash: str
    scenario_ms: int
    observed_start: datetime
    valid_coverage_ns: int = 0
    attempts: list[str] = field(default_factory=list)
    selected_directions: list[str] = field(default_factory=list)
    max_edge: dict[str, D | None] = field(default_factory=lambda: {"A": None, "B": None})
    target_captured: bool = False
    capture_timestamp: datetime | None = None
    capture_seconds: D | None = None
    simulated_net: D = D(0)
    failure_reason: str = "no_signal"
    closed_at: datetime | None = None
    close_reason: str | None = None
    match_quality: str = "UNKNOWN"


def utc_from_us(us: int) -> datetime:
    seconds, micros = divmod(us, 1_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=micros)
