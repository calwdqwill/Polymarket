"""Offline raw adapters plus query-time snapshots. No network or source writes."""

import gzip
import heapq
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import decode
from app.prediction.storage import dumps
from app.scripts.analyze_prediction import records
from app.scripts.verify_prediction_observations import market_from

LATENCIES = [(n, n) for n in (25, 50, 100, 250, 500, 1000)] + [(50, 100), (100, 50), (100, 250), (250, 100)]


def candidates(bundle, windows):
    """Episodes index causal crossings; never use maxima or right-censoring to enter."""
    result = defaultdict(list)
    with gzip.open(bundle / "opportunities.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if (
                row["window"] not in windows
                or row["quantity"] not in ("10", "25", "50", "100")
                or D(row["threshold"]) == 0
                or row["left_censored"]
            ):
                continue
            result[row["window"]].append(row)
    return result


def query_times(episodes):
    offsets = sorted(
        {
            0,
            *[n for pair in LATENCIES for n in pair],
            *[2 * n for pair in LATENCIES for n in pair],
            *[2 * a + b for a, b in LATENCIES],
            *[a + 2 * b for a, b in LATENCIES],
        }
    )
    result = set()
    for e in episodes:
        starts = [e["start_ns"]]
        # Cooldown sensitivity is predeclared for Q25, 2c, 100/100ms.
        if e["quantity"] == "25" and D(e["threshold"]) == D(".02"):
            for cd in (250, 500, 1000, 5000):
                starts.extend(range(e["start_ns"] + cd * 1_000_000, e["end_ns"], cd * 1_000_000))
        for start in starts:
            result.update(start + offset * 1_000_000 for offset in offsets)
    return sorted(result)


class RawTimeline:
    def __init__(self):
        self.markets, self.pairs, self.adapters, self.selected = {}, defaultdict(dict), {}, {}
        self.heartbeats, self.subscribed = {}, {}
        self.ordinal = 0
        self.now_ns = 0
        self.utc = None
        self.session = None

    @staticmethod
    def books(adapter):
        return list(adapter.books.values()) if isinstance(adapter, PolymarketBooks) else [adapter.yes, adapter.no]

    def apply(self, stream, r):
        p, conn, mono = r["payload"], r["connection"], r["monotonic_received_ns"]
        if mono < self.now_ns:
            raise ValueError("Regressing monotonic transport clock")
        if self.session is not None and self.session != r["session"]:
            raise ValueError("Multiple clock sessions require explicit alignment")
        self.session, self.ordinal, self.now_ns = r["session"], r["local_ordinal"], mono
        self.utc = datetime.fromisoformat(r["received_timestamp"])
        if stream == "markets":
            market = market_from(p)
            self.markets[market.slug] = market
            self.pairs[int(market.start.timestamp())][market.venue] = market
            return
        if stream == "connections":
            market = self.markets.get(p["market"])
            if market is None:
                return
            if p["event"] == "CONNECTED":
                self.adapters[conn] = (
                    PolymarketBooks(market) if market.venue == "Polymarket" else LimitlessBooks(market)
                )
                self.selected[market.venue, market.market_id] = conn
                self.heartbeats[conn] = 30_000_000_000
                self.subscribed[conn] = mono
                for book in self.books(self.adapters[conn]):
                    book.status = "RECOVERING"
                    book.freshness_ttl_ms = 30000 if market.venue == "Polymarket" else 60000
            elif p["event"] == "INVALID" and conn in self.adapters:
                self.adapters[conn].disconnect()
            return
        if stream == "transport_sent":
            if "subscribe_market_prices" in p["frame"]:
                self.subscribed[conn] = mono
                if conn in self.adapters:
                    for book in self.books(self.adapters[conn]):
                        book.status = "RECOVERING"
            return
        market = self.markets[p["market"]]
        if conn not in self.adapters:
            raise ValueError("Raw frame before CONNECTED")
        adapter = self.adapters[conn]
        for book in self.books(adapter):
            book.connection_deadline_ns = mono + self.heartbeats[conn]
        frame, messages = p["frame"], []
        if market.venue == "Polymarket" and frame != "PONG":
            parsed = decode(frame)
            messages = parsed if isinstance(parsed, list) else [parsed]
        elif market.venue == "Limitless":
            if frame.startswith("0"):
                handshake = decode(frame[1:])
                self.heartbeats[conn] = (int(handshake["pingInterval"]) + int(handshake["pingTimeout"])) * 1_000_000
            elif frame.startswith("42/markets,"):
                event = decode(frame.split(",", 1)[1])
                if event[0] == "orderbookUpdate":
                    messages = [event[1]]
                elif event[0] == "exception":
                    adapter.disconnect()
            elif frame.startswith(("41", "44", "1")):
                adapter.disconnect()
        for message in messages:
            if (
                isinstance(adapter, LimitlessBooks)
                and adapter.version is not None
                and int(message["version"]) < adapter.version
            ):
                if mono - self.subscribed[conn] >= 15_000_000_000:
                    adapter.disconnect()
                continue
            adapter.apply(message, self.utc, mono)

    def snapshot(self, window, ns, intern):
        from app.prediction.models import match_markets

        pair = self.pairs.get(window, {})
        quality = match_markets(pair["Polymarket"], pair["Limitless"]).quality if len(pair) == 2 else "UNKNOWN"
        result = {"ordinal": self.ordinal, "source_ns": self.now_ns, "quality": quality, "books": {}}
        for venue, market in pair.items():
            conn = self.selected.get((venue, market.market_id))
            adapter = self.adapters.get(conn)
            if adapter is None:
                continue
            for book in self.books(adapter):
                ladder = (tuple(sorted(book.asks.items())), tuple(sorted(book.bids.items(), reverse=True)))
                result["books"][venue + ":" + book.outcome] = {
                    "ladder": intern(ladder),
                    "status": book.current_status(ns, 2000),
                    "book_ns": book.received_monotonic_ns,
                    "connection": conn,
                    "min_order": str(market.min_order_size) if market.min_order_size is not None else None,
                }
        return result


