"""Independently verify every priced observation against raw reconstructed depth."""

import argparse
import heapq
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import atomic_json, decode
from app.prediction.models import Market, match_markets, vwap
from app.prediction.observer import SIZES
from app.scripts.analyze_prediction import records


def market_from(payload):
    payload = payload.copy()
    for field in ("start", "end"):
        payload[field] = datetime.fromisoformat(payload[field])
    for field in ("min_order_size", "tick_size", "size_scale"):
        if payload.get(field) is not None:
            payload[field] = D(payload[field])
    return Market(**payload)


def verify(path):
    markets, pairs, adapters, selected = {}, defaultdict(dict), {}, {}
    subscribed, heartbeat, raw_ordinal = {}, {}, {}
    history = defaultdict(lambda: deque(maxlen=20))
    cache, counts, windows, qualities = {}, Counter(), defaultdict(Counter), {}
    unsafe = {}
    streams = (
        "markets",
        "matches",
        "connections",
        "transport_sent",
        "raw_ws_polymarket",
        "raw_ws_limitless",
        "observations",
    )

    def tagged(stream):
        for record in records(path, stream):
            yield record["local_ordinal"], stream, record

    for _, stream, record in heapq.merge(*(tagged(s) for s in streams)):
        p, mono, conn = record["payload"], record["monotonic_received_ns"], record["connection"]
        if stream == "markets":
            market = market_from(p)
            markets[market.slug] = market
            pairs[int(market.start.timestamp())][market.venue] = market
            qualities.pop(int(market.start.timestamp()), None)
        elif stream == "matches":
            pair = next(
                (v for v in pairs.values() if any(m.canonical_market_id == p["window"] for m in v.values())), {}
            )
            if len(pair) == 2:
                actual = asdict(match_markets(pair["Polymarket"], pair["Limitless"]))
                if any(actual[k] != p[k] for k in actual):
                    raise ValueError(f"Match metadata mismatch at ordinal {record['local_ordinal']}")
                counts["matches_checked"] += 1
        elif stream == "connections":
            if p["event"] == "INVALID" and conn in unsafe:
                unsafe[conn].update(
                    invalidated_ordinal=record["local_ordinal"],
                    invalidated_ns=mono,
                    deferred_invalidation_ms=D(mono - unsafe[conn]["raw_ns"]) / 1_000_000,
                )
        elif stream == "transport_sent":
            if "subscribe_market_prices" in p["frame"]:
                subscribed[conn] = mono
                if conn in adapters:
                    adapters[conn].yes.status = adapters[conn].no.status = "RECOVERING"
        elif stream.startswith("raw_ws_"):
            market = markets[p["market"]]
            if conn not in adapters:
                adapters[conn] = PolymarketBooks(market) if market.venue == "Polymarket" else LimitlessBooks(market)
                subscribed.setdefault(conn, mono)
                heartbeat[conn] = 30_000_000_000
            adapter = adapters[conn]
            selected[market.venue, market.market_id] = conn
            books = list(adapter.books.values()) if market.venue == "Polymarket" else [adapter.yes, adapter.no]
            for book in books:
                book.connection_deadline_ns = mono + heartbeat[conn]
                book.freshness_ttl_ms = 30_000 if market.venue == "Polymarket" else 60_000
            frame, messages = p["frame"], []
            if market.venue == "Polymarket" and frame != "PONG":
                parsed = decode(frame)
                messages = parsed if isinstance(parsed, list) else [parsed]
            elif market.venue == "Limitless":
                if frame.startswith("0"):
                    handshake = decode(frame[1:])
                    heartbeat[conn] = (int(handshake["pingInterval"]) + int(handshake["pingTimeout"])) * 1_000_000
                elif frame.startswith("42/markets,"):
                    event = decode(frame.split(",", 1)[1])
                    if event[0] == "orderbookUpdate":
                        messages = [event[1]]
                    elif event[0] == "exception":
                        unsafe.setdefault(
                            conn,
                            dict(
                                window=int(market.start.timestamp()),
                                market=market.slug,
                                raw_ordinal=record["local_ordinal"],
                                raw_ns=mono,
                                reason="Socket.IO exception",
                            ),
                        )
                elif frame.startswith(("41", "44", "1")):
                    unsafe.setdefault(
                        conn,
                        dict(
                            window=int(market.start.timestamp()),
                            market=market.slug,
                            raw_ordinal=record["local_ordinal"],
                            raw_ns=mono,
                            reason="Socket.IO disconnect/rejection",
                        ),
                    )
            for message in messages:
                if (
                    isinstance(adapter, LimitlessBooks)
                    and adapter.version is not None
                    and int(message["version"]) < adapter.version
                ):
                    counts["limitless_older_versions"] += 1
                    if mono - subscribed[conn] >= 15_000_000_000:
                        # async websocket context exit defers collector's disconnect.
                        # Reconstruct the recorded state, but exclude the affected market.
                        unsafe.setdefault(
                            conn,
                            dict(
                                window=int(market.start.timestamp()),
                                market=market.slug,
                                raw_ordinal=record["local_ordinal"],
                                raw_ns=mono,
                                reason="Late Limitless version regression",
                            ),
                        )
                    continue
                adapter.apply(message, datetime.fromisoformat(record["received_timestamp"]), mono)
            raw_ordinal[conn] = record["local_ordinal"]
            history[conn].append(record)
            counts["raw_frames"] += 1
            if counts["raw_frames"] % 500_000 == 0:
                print(json.dumps(dict(counts)), flush=True)
        else:
            window = p["window"]
            pair = pairs[window]
            counts["observations"] += 1
            if len(pair) != 2:
                if any(row[3] is not None for row in p["rows"]):
                    raise ValueError("Priced observation without both markets")
                continue
            if window not in qualities:
                qualities[window] = match_markets(pair["Polymarket"], pair["Limitless"]).quality
            quality = qualities[window]
            if quality != p["quality"]:
                raise ValueError(f"Observation matching quality mismatch at {record['local_ordinal']}")
            prepared = {}
            for direction, quantity, status, edge in p["rows"]:
                if edge is None:
                    counts["unpriced_rows_excluded"] += 1
                    continue
                keys = (
                    (("Limitless", "YES"), ("Polymarket", "NO"))
                    if direction == "A"
                    else (("Polymarket", "YES"), ("Limitless", "NO"))
                )
                prices, problems, context = [], [], []
                for venue, outcome in keys:
                    if (venue, outcome) in prepared:
                        ladder, book_problems, book_context = prepared[venue, outcome]
                        prices.append(ladder[quantity])
                        problems.extend(book_problems)
                        context.append(book_context)
                        continue
                    problem_start = len(problems)
                    market = pair[venue]
                    connection = selected.get((venue, market.market_id))
                    adapter = adapters.get(connection)
                    book = (
                        (
                            adapter.books[market.tokens[outcome]]
                            if venue == "Polymarket"
                            else adapter.yes
                            if outcome == "YES"
                            else adapter.no
                        )
                        if adapter
                        else None
                    )
                    if book is None:
                        problems.append("missing reconstructed book")
                        continue
                    expected_health = p["books"][venue + ":" + outcome]
                    if (
                        book.current_status(mono, 2000) != "VALID"
                        or book.received_monotonic_ns != expected_health["book_received_ns"]
                    ):
                        problems.append("raw book status/timestamp differs from priced observation")
                    cache_key = connection, outcome
                    version = book.received_monotonic_ns
                    if cache_key not in cache or cache[cache_key][0] != version:
                        old = cache.get(cache_key)
                        prices_by_size = (
                            old[1] if old and old[2] == book.asks else {str(q): vwap(book.asks, q) for q in SIZES}
                        )
                        cache[cache_key] = version, prices_by_size, book.asks.copy()
                    prices.append(cache[cache_key][1][quantity])
                    context.append((venue, market.slug, connection, book))
                    prepared[venue, outcome] = cache[cache_key][1], problems[problem_start:], context[-1]
                actual = (
                    1 - (prices[0] + prices[1]) if len(prices) == 2 and all(v is not None for v in prices) else None
                )
                if (
                    actual != D(edge)
                    or quality == "NOT_EQUIVALENT"
                    or status != ("OPPORTUNITY" if D(edge) > 0 else "NO_EDGE")
                ):
                    problems.append("edge/status mismatch")
                if problems:
                    context = [
                        dict(
                            venue=v,
                            market=m,
                            raw_ordinal=raw_ordinal[c],
                            reconstructed_book=b,
                            previous_frames=list(history[c]),
                        )
                        for v, m, c, b in context
                    ]
                    result = dict(
                        consistent=False,
                        first_divergence=dict(
                            observation_ordinal=record["local_ordinal"],
                            window=window,
                            direction=direction,
                            quantity=quantity,
                            expected_edge=edge,
                            reconstructed_edge=actual,
                            problems=problems,
                            context=context,
                        ),
                        counts=counts,
                    )
                    atomic_json(path / "observation-replay.json", result)
                    return result
                counts["priced_rows_checked"] += 1
                windows[window]["priced_rows_checked"] += 1
                for _, _, connection, _ in context:
                    if connection in unsafe and "invalidated_ns" not in unsafe[connection]:
                        unsafe[connection]["priced_rows_during_deferred_invalidation"] = (
                            unsafe[connection].get("priced_rows_during_deferred_invalidation", 0) + 1
                        )
    result = dict(
        consistent=True,
        counts=counts,
        windows=windows,
        protocol_unsafe_intervals=unsafe,
        strategy_excluded_windows=sorted({v["window"] for v in unsafe.values()}),
        scope="Every non-null edge: raw depth, Decimal VWAP, status, book timestamp and market matching. "
        "Null rows excluded; exact transport deadline equality not asserted.",
    )
    atomic_json(path / "observation-replay.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    result = verify(parser.parse_args().directory)
    print(json.dumps({k: v for k, v in result.items() if k not in ("windows", "first_divergence")}, default=str))
    if not result["consistent"]:
        raise SystemExit(1)
