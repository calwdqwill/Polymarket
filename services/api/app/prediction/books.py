from decimal import Decimal

from app.prediction.models import Book, Market, decimal


class PolymarketBooks:
    """Protocol reconstruction only. A new instance is required after reconnect."""

    def __init__(self, market: Market, legacy=False):
        self.legacy = legacy
        self.books = {
            token: Book(market.venue, market.market_id, market.canonical_market_id, outcome)
            for outcome, token in market.tokens.items()
        }
        self.last_timestamp: dict[str, int] = {}
        self.diagnostics = []

    def disconnect(self):
        for book in self.books.values():
            book.status = "DESYNC"

    def apply(self, message: dict, received, mono_ns: int) -> list[Book]:
        self.diagnostics = []
        kind = message.get("event_type")
        if kind == "market_resolved":
            self.disconnect()
            return list(self.books.values())
        if kind not in ("book", "price_change"):
            return []
        changes = [message] if kind == "book" else message.get("price_changes", [])
        changed = []
        advertised = {}
        try:
            stamp = int(message["timestamp"])
            for change in changes:
                token = change["asset_id"]
                if token not in self.books:
                    continue
                book = self.books[token]
                if stamp < self.last_timestamp.get(token, 0):
                    # An older absolute delta already present in the snapshot is a no-op.
                    # Do not roll back the timestamp or infer ordering from milliseconds.
                    if not self.legacy and kind == "price_change" and change.get("side") in ("BUY", "SELL"):
                        levels = book.bids if change["side"] == "BUY" else book.asks
                        if levels.get(decimal(change["price"]), Decimal(0)) == decimal(change["size"]):
                            self.diagnostics.append(dict(reason="OLDER_IDEMPOTENT_DELTA", token=token, timestamp=stamp))
                            continue
                    book.status = "DESYNC"
                    self.diagnostics.append(
                        dict(
                            reason="TIMESTAMP_REGRESSION",
                            token=token,
                            timestamp=stamp,
                            previous_timestamp=self.last_timestamp[token],
                        )
                    )
                    changed.append(book)
                    continue
                if kind == "book":
                    book.snapshot(change["bids"], change["asks"], received, mono_ns, str(stamp))
                else:
                    if book.status != "VALID":
                        continue
                    side = change["side"]
                    if side not in ("BUY", "SELL"):
                        raise ValueError("Unknown book side")
                    price, size = decimal(change["price"]), decimal(change["size"])
                    if not 0 <= price <= 1 or size < 0:
                        raise ValueError("Invalid delta")
                    levels = book.bids if side == "BUY" else book.asks
                    if size:
                        levels[price] = size
                    else:
                        levels.pop(price, None)
                    book.received_timestamp, book.received_monotonic_ns = received, mono_ns
                    book.exchange_timestamp = str(stamp)
                    if "best_bid" in change and "best_ask" in change:
                        advertised[token] = (decimal(change["best_bid"]), decimal(change["best_ask"]))
                self.last_timestamp[token] = stamp
                if book not in changed:
                    changed.append(book)
            # A price_change batch is atomic; do not evaluate transient crossed states mid-batch.
            for book in changed:
                if book.bids and book.asks and max(book.bids) >= min(book.asks):
                    book.status = "DESYNC"
            for token, (bid, ask) in advertised.items():
                book = self.books[token]
                if max(book.bids, default=Decimal(0)) != bid or min(book.asks, default=Decimal(1)) != ask:
                    book.status = "DESYNC"
                    self.diagnostics.append(
                        dict(
                            reason="BBO_MISMATCH",
                            token=token,
                            advertised=[bid, ask],
                            calculated=[max(book.bids, default=Decimal(0)), min(book.asks, default=Decimal(1))],
                            timestamp=stamp,
                        )
                    )
            return changed
        except (KeyError, TypeError, ValueError):
            self.disconnect()
            raise


class LimitlessBooks:
    """Coalesced full YES book; mirrored NO liquidity is not an independent pool."""

    def __init__(self, market: Market):
        self.slug = market.slug
        self.size_scale = market.size_scale
        self.token_id = market.tokens["YES"]
        self.yes = Book(market.venue, market.market_id, market.canonical_market_id, "YES")
        self.no = Book(market.venue, market.market_id, market.canonical_market_id, "NO")
        self.version: int | None = None

    def disconnect(self):
        self.yes.status = self.no.status = "DESYNC"

    def apply(self, message: dict, received, mono_ns: int) -> list[Book]:
        if message.get("marketSlug") != self.slug:
            return []
        try:
            version = int(message["version"])
            if self.version is not None and (
                version < self.version or (version == self.version and self.yes.status == "VALID")
            ):
                return []
            book = message["orderbook"]
            if book.get("tokenId", self.token_id) != self.token_id:
                raise ValueError("Unexpected Limitless YES token")
            normalized = {
                side: [
                    {"price": level["price"], "size": decimal(level["size"]) / self.size_scale} for level in book[side]
                ]
                for side in ("bids", "asks")
            }
            self.yes.snapshot(normalized["bids"], normalized["asks"], received, mono_ns, message.get("timestamp"))
            bids = [{"price": Decimal(1) - p, "size": s} for p, s in self.yes.asks.items()]
            asks = [{"price": Decimal(1) - p, "size": s} for p, s in self.yes.bids.items()]
            self.no.snapshot(bids, asks, received, mono_ns, message.get("timestamp"))
            self.version = version
            return [self.yes, self.no]
        except (KeyError, TypeError, ValueError):
            self.disconnect()
            raise
