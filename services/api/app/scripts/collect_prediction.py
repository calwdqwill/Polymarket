import argparse
import asyncio
import hashlib
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.prediction.discovery import Discovery
from app.prediction.live_screen import render_live
from app.prediction.live_storage import LiveJournal, atomic_json
from app.prediction.live_transport import stream_market
from app.prediction.models import match_markets
from app.prediction.observer import Observer


async def collect(output, full_windows=3, max_age_ms=2000, seconds=None, stop_event=None, safety=None, identity=None):
    journal = LiveJournal(output)
    observer = Observer(max_age_ms)
    books, markets, tasks = {}, {}, {}
    start = datetime.now(timezone.utc)
    stop_epoch = (int(start.timestamp()) // 300 + 1 + full_windows) * 300
    if seconds is not None:
        stop_epoch = start.timestamp() + seconds
    state = {}
    active_window = None
    last_signature = None

    def register(values):
        for value in values:
            books[value.venue, value.market_id, value.outcome] = value

    def evaluate(now, mono, changed_market=None, observe=True):
        nonlocal state, active_window, last_signature
        window = int(now.timestamp()) // 300 * 300
        if window != active_window:
            for event in observer.close_all(now, "ROTATION"):
                journal.append("opportunities", event, now, mono)
            active_window = window
            journal.append("windows", dict(event="ACTIVE", start=window, end=window + 300), now, mono)
        current = [m for m in markets.values() if int(m.start.timestamp()) == window]
        pair = {m.venue: m for m in current}
        rows = []
        quality = "UNKNOWN"
        if "Polymarket" in pair and "Limitless" in pair:
            a, b = pair["Polymarket"], pair["Limitless"]
            quality = match_markets(a, b).quality
            rows, events = observer.evaluate(a, b, books, now, mono, observe=False)
        top = []
        for venue in ["Polymarket", "Limitless"]:
            m = pair.get(venue)
            y = books.get((venue, m.market_id, "YES")) if m else None
            n = books.get((venue, m.market_id, "NO")) if m else None
            top.append(
                [
                    venue,
                    max(y.bids, default=None) if y else None,
                    min(y.asks, default=None) if y else None,
                    max(n.bids, default=None) if n else None,
                    min(n.asks, default=None) if n else None,
                    max(y.age_ms(mono), n.age_ms(mono)) if y and n else None,
                    "/".join(v.current_status(mono, max_age_ms) for v in [y, n]) if y and n else "MISSING",
                ]
            )
        health = {}
        for venue in ("Polymarket", "Limitless"):
            market = pair.get(venue)
            for outcome in ("YES", "NO"):
                book = books.get((venue, market.market_id, outcome)) if market else None
                key = venue + ":" + outcome
                health[key] = dict(
                    status=book.current_status(mono, max_age_ms) if book else "MISSING",
                    valid_until_ns=min(
                        book.connection_deadline_ns,
                        book.received_monotonic_ns + (book.freshness_ttl_ms or max_age_ms) * 1_000_000,
                    )
                    if book
                    else mono,
                    healthy_until_ns=book.connection_deadline_ns if book else mono,
                    book_received_ns=book.received_monotonic_ns if book else 0,
                )
        compact = [
            ["B" if r["venue_yes"] == "Polymarket" else "A", str(r["share_size"]), r["status"], r["observed_edge"]]
            for r in rows
        ]
        signature = (window, quality, str(compact), tuple(v["status"] for v in health.values()))
        if (
            changed_market is not None and int(changed_market.start.timestamp()) == window
        ) or signature != last_signature:
            journal.append(
                "observations",
                dict(
                    window=window,
                    quality=quality,
                    rows=compact,
                    books=health,
                    event_weighted=bool(changed_market and observe and int(changed_market.start.timestamp()) == window),
                ),
                now,
                mono,
            )
            last_signature = signature
        state = dict(
            timestamp=now,
            window=window,
            quality=quality,
            rows=rows,
            top=top,
            status="LIVE",
            stop_at=datetime.fromtimestamp(stop_epoch, timezone.utc),
            max_age_ms=max_age_ms,
        )

    async def refresh(discovery):
        while datetime.now(timezone.utc).timestamp() < stop_epoch:
            discovered = await discovery.collect(datetime.now(timezone.utc))
            for market in discovered:
                key = (market.venue, market.market_id)
                markets[key] = market
                if key not in tasks:
                    tasks[key] = asyncio.create_task(stream_market(market, journal, evaluate, register))
            for a in discovered:
                for b in discovered:
                    if a.venue == "Polymarket" and b.venue == "Limitless" and a.start == b.start:
                        journal.append("matches", dict(window=a.canonical_market_id, **asdict(match_markets(a, b))))
            expired = [key for key in tasks if markets[key].end <= datetime.now(timezone.utc)]
            for key in expired:
                tasks[key].cancel()
            if expired:
                await asyncio.gather(*(tasks[key] for key in expired), return_exceptions=True)
            for key in expired:
                del tasks[key]
                market = markets.pop(key)
                for outcome in ("YES", "NO"):
                    books.pop((market.venue, market.market_id, outcome), None)
            await asyncio.sleep(10)

    print(str(output.resolve()), flush=True)
    atomic_json(
        output / "run.json",
        dict(
            identity=identity,
            session_id=journal.session,
            started=start,
            stop_at=stop_epoch,
            full_windows_requested=full_windows,
            max_age_ms=max_age_ms,
            schema_version=2,
            checkpoint_seconds=10,
            segment_seconds=60,
            freshness_policy="Poly 30s; Limitless 60s with idle resubscribe at 30s; application heartbeat deadlines",
            source_sha256={
                str(p.relative_to(Path(__file__).parents[1])): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(__file__).parents[1].glob("prediction/*.py")
            },
            pid=__import__("os").getpid(),
        ),
    )
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        discovery_task = asyncio.create_task(refresh(Discovery(client, journal)))
        last_view = 0
        previous_tick = time.perf_counter_ns()
        failure = None
        try:
            while datetime.now(timezone.utc).timestamp() < stop_epoch:
                if (stop_event and stop_event.is_set()) or (output / "STOP").exists():
                    break
                if discovery_task.done():
                    discovery_task.result()
                for task in tasks.values():
                    if task.done() and not task.cancelled():
                        task.result()
                        raise RuntimeError("Market-data task exited unexpectedly")
                now, mono = datetime.now(timezone.utc), time.perf_counter_ns()
                lag_ms = max(0, (mono - previous_tick) / 1e6 - 50)
                previous_tick = mono
                if lag_ms > 100:
                    journal.append("processing_lag", dict(timer_lag_ms=lag_ms), now, mono)
                evaluate(now, mono, observe=False)
                if mono - last_view >= 1_000_000_000:
                    if shutil.disk_usage(output).free < 2 * 1024**3:
                        raise RuntimeError("Less than 2 GiB free disk; stopping losslessly")
                    if safety:
                        safety(journal)
                    json_ok = atomic_json(output / "live.json", state)
                    html_ok = render_live(state, output / "live.html")
                    if not json_ok or not html_ok:
                        journal.append("view_warnings", dict(json_published=json_ok, html_published=html_ok))
                    journal.flush()
                    atomic_json(output / "metrics.json", journal.metrics())
                    last_view = mono
                await asyncio.sleep(0.05)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            discovery_task.cancel()
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(discovery_task, *tasks.values(), return_exceptions=True)
            for event in observer.close_all(datetime.now(timezone.utc), "STOP"):
                journal.append("opportunities", event)
            state["status"] = "FAILED" if failure else "STOPPED"
            state["failure"] = failure
            state["timestamp"] = datetime.now(timezone.utc)
            atomic_json(output / "live.json", state)
            render_live(state, output / "live.html")
            journal.close()
            atomic_json(output / "metrics.json", journal.metrics())


def main():
    parser = argparse.ArgumentParser(description="Public read-only BTC 5m L2 research collector")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-windows", type=int, default=3)
    parser.add_argument("--max-age-ms", type=int, default=2000)
    parser.add_argument("--seconds", type=int)
    args = parser.parse_args()
    if args.full_windows < 1 or args.max_age_ms <= 0 or (args.seconds is not None and args.seconds <= 0):
        parser.error("Durations and window count must be positive")
    output = args.output or Path("data/prediction") / (
        "live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    asyncio.run(collect(output, args.full_windows, args.max_age_ms, args.seconds))


if __name__ == "__main__":
    main()
