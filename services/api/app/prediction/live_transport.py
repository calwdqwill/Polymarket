import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from uuid import uuid4

from websockets.asyncio.client import connect

from app.prediction.books import LimitlessBooks, PolymarketBooks
from app.prediction.live_storage import decode
from app.prediction.transport_queue import MonitoredConnection, ReceiveQueue

URLS = {
    "Polymarket": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "Limitless": "wss://ws.limitless.exchange/socket.io/?EIO=4&transport=websocket",
}


def adapter_books(adapter):
    return list(adapter.books.values()) if isinstance(adapter, PolymarketBooks) else [adapter.yes, adapter.no]


@asynccontextmanager
async def invalidating_connection(connection, invalidate):
    async with connection as ws:
        try:
            yield ws
        finally:
            invalidate()


async def stream_market(market, journal, changed, register, checkpoint_seconds=10, recovery_seconds=1):
    failures = 0
    while True:
        adapter = PolymarketBooks(market) if market.venue == "Polymarket" else LimitlessBooks(market)
        register(adapter_books(adapter))
        connection = uuid4().hex
        history = deque(maxlen=20)
        last_valid = {}
        recovering_since = {}
        checkpoint_at = {}
        heartbeat_seconds = 30
        last_frame = time.perf_counter_ns()
        subscribed_at = time.perf_counter_ns()

        async def send(frame):
            journal.append(
                "transport_sent", dict(venue=market.venue, market=market.slug, frame=frame), connection=connection
            )
            await ws.send(frame)

        def checkpoint(book, now, mono, reason, raw_ordinal=None):
            journal.append(
                "checkpoints_" + market.venue.lower(),
                dict(book=book, reason=reason, raw_ordinal=raw_ordinal),
                now,
                mono,
                connection,
            )
            checkpoint_at[book.outcome] = mono

        queue = ReceiveQueue()
        journal.transport_queues[connection] = queue
        invalidated = False

        def invalidate():
            nonlocal invalidated
            if invalidated:
                return
            invalidated = True
            for book in adapter_books(adapter):
                if book.received_timestamp and (market.venue != "Limitless" or book.outcome == "YES"):
                    checkpoint(book, datetime.now(timezone.utc), time.perf_counter_ns(), "CONNECTION_END")
            adapter.disconnect()
            for book in adapter_books(adapter):
                book.connection_deadline_ns = 0
            changed(datetime.now(timezone.utc), time.perf_counter_ns(), market, False)
            journal.append(
                "connections", dict(venue=market.venue, market=market.slug, event="INVALID"), connection=connection
            )

        try:
            async with invalidating_connection(
                connect(
                    URLS[market.venue],
                    open_timeout=15,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                    max_queue=16,
                    close_timeout=5,
                    create_connection=partial(
                        MonitoredConnection,
                        queue=queue,
                        invalidate=invalidate,
                        overflow=lambda value: journal.append("queue_overflow", value, connection=connection),
                    ),
                ),
                invalidate,
            ) as ws:
                journal.append(
                    "connections",
                    dict(venue=market.venue, market=market.slug, event="CONNECTED"),
                    connection=connection,
                )
                if market.venue == "Polymarket":
                    await send(json.dumps(dict(assets_ids=list(market.tokens.values()), type="market")))
                for book in adapter_books(adapter):
                    book.status = "RECOVERING"
                    book.connection_deadline_ns = time.perf_counter_ns() + int(heartbeat_seconds * 1e9)
                    book.freshness_ttl_ms = 60_000 if market.venue == "Limitless" else 30_000
                last_ping = time.monotonic()
                snapshot_deadline = time.monotonic() + 15
                while True:
                    tick = time.perf_counter_ns()
                    if tick - last_frame > heartbeat_seconds * 1e9:
                        raise ValueError("Application heartbeat deadline exceeded")
                    if any(tick - since >= recovery_seconds * 1e9 for since in recovering_since.values()):
                        raise ValueError("DESYNC snapshot recovery deadline exceeded")
                    if time.monotonic() > snapshot_deadline and any(
                        b.status == "RECOVERING" for b in adapter_books(adapter)
                    ):
                        raise ValueError("Initial snapshot missing")
                    if (
                        market.venue == "Limitless"
                        and tick - max(subscribed_at, adapter.yes.received_monotonic_ns) > 30_000_000_000
                    ):
                        # Documented re-subscribe returns a full snapshot. Invalidate during its gap.
                        adapter.yes.status = adapter.no.status = "RECOVERING"
                        changed(datetime.now(timezone.utc), tick, market, False)
                        await send(
                            "42/markets," + json.dumps(["subscribe_market_prices", {"marketSlugs": [market.slug]}])
                        )
                        subscribed_at = tick
                        snapshot_deadline = time.monotonic() + 15
                    if market.venue == "Polymarket" and time.monotonic() - last_ping >= 10:
                        await send("PING")
                        last_ping = time.monotonic()
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=1)
                    except TimeoutError:
                        continue
                    now, mono = datetime.now(timezone.utc), time.perf_counter_ns()
                    if not isinstance(frame, str):
                        raise ValueError("Unexpected binary public frame")
                    raw_ordinal = journal.append(
                        "raw_ws_" + market.venue.lower(), dict(frame=frame, market=market.slug), now, mono, connection
                    )
                    last_frame = mono
                    for book in adapter_books(adapter):
                        book.connection_deadline_ns = mono + int(heartbeat_seconds * 1e9)
                    messages = []
                    if market.venue == "Polymarket":
                        if frame != "PONG":
                            payload = decode(frame)
                            messages = payload if isinstance(payload, list) else [payload]
                    elif frame.startswith("0"):
                        handshake = decode(frame[1:])
                        heartbeat_seconds = (int(handshake["pingInterval"]) + int(handshake["pingTimeout"])) / 1000
                        await send("40/markets,")
                    elif frame.startswith("2"):
                        await send("3" + frame[1:])
                    elif frame.startswith("40/markets,"):
                        await send(
                            "42/markets," + json.dumps(["subscribe_market_prices", {"marketSlugs": [market.slug]}])
                        )
                    elif frame.startswith("42/markets,"):
                        event = decode(frame[len("42/markets,") :])
                        if event[0] == "orderbookUpdate":
                            messages = [event[1]]
                        elif event[0] == "exception":
                            raise ValueError(str(event[1]))
                    elif frame.startswith(("41", "44", "1")):
                        raise ValueError("Socket.IO disconnected or rejected namespace: " + frame)
                    updates = []
                    for message in messages:
                        if (
                            isinstance(adapter, LimitlessBooks)
                            and adapter.version is not None
                            and int(message["version"]) < adapter.version
                        ):
                            if mono - subscribed_at < 15_000_000_000:
                                journal.append(
                                    "protocol_diagnostics",
                                    dict(
                                        market=market.slug,
                                        reason="LIMITLESS_SUBSCRIPTION_OLDER_VERSION",
                                        raw_ordinal=raw_ordinal,
                                    ),
                                    now,
                                    mono,
                                    connection,
                                )
                                continue
                            raise ValueError("Limitless version regression; fresh session required")
                        updates.extend(adapter.apply(message, now, mono))
                        if isinstance(adapter, PolymarketBooks) and adapter.diagnostics:
                            journal.append(
                                "protocol_diagnostics",
                                dict(market=market.slug, raw_ordinal=raw_ordinal, diagnostics=adapter.diagnostics),
                                now,
                                mono,
                                connection,
                            )
                    unique_updates = {b.outcome: b for b in updates}.values()
                    for book in unique_updates:
                        if book.status == "DESYNC":
                            if book.outcome not in recovering_since:
                                journal.append(
                                    "desync_diagnostics",
                                    dict(
                                        market=market.slug,
                                        token=market.tokens[book.outcome],
                                        last_valid_bbo=last_valid.get(book.outcome),
                                        raw_frame=frame,
                                        calculated_book=book,
                                        advertised=getattr(adapter, "diagnostics", []),
                                        previous_events=list(history),
                                        raw_ordinal=raw_ordinal,
                                    ),
                                    now,
                                    mono,
                                    connection,
                                )
                                recovering_since[book.outcome] = mono
                        elif book.status == "VALID":
                            recovered = book.outcome in recovering_since
                            if recovered:
                                journal.append(
                                    "recoveries",
                                    dict(
                                        market=market.slug,
                                        outcome=book.outcome,
                                        duration_ms=(mono - recovering_since.pop(book.outcome)) / 1e6,
                                        raw_ordinal=raw_ordinal,
                                    ),
                                    now,
                                    mono,
                                    connection,
                                )
                            last_valid[book.outcome] = dict(
                                bid=max(book.bids, default=None),
                                ask=min(book.asks, default=None),
                                timestamp=now,
                                monotonic_ns=mono,
                                exchange_timestamp=book.exchange_timestamp,
                            )
                            if market.venue != "Limitless" or book.outcome == "YES":
                                if recovered or mono - checkpoint_at.get(book.outcome, 0) >= checkpoint_seconds * 1e9:
                                    checkpoint(
                                        book,
                                        now,
                                        mono,
                                        "RECOVERED"
                                        if recovered
                                        else "INITIAL"
                                        if book.outcome not in checkpoint_at
                                        else "PERIODIC",
                                        raw_ordinal,
                                    )
                    history.append(
                        dict(raw_ordinal=raw_ordinal, received_timestamp=now, monotonic_ns=mono, frame=frame)
                    )
                    if updates:
                        failures = 0
                        changed(now, mono, market)
                    elif frame == "PONG" or (market.venue == "Limitless" and frame.startswith("2")):
                        changed(now, mono, market, False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            journal.append(
                "errors",
                dict(venue=market.venue, market=market.slug, error=type(exc).__name__, detail=str(exc)),
                connection=connection,
            )
        finally:
            invalidate()
            journal.queue_closed(connection)
        await asyncio.sleep(min(15, 2 ** min(failures, 4)))