def prepare(bundle, output, episodes):
    streams = ("markets", "connections", "transport_sent", "raw_ws_polymarket", "raw_ws_limitless")

    def tagged(stream):
        for r in records(bundle / "dataset", stream):
            yield r["local_ordinal"], stream, r

    merged = iter(heapq.merge(*(tagged(s) for s in streams)))
    upcoming = next(merged, None)
    timeline = RawTimeline()
    count = 0
    output.mkdir(parents=True, exist_ok=True)
    queries = []
    state = {}
    remaining = {}
    for window, entries in episodes.items():
        times = query_times(entries)
        queries.extend((ns, window) for ns in times)
        remaining[window] = len(times)
    for ns, window in sorted(queries):
        if window not in state:
            state[window] = ([], {}, {})
        levels, level_ids, snapshots = state[window]

        def intern(ladder):
            if ladder not in level_ids:
                level_ids[ladder] = len(levels)
                levels.append(ladder)
            return level_ids[ladder]

        # Apply only records available by this exact query, never the first later snapshot.
        while upcoming is not None and upcoming[2]["monotonic_received_ns"] <= ns:
            _, stream, record = upcoming
            timeline.apply(stream, record)
            count += 1
            upcoming = next(merged, None)
        snapshots[ns] = timeline.snapshot(window, ns, intern)
        remaining[window] -= 1
        if remaining[window] == 0:
            with gzip.open(output / f"{window}.json.gz", "wt", encoding="utf-8", compresslevel=1) as handle:
                handle.write(dumps({"levels": levels, "snapshots": snapshots, "episodes": episodes[window]}))
            print(json.dumps({"prepared_window": window, "records": count, "queries": len(snapshots)}), flush=True)
            del state[window]
    return count
