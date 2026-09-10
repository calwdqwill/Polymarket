from datetime import datetime
from decimal import Decimal

from app.prediction.models import Book, Market, match_markets, vwap

SIZES = tuple(Decimal(q) for q in (10, 25, 50, 100, 250, 500, 1000))


def lifetime_bucket(duration_ms: Decimal) -> str:
    for upper, label in (
        (25, "<25 ms"),
        (50, "25-50 ms"),
        (100, "50-100 ms"),
        (250, "100-250 ms"),
        (500, "250-500 ms"),
        (1000, "0.5-1 sec"),
        (5000, "1-5 sec"),
    ):
        if duration_ms < upper:
            return label
    return ">=5 sec"


class Observer:
    """Call on each atomic book update AND on a timer to expire stale books."""

    def __init__(self, max_age_ms: int = 2000):
        if max_age_ms <= 0:
            raise ValueError("max_age_ms must be positive")
        self.max_age_ms = max_age_ms
        self.open_events: dict[tuple, dict] = {}

    def evaluate(
        self, a: Market, b: Market, books: dict, now: datetime, mono_ns: int, observe: bool = True
    ) -> tuple[list, list]:
        match = match_markets(a, b)
        rows, events = [], []
        for yes_market, no_market in ((a, b), (b, a)):
            yes = books.get((yes_market.venue, yes_market.market_id, "YES"))
            no = books.get((no_market.venue, no_market.market_id, "NO"))
            for quantity in SIZES:
                key = (a.canonical_market_id, yes_market.venue, no_market.venue, str(quantity))
                row = {
                    "canonical_market_id": a.canonical_market_id,
                    "venue_yes": yes_market.venue,
                    "venue_no": no_market.venue,
                    "share_size": quantity,
                    "matching_quality": match.quality,
                    "time_to_expiry": Decimal(str((a.end - now).total_seconds())),
                    "yes_vwap": None,
                    "no_vwap": None,
                    "combined_cost": None,
                    "observed_edge": None,
                    "gross_pnl": None,
                    "book_age_A": yes.age_ms(mono_ns) if yes else None,
                    "book_age_B": no.age_ms(mono_ns) if no else None,
                    "book_age_poly": (yes if yes_market.venue == "Polymarket" else no).age_ms(mono_ns)
                    if yes and no
                    else None,
                    "book_age_limitless": (yes if yes_market.venue == "Limitless" else no).age_ms(mono_ns)
                    if yes and no
                    else None,
                    "classification": "observed cross-venue discrepancy",
                    "research_only": match.quality != "EXACT",
                    "execution_scope": "displayed_depth_only; fees and simultaneous fills not verified",
                    "minimum_verified": all(m.min_order_size is not None for m in (yes_market, no_market)),
                    "yes_best_ask": min(yes.asks, default=None) if yes else None,
                    "no_best_ask": min(no.asks, default=None) if no else None,
                    "yes_depth": sum(yes.asks.values(), Decimal(0)) if yes else None,
                    "no_depth": sum(no.asks.values(), Decimal(0)) if no else None,
                }
                status = self._status(match.quality, yes_market, no_market, yes, no, now, mono_ns, quantity)
                if status == "VALID":
                    y, n = vwap(yes.asks, quantity), vwap(no.asks, quantity)
                    row.update(yes_vwap=y, no_vwap=n)
                    if y is None or n is None:
                        status = "NOT_EXECUTABLE"
                    else:
                        cost = y + n
                        row.update(
                            yes_vwap=y,
                            no_vwap=n,
                            combined_cost=cost,
                            observed_edge=1 - cost,
                            gross_pnl=quantity * (1 - cost),
                        )
                        status = "OPPORTUNITY" if cost < 1 else "NO_EDGE"
                row["status"] = status
                row["executable"] = status in ("OPPORTUNITY", "NO_EDGE")
                row["limiting_venue"] = (
                    (
                        yes_market.venue
                        if row["yes_depth"] < row["no_depth"]
                        else no_market.venue
                        if row["no_depth"] < row["yes_depth"]
                        else "BOTH"
                    )
                    if yes and no
                    else None
                )
                rows.append(row)
                if status == "OPPORTUNITY":
                    if not observe:
                        continue
                    old = self.open_events.get(key)
                    start_ns = old["start_monotonic_ns"] if old else mono_ns
                    duration = Decimal(mono_ns - start_ns) / Decimal(1_000_000)
                    event = dict(
                        row,
                        event_type="UPDATE" if old else "OPEN",
                        timestamp_start=old["timestamp_start"] if old else now,
                        timestamp_last_seen=now,
                        start_monotonic_ns=start_ns,
                        duration_ms=duration,
                        lifetime_bucket=lifetime_bucket(duration),
                        clock="local_monotonic",
                        censored=False,
                        max_edge=max(row["observed_edge"], old["max_edge"]) if old else row["observed_edge"],
                        current_edge=row["observed_edge"],
                    )
                    self.open_events[key] = event
                    events.append(event.copy())
                elif key in self.open_events:
                    events.append(self._close(key, now, status))
        for row in rows:
            direction_rows = [r for r in rows if r["venue_yes"] == row["venue_yes"]]
            row["max_executable_size"] = max(
                (r["share_size"] for r in direction_rows if r["executable"]), default=Decimal(0)
            )
        for event in events:
            if event["event_type"] != "CLOSE":
                direction_rows = [r for r in rows if r["venue_yes"] == event["venue_yes"]]
                event["max_executable_size"] = direction_rows[0]["max_executable_size"]
                event["pnl_by_size"] = {str(r["share_size"]): r["gross_pnl"] for r in direction_rows}
                key = (a.canonical_market_id, event["venue_yes"], event["venue_no"], str(event["share_size"]))
                self.open_events[key].update(
                    max_executable_size=event["max_executable_size"], pnl_by_size=event["pnl_by_size"]
                )
        return rows, events

    def close_all(self, now: datetime, reason: str = "STOP") -> list:
        return [self._close(key, now, reason) for key in list(self.open_events)]

    def _close(self, key: tuple, now: datetime, reason: str) -> dict:
        event = self.open_events.pop(key)
        return dict(event, event_type="CLOSE", timestamp_closed=now, close_reason=reason, censored=reason != "NO_EDGE")

    def _status(self, quality, a, b, yes: Book | None, no: Book | None, now, mono_ns, quantity):
        if quality not in ("EXACT", "PROVISIONAL", "UNKNOWN"):
            return "BLOCKED_MATCH"
        if not a.start <= now < a.end or not b.start <= now < b.end or not a.active or not b.active:
            return "INACTIVE"
        for book, market, outcome in ((yes, a, "YES"), (no, b, "NO")):
            if book is None:
                return "MISSING_BOOK"
            if (book.venue, book.market_id, book.canonical_market_id, book.outcome) != (
                market.venue,
                market.market_id,
                market.canonical_market_id,
                outcome,
            ):
                return "DESYNC"
            status = book.current_status(mono_ns, self.max_age_ms)
            if status != "VALID":
                return status
            if market.min_order_size is not None and quantity < market.min_order_size:
                return "BELOW_MIN_ORDER_SIZE"
        return "VALID"
