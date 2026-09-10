import argparse
import json
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import atomic_json, decode
from app.prediction.models import Market
from app.prediction.storage import dumps
from app.scripts.analyze_prediction import records


def replay(path):
    run = json.loads((path / "run.json").read_text())
    if run.get("schema_version") == 2:
        return replay_checkpoints(path)
    markets = {}
    for record in records(path, "markets"):
        payload = record["payload"]
        payload["start"] = datetime.fromisoformat(payload["start"])
        payload["end"] = datetime.fromisoformat(payload["end"])
        for field in ("min_order_size", "tick_size", "size_scale"):
            if payload.get(field) is not None:
                payload[field] = Decimal(payload[field])
        markets[payload["slug"]] = Market(**payload)
    results = {}
    for venue in ("polymarket", "limitless"):
        adapters = {}
        normalized = iter(records(path, "books_" + venue))
        expected_count, mismatches, decode_errors = 0, [], 0
        for record in records(path, "raw_ws_" + venue):
            frame = record["payload"]["frame"]
            connection = record["connection"]
            if connection not in adapters:
                market = markets[record["payload"]["market"]]
                adapters[connection] = (
                    PolymarketBooks(market, legacy=True) if venue == "polymarket" else LimitlessBooks(market)
                )
            adapter = adapters[connection]
            messages = []
            try:
                if venue == "polymarket" and frame != "PONG":
                    parsed = decode(frame)
                    messages = parsed if isinstance(parsed, list) else [parsed]
                elif venue == "limitless" and frame.startswith("42/markets,"):
                    event = decode(frame[len("42/markets,") :])
                    if event[0] == "orderbookUpdate":
                        messages = [event[1]]
                updates = []
                for message in messages:
                    updates.extend(
                        adapter.apply(
                            message,
                            datetime.fromisoformat(record["received_timestamp"]),
                            record["monotonic_received_ns"],
                        )
                    )
            except (KeyError, TypeError, ValueError):
                adapter.disconnect()
                decode_errors += 1
                continue
            for book in updates:
                if venue == "limitless" and book.outcome == "NO":
                    continue
                expected_count += 1
                stored = next(normalized, None)
                expected = json.loads(dumps(book))
                for field in ("connection_deadline_ns", "freshness_ttl_ms"):
                    expected.pop(field, None)
                if (
                    stored is None
                    or stored["payload"] != expected
                    or stored["connection"] != connection
                    or stored["monotonic_received_ns"] != record["monotonic_received_ns"]
                ):
                    if len(mismatches) < 10:
                        mismatches.append(record["local_ordinal"])
        extra = sum(1 for _ in normalized)
        results[venue] = dict(
            normalized_events=expected_count,
            mismatch_examples=mismatches,
            extra_stored=extra,
            decode_errors=decode_errors,
            consistent=not mismatches and extra == 0 and decode_errors == 0,
        )
    atomic_json(path / "replay.json", results)
    return results


def replay_checkpoints(path):
    markets = {}
    for record in records(path, "markets"):
        payload = record["payload"]
        for field in ("start", "end"):
            payload[field] = datetime.fromisoformat(payload[field])
        for field in ("min_order_size", "tick_size", "size_scale"):
            if payload.get(field) is not None:
                payload[field] = Decimal(payload[field])
        markets[payload["slug"]] = Market(**payload)
    results = {}
    for venue in ("polymarket", "limitless"):
        adapters, last_ordinals = {}, {}
        sent = iter(records(path, "transport_sent"))
        outbound = next(sent, None)
        checkpoints = iter(records(path, "checkpoints_" + venue))
        checkpoint = next(checkpoints, None)
        checked, raw_count, updates_count, errors = 0, 0, 0, []
        history = defaultdict(lambda: deque(maxlen=20))
        first_divergence = None

        def compare(record):
            nonlocal checked, first_divergence
            checked += 1
            adapter = adapters.get(record["connection"])
            expected = record["payload"]["book"].copy()
            if adapter is None:
                errors.append(dict(kind="missing_connection", ordinal=record["local_ordinal"]))
                return
            books = list(adapter.books.values()) if venue == "polymarket" else [adapter.yes]
            book = next(b for b in books if b.outcome == expected["outcome"])
            actual = json.loads(dumps(book))
            # Transport liveness is replayed by the separate observation timeline.
            for key in ("connection_deadline_ns", "freshness_ttl_ms"):
                expected.pop(key, None)
                actual.pop(key, None)
            if expected != actual:
                if first_divergence is None:
                    first_divergence = dict(
                        venue=venue,
                        market=expected.get("market_id"),
                        checkpoint_ordinal=record["local_ordinal"],
                        raw_ordinal=record["payload"].get("raw_ordinal"),
                        last_raw_ordinal=last_ordinals.get(record["connection"]),
                        expected=expected,
                        reconstructed=actual,
                        previous_frames=list(history[record["connection"]]),
                    )
                errors.append(
                    dict(
                        kind="book_mismatch",
                        ordinal=record["local_ordinal"],
                        differing_fields=[k for k in expected if expected[k] != actual.get(k)],
                    )
                )

        for record in records(path, "raw_ws_" + venue):
            while checkpoint is not None and checkpoint["local_ordinal"] < record["local_ordinal"]:
                compare(checkpoint)
                checkpoint = next(checkpoints, None)
            while outbound is not None and outbound["local_ordinal"] < record["local_ordinal"]:
                previous_adapter = adapters.get(outbound["connection"])
                if (
                    venue == "limitless"
                    and previous_adapter is not None
                    and "subscribe_market_prices" in outbound["payload"]["frame"]
                ):
                    previous_adapter.yes.status = previous_adapter.no.status = "RECOVERING"
                outbound = next(sent, None)
            raw_count += 1
            connection = record["connection"]
            market = markets[record["payload"]["market"]]
            if connection not in adapters:
                adapters[connection] = PolymarketBooks(market) if venue == "polymarket" else LimitlessBooks(market)
            adapter = adapters[connection]
            frame = record["payload"]["frame"]
            messages = []
            try:
                if venue == "polymarket" and frame != "PONG":
                    data = decode(frame)
                    messages = data if isinstance(data, list) else [data]
                elif venue == "limitless" and frame.startswith("42/markets,"):
                    data = decode(frame.split(",", 1)[1])
                    if data[0] == "orderbookUpdate":
                        messages = [data[1]]
                for message in messages:
                    updates_count += len(
                        adapter.apply(
                            message,
                            datetime.fromisoformat(record["received_timestamp"]),
                            record["monotonic_received_ns"],
                        )
                    )
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(dict(kind="decode", ordinal=record["local_ordinal"], detail=str(exc)))
            last_ordinals[connection] = record["local_ordinal"]
            history[connection].append(record)
        while checkpoint is not None:
            compare(checkpoint)
            checkpoint = next(checkpoints, None)
        results[venue] = dict(
            raw_frames=raw_count,
            reconstructed_updates=updates_count,
            checkpoints_checked=checked,
            mismatch_count=len(errors),
            mismatch_examples=errors[:10],
            first_divergence=first_divergence,
            consistent=not errors and checked > 0,
            scope="Every raw frame reconstructed; exact full book/status/timestamp equality at checkpoints",
        )
    atomic_json(path / "replay.json", results)
    return results


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Replay stored public raw WS against normalized books; no network")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = replay(args.directory)
    print(json.dumps(result))
    if not all(v["consistent"] for v in result.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
