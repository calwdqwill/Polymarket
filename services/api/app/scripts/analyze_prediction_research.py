"""Duration-weighted BTC research, with exact validity boundaries and bounded memory."""

import argparse
import gzip
import heapq
import json
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from app.prediction.live_storage import atomic_json, decode
from app.prediction.research_statistics import Episodes, ExactStatistics
from app.prediction.storage import dumps
from app.scripts.analyze_prediction import records

TTE = ((240, "300-240"), (180, "240-180"), (120, "180-120"), (60, "120-60"), (30, "60-30"), (10, "30-10"), (0, "<10"))


def epoch_ns(value):
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def connection_coverage(path, begin, end):
    """Transport health begins at CONNECTED, independently of the book snapshot."""
    coverage, connections = defaultdict(Counter), {}

    def add(state, finish):
        start = state["utc"]
        finish = start + max(0, min(finish, state["deadline"]) - state["mono"])
        window = state["window"]
        elapsed = max(0, min(end, (window + 300) * 1_000_000_000, finish) - max(begin, window * 1_000_000_000, start))
        coverage[window][state["venue"] + "_connection_healthy_ns"] += elapsed

    streams = [records(path, s) for s in ("connections", "raw_ws_polymarket", "raw_ws_limitless")]
    for record in heapq.merge(*streams, key=lambda r: r["local_ordinal"]):
        connection, p, mono = record["connection"], record["payload"], record["monotonic_received_ns"]
        if p.get("event") == "CONNECTED":
            connections[connection] = dict(
                venue=p["venue"],
                window=int(p["market"].rsplit("-", 1)[1]),
                mono=mono,
                utc=epoch_ns(record["received_timestamp"]),
                deadline=mono + 30_000_000_000,
                ttl=30_000_000_000,
            )
        elif p.get("event") == "INVALID":
            if connection in connections:
                add(connections.pop(connection), mono)
        elif "frame" in p and connection in connections:
            state = connections[connection]
            if mono > state["deadline"]:
                add(state, state["deadline"])
                state.update(mono=mono, utc=epoch_ns(record["received_timestamp"]))
            if state["venue"] == "Limitless" and p["frame"].startswith("0"):
                handshake = decode(p["frame"][1:])
                state["ttl"] = (int(handshake["pingInterval"]) + int(handshake["pingTimeout"])) * 1_000_000
            state["deadline"] = mono + state["ttl"]
    for state in connections.values():
        add(state, state["mono"] + end - state["utc"])
    return coverage


