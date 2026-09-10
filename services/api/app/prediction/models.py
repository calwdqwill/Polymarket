from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

SETTLEMENT_FIELDS = (
    "strike",
    "reference",
    "feed_id",
    "strike_method",
    "final_price_method",
    "timestamp_boundary",
    "twap_seconds",
    "equality",
    "precision",
    "rounding",
    "cancellation",
    "fallback",
    "payout",
    "payout_currency",
    "payout_economics",
)

MATCH_QUALITIES = ("EXACT", "PROVISIONAL", "UNKNOWN", "NOT_EQUIVALENT")


def decimal(value) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("Decode JSON with parse_float=Decimal; binary floats are not accepted")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid financial value") from exc
    if not result.is_finite():
        raise ValueError("Non-finite financial value")
    return result


@dataclass
class Market:
    venue: str
    market_id: str
    slug: str
    condition_id: str
    tokens: dict[str, str]
    start: datetime
    end: datetime
    asset: str
    active: bool
    rules: str
    settlement: dict[str, str | None]
    evidence: dict[str, str]
    min_order_size: Decimal | None = None
    tick_size: Decimal | None = None
    size_scale: Decimal = Decimal(1)

    @property
    def canonical_market_id(self) -> str:
        return f"{self.asset}-5M-{self.start:%Y%m%dT%H%M%SZ}"


@dataclass
class Match:
    quality: str
    missing: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)


def match_markets(a: Market, b: Market) -> Match:
    missing, differences = [], []
    if a.venue == b.venue:
        differences.append("venue: must be independent")
    for name in ("asset", "start", "end"):
        if getattr(a, name) != getattr(b, name):
            differences.append(name)
    for market in (a, b):
        if market.asset != "BTC" or (market.end - market.start).total_seconds() != 300:
            differences.append(f"{market.venue}: not BTC 5m")
        if market.start.timestamp() % 300:
            differences.append(f"{market.venue}: unaligned window")
        if set(market.tokens) != {"YES", "NO"} or len(set(market.tokens.values())) != 2:
            missing.append(f"{market.venue}.outcome_mapping")
    for name in SETTLEMENT_FIELDS:
        values = [m.settlement.get(name) for m in (a, b)]
        for market, value in zip((a, b), values):
            if value is None or value == "" or not market.evidence.get(name):
                missing.append(f"{market.venue}.{name}")
        if all(v is not None and v != "" for v in values) and values[0] != values[1]:
            differences.append(name)
    quality = "NOT_EQUIVALENT" if differences else "UNKNOWN" if missing else "EXACT"
    return Match(quality, missing, differences)


@dataclass
class Book:
    venue: str
    market_id: str
    canonical_market_id: str
    outcome: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    received_timestamp: datetime | None = None
    received_monotonic_ns: int = 0
    exchange_timestamp: str | None = None
    status: str = "DESYNC"
    connection_deadline_ns: int = 0
    freshness_ttl_ms: int | None = None

    def snapshot(
        self, bids: list, asks: list, received: datetime, mono_ns: int, exchange_timestamp: str | None = None
    ) -> None:
        self.status = "DESYNC"
        self.bids, self.asks = self._levels(bids), self._levels(asks)
        self.received_timestamp, self.received_monotonic_ns = received, mono_ns
        self.exchange_timestamp = exchange_timestamp
        self.status = "DESYNC" if self.bids and self.asks and max(self.bids) >= min(self.asks) else "VALID"

    @staticmethod
    def _levels(levels: list) -> dict[Decimal, Decimal]:
        result = {}
        for level in levels:
            price, size = decimal(level["price"]), decimal(level["size"])
            if not 0 <= price <= 1 or size < 0 or price in result:
                raise ValueError("Invalid or duplicate L2 level")
            if size:
                result[price] = size
        return result

    def age_ms(self, mono_ns: int) -> Decimal:
        return Decimal(mono_ns - self.received_monotonic_ns) / Decimal(1_000_000)

    def current_status(self, mono_ns: int, max_age_ms: int) -> str:
        if self.status != "VALID":
            return self.status
        age = self.age_ms(mono_ns)
        if self.connection_deadline_ns and mono_ns >= self.connection_deadline_ns:
            return "STALE"
        ttl = self.freshness_ttl_ms if self.freshness_ttl_ms is not None else max_age_ms
        return "DESYNC" if age < 0 else "STALE" if age >= ttl else "VALID"


def vwap(asks: dict[Decimal, Decimal], quantity: Decimal) -> Decimal | None:
    quantity = decimal(quantity)
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    remaining, cost = quantity, Decimal(0)
    for price, size in sorted(asks.items()):
        price, size = decimal(price), decimal(size)
        if not 0 <= price <= 1 or size < 0:
            raise ValueError("Invalid L2 level")
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining == 0:
            return cost / quantity
    return None