def analyze(path, full_windows_only=False, excluded_windows=(), progress=False):
    excluded_windows = set(excluded_windows)
    run = json.loads((path / "run.json").read_text())
    final = json.loads((path / "live.json").read_text())
    metrics = json.loads((path / "metrics.json").read_text(), parse_float=D)
    if final["status"] not in ("STOPPED", "FAILED", "FROZEN_PREFIX"):
        raise ValueError("Analyze a stopped run or an immutable closed-segment prefix")
    begin, finish = epoch_ns(run["started"]), epoch_ns(final["timestamp"])
    coverage = defaultdict(Counter)
    episode_counts, capacity_counts = Counter(), defaultdict(Counter)
    lifetime_keys = set()
    with tempfile.TemporaryDirectory(prefix="prediction-analysis-") as scratch:
        stats = ExactStatistics(Path(scratch) / "weights.sqlite")
        with gzip.open(path / "opportunities.jsonl.gz", "wt", encoding="utf8") as output:
            anchor = None

            def emit(event):
                key = f'{event["direction"]}:{event["quantity"]}:{event["threshold"]}'
                episode_counts[key] += 1
                coverage[event["window"]]["episodes:" + key] += 1
                for threshold, quantity in event["capacity_by_threshold"].items():
                    capacity_counts[key + ":" + threshold][str(quantity)] += 1
                category = "censored" if event["left_censored"] or event["right_censored"] else "completed"
                stats.add("lifetime", key + ":" + category, event["duration_ms"], events=1)
                lifetime_keys.add(key + ":" + category)
                if anchor is not None:
                    for field in ("start", "end"):
                        event[field] = anchor[1] + timedelta(microseconds=(event[field + "_ns"] - anchor[0]) // 1000)
                output.write(dumps(event) + "\n")

            episodes = Episodes(emit)
            previous = None
            last_window = None

            def missing_between(template, start_utc, start_ns, end_ns):
                cursor = start_ns
                while cursor < end_ns:
                    utc = start_utc + cursor - start_ns
                    window = utc // 1_000_000_000 // 300 * 300
                    stop = min(end_ns, cursor + (window + 300) * 1_000_000_000 - utc)
                    timestamp = datetime.fromtimestamp(utc // 1_000_000_000, timezone.utc) + timedelta(
                        microseconds=(utc % 1_000_000_000) // 1000
                    )
                    missing = dict(
                        template,
                        received_timestamp=timestamp.isoformat(),
                        monotonic_received_ns=cursor,
                        payload=dict(
                            template["payload"],
                            window=window,
                            rows=[],
                            event_weighted=False,
                            books={
                                k: dict(v, status="MISSING", healthy_until_ns=0)
                                for k, v in template["payload"]["books"].items()
                            },
                        ),
                    )
                    integrate(missing, stop)
                    cursor = stop

            def integrate(record, end_ns, next_window=None):
                nonlocal last_window
                p = record["payload"]
                start_ns = record["monotonic_received_ns"]
                window = p["window"]
                include = window not in excluded_windows and (
                    not full_windows_only
                    or (begin <= window * 1_000_000_000 and finish >= (window + 300) * 1_000_000_000)
                )
                utc_ns = epoch_ns(record["received_timestamp"])
                window_end_ns = start_ns + (window + 300) * 1_000_000_000 - utc_ns
                end_ns = min(end_ns, window_end_ns)
                if end_ns < start_ns:
                    raise ValueError("Non-monotonic observation timeline")
                if window != last_window:
                    episodes.close_all(start_ns, "ROTATION")
                    last_window = window
                cuts = {start_ns, end_ns}
                for book in p["books"].values():
                    for deadline in (
                        book["valid_until_ns"],
                        book["healthy_until_ns"],
                        book["book_received_ns"] + 2_000_000_000,
                    ):
                        if start_ns < deadline < end_ns:
                            cuts.add(deadline)
                for seconds, _ in TTE:
                    boundary = window_end_ns - seconds * 1_000_000_000
                    if start_ns < boundary < end_ns:
                        cuts.add(boundary)
                points = sorted(cuts)
                # Event distributions include zero-duration transitions, once per applied frame.
                if include and p.get("event_weighted"):
                    for direction, quantity, status, edge in p["rows"]:
                        if edge is not None and status in ("OPPORTUNITY", "NO_EDGE"):
                            stats.add("edge", direction + ":" + quantity, D(edge), events=1)
                for left, right in zip(points, points[1:]):
                    dt = right - left
                    c = coverage[window]
                    c["total_monitoring_ns"] += dt
                    effective = {
                        key: (
                            "STALE" if book["status"] == "VALID" and left >= book["valid_until_ns"] else book["status"]
                        )
                        for key, book in p["books"].items()
                    }
                    valid = {}
                    for venue in ("Polymarket", "Limitless"):
                        pair = [venue + ":" + outcome for outcome in ("YES", "NO")]
                        if all(left < p["books"][key]["healthy_until_ns"] for key in pair):
                            c[venue + "_connection_healthy_ns"] += dt
                        valid[venue] = all(effective[key] == "VALID" for key in pair)
                        statuses = {effective[key] for key in pair}
                        status = next(
                            (s for s in ("DESYNC", "RECOVERING", "MISSING", "STALE") if s in statuses), "VALID"
                        )
                        c[venue + "_" + status + "_ns"] += dt
                    both = all(valid.values())
                    if both:
                        c["both_valid_ns"] += dt
                    if both and all(left < b["book_received_ns"] + 2_000_000_000 for b in p["books"].values()):
                        c["baseline_2s_both_valid_ns"] += dt
                    c["both_invalid_ns"] += 0 if both else dt
                    if not both:
                        statuses = set(effective.values())
                        reason = next(
                            (s for s in ("DESYNC", "RECOVERING", "MISSING", "STALE") if s in statuses), "OTHER"
                        )
                        c["cross_venue_" + reason + "_ns"] += dt
                    remaining = D(window_end_ns - left) / 1_000_000_000
                    bucket = next(label for lower, label in TTE if remaining > lower)
                    rows = []
                    for direction, quantity, status, edge in p["rows"]:
                        keys = (
                            ("Limitless:YES", "Polymarket:NO")
                            if direction == "A"
                            else ("Polymarket:YES", "Limitless:NO")
                        )
                        executable = (
                            edge is not None
                            and status in ("OPPORTUNITY", "NO_EDGE")
                            and all(effective[k] == "VALID" for k in keys)
                        )
                        rows.append(
                            [direction, quantity, status if executable else "INVALID", edge if executable else None]
                        )
                        if executable:
                            key = direction + ":" + quantity
                            c["executable_ns:" + key] += dt
                            if include:
                                stats.add("edge", key, D(edge), ns=dt)
                                stats.add("tte", key + ":" + bucket, D(edge), ns=dt)
                            if D(edge) > 0:
                                c["positive_ns:" + key] += dt
                    for quantity in ("10", "25", "50", "100", "250", "500", "1000"):
                        if sum(1 for _, q, status, _ in rows if q == quantity and status != "INVALID") == 2:
                            c["both_directions_executable_ns:" + quantity] += dt
                    episodes.update(
                        window,
                        left,
                        right,
                        rows if include else [],
                        "INVALID",
                        left_censored=not c["total_monitoring_ns"] > dt
                        or any(s != "VALID" for s in effective.values()),
                    )
                if end_ns == window_end_ns or next_window is not None and next_window != window:
                    episodes.close_all(end_ns, "ROTATION")

            for record_index, record in enumerate(records(path, "observations"), 1):
                if progress and record_index % 100_000 == 0:
                    atomic_json(
                        path / "analysis-progress.json",
                        dict(
                            stage="OBSERVATIONS",
                            observations=record_index,
                            through_timestamp=record["received_timestamp"],
                            updated_at=datetime.now(timezone.utc),
                        ),
                    )
                if anchor is None:
                    anchor = (record["monotonic_received_ns"], datetime.fromisoformat(record["received_timestamp"]))
                    offset = epoch_ns(record["received_timestamp"]) - epoch_ns(run["started"])
                    if offset > 0:
                        missing_between(
                            record,
                            epoch_ns(run["started"]),
                            record["monotonic_received_ns"] - offset,
                            record["monotonic_received_ns"],
                        )
                if previous is not None:
                    integrate(previous, record["monotonic_received_ns"], record["payload"]["window"])
                    if previous["payload"]["window"] != record["payload"]["window"]:
                        boundary = (previous["payload"]["window"] + 300) * 1_000_000_000
                        boundary_ns = (
                            previous["monotonic_received_ns"] + boundary - epoch_ns(previous["received_timestamp"])
                        )
                        missing_between(record, boundary, boundary_ns, record["monotonic_received_ns"])
                previous = record
            if previous is not None:
                final_ns = (
                    previous["monotonic_received_ns"]
                    + epoch_ns(final["timestamp"])
                    - epoch_ns(previous["received_timestamp"])
                )
                integrate(previous, final_ns)
                episodes.close_all(final_ns, "STOP")
        edge, tte = stats.report("edge"), stats.report("tte")
        stats.flush()
        lifetime = {key: stats.distribution("lifetime", key, "events") for key in sorted(lifetime_keys)}
        intervals = defaultdict(lambda: None)
        freshness_counts = Counter()
        previous_books = {}
        for record in records(path, "raw_ws_limitless"):
            frame = record["payload"]["frame"]
            connection, mono = record["connection"], record["monotonic_received_ns"]
            kind = "heartbeat" if frame.startswith("2") else None
            if frame.startswith("42/markets,"):
                data = decode(frame.split(",", 1)[1])
                if data[0] == "orderbookUpdate":
                    kind = "book"
                    previous_book = previous_books.get(connection)
                    current_book = data[1]
                    freshness_counts["full_book_messages"] += 1
                    if previous_book is not None:
                        freshness_counts["unchanged_full_books"] += int(
                            previous_book["orderbook"] == current_book["orderbook"]
                        )
                        freshness_counts["repeated_version"] += int(previous_book["version"] == current_book["version"])
                        freshness_counts["version_regression"] += int(
                            int(current_book["version"]) < int(previous_book["version"])
                        )
                    previous_books[connection] = current_book
            if kind:
                old = intervals[connection, kind]
                if old is not None:
                    stats.add("intervals", kind, D(mono - old) / 1_000_000, events=1)
                    if kind == "book":
                        freshness_counts["book_gaps_gt_2s"] += int(mono - old > 2_000_000_000)
                        freshness_counts["book_gaps_gt_30s"] += int(mono - old > 30_000_000_000)
                intervals[connection, kind] = mono
        stats.flush()
        freshness = {k: stats.distribution("intervals", k, "events") for k in ("book", "heartbeat")}
        stats.close()
    total = Counter()
    windows = {}
    begin, end = epoch_ns(run["started"]), epoch_ns(final["timestamp"])
    transport_health = connection_coverage(path, begin, end)
    for window, counts in sorted(coverage.items()):
        if transport_health:
            for venue in ("Polymarket", "Limitless"):
                counts[venue + "_connection_healthy_ns"] = transport_health[window][venue + "_connection_healthy_ns"]
        numeric = {k: v for k, v in counts.items() if not k.startswith("episodes:")}
        total.update(numeric)
        windows[window] = dict(
            counts,
            cross_venue_valid_coverage=D(counts["both_valid_ns"]) / counts["total_monitoring_ns"],
            full_window=begin <= window * 1_000_000_000 and end >= (window + 300) * 1_000_000_000,
            strategy_window=window not in excluded_windows
            and (not full_windows_only or begin <= window * 1_000_000_000 and end >= (window + 300) * 1_000_000_000),
            replay_excluded=window in excluded_windows,
        )
    duration = D(total["total_monitoring_ns"]) / 1_000_000_000
    strategy_duration = (
        sum(D(w["total_monitoring_ns"]) for w in windows.values() if w["strategy_window"]) / 1_000_000_000
        if full_windows_only or excluded_windows
        else duration
    )
    storage = {}
    for stream, values in metrics["streams"].items():
        storage[stream] = dict(
            jsonl_mb_per_hour=values["mb_per_hour"],
            gzip_mb_per_hour=D(values["stored_bytes"]) / D(metrics["duration_seconds"]) * 3600 / 1_000_000,
        )
    total_mb = sum(v["gzip_mb_per_hour"] for v in storage.values())
    episode_bytes = (path / "opportunities.jsonl.gz").stat().st_size
    opportunity_rate = D(episode_bytes) / max(duration, D(".001")) * 3600 / 1_000_000
    storage["opportunities_derived"] = dict(gzip_mb_per_hour=opportunity_rate)
    total_mb += opportunity_rate
    result = dict(
        duration_seconds=duration,
        strategy_duration_seconds=strategy_duration,
        full_windows_only=full_windows_only,
        excluded_windows=sorted(excluded_windows),
        dataset_status=final["status"],
        windows=windows,
        totals_ns=total,
        cross_venue_valid_coverage=D(total["both_valid_ns"]) / duration / 1_000_000_000 if duration else None,
        baseline_2s_coverage=D(total["baseline_2s_both_valid_ns"]) / duration / 1_000_000_000 if duration else None,
        edge=edge,
        time_to_expiry=tte,
        episode_counts=episode_counts,
        episodes_per_hour={k: D(n) / strategy_duration * 3600 for k, n in episode_counts.items()},
        capacity_counts=capacity_counts,
        lifetime_ms=lifetime,
        limitless_intervals_ms=freshness,
        limitless_freshness_counts=freshness_counts,
        storage=storage,
        normalized_delta_mb_per_hour=0,
        total_gzip_mb_per_hour=total_mb,
        projected_gb={str(days): total_mb * 24 * days / 1000 for days in (1, 7, 30)},
        quantile_method="exact inverse empirical CDF; event count or valid duration in nanoseconds",
        episode_scope="ANY_Q is the union across sizes per direction and threshold; do not sum sizes or thresholds",
        decision="MORE DATA REQUIRED",
        settlement="UNKNOWN",
        distance_to_strike="SKIPPED: no verified contemporaneous reference",
    )
    atomic_json(path / "research-analysis.json", result)
    if progress:
        atomic_json(path / "analysis-progress.json", dict(stage="COMPLETE", updated_at=datetime.now(timezone.utc)))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze finalized schema v2 research logs")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--full-windows-only", action="store_true")
    parser.add_argument("--replay-exclusions", type=Path)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()
    exclusions = (
        json.loads(args.replay_exclusions.read_text())["strategy_excluded_windows"] if args.replay_exclusions else []
    )
    result = analyze(
        args.directory, full_windows_only=args.full_windows_only, excluded_windows=exclusions, progress=args.progress
    )
    print(
        dumps(
            {
                k: result[k]
                for k in (
                    "duration_seconds",
                    "cross_venue_valid_coverage",
                    "baseline_2s_coverage",
                    "total_gzip_mb_per_hour",
                    "decision",
                )
            }
        )
    )
