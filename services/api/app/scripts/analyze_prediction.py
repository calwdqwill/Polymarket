import argparse
import gzip
import json
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.prediction.live_storage import atomic_json
from app.prediction.observer import SIZES

D = Decimal
CUTS = ("0", ".01", ".02", ".03", ".05", ".08", ".10")


def records(path, stream):
    files = sorted(path.glob(stream + ".*.jsonl.gz"))
    legacy = path / (stream + ".jsonl.gz")
    if legacy.exists():
        files.insert(0, legacy)
    for file in files:
        with gzip.open(file, "rt", encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line, parse_float=D)


def distribution(values):
    if not values:
        return dict(count=0, min=None, median=None, p90=None, p95=None, max=None)
    values = sorted(values)

    def quantile(q):
        position = D(len(values) - 1) * D(q)
        low = int(position)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)

    return dict(
        count=len(values),
        min=values[0],
        median=quantile(".5"),
        p90=quantile(".90"),
        p95=quantile(".95"),
        max=values[-1],
    )


def remaining_valid_seconds(row, mono, max_age_ms, invalidations):
    budget = min(
        max(D(0), D(row["time_to_expiry"])),
        max(D(0), (D(max_age_ms) - max(D(row["book_age_poly"]), D(row["book_age_limitless"]))) / 1000),
    )
    index = bisect_right(invalidations, mono)
    if index < len(invalidations):
        budget = min(budget, D(invalidations[index] - mono) / D(1e9))
    return budget


def analyze(path):
    run = json.loads((path / "run.json").read_text(encoding="utf8"))
    if run.get("schema_version") == 2:
        from app.scripts.analyze_prediction_research import analyze as analyze_research

        return analyze_research(path)
    final = json.loads((path / "live.json").read_text(encoding="utf8"))
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf8"), parse_float=D)
    begin = datetime.fromisoformat(run["started"]).timestamp()
    end = datetime.fromisoformat(final["timestamp"]).timestamp()
    full = list(range((int(begin) // 300 + 1) * 300, int(end) // 300 * 300, 300))
    # Include a window ending exactly at the final boundary.
    windows = {w: dict(start=w, end=w + 300, observations=0, valid_rows=0) for w in full}
    invalidations = defaultdict(list)
    for record in records(path, "connections"):
        if record["payload"]["event"] == "INVALID":
            window = int(record["payload"]["market"].rsplit("-", 1)[1])
            invalidations[window].append(record["monotonic_received_ns"])
    edges, pnl = defaultdict(list), defaultdict(list)
    statuses, buckets, seconds, positive_seconds = (
        defaultdict(Counter),
        defaultdict(Counter),
        Counter(),
        defaultdict(Counter),
    )
    previous = {}
    size_keys = [direction + ":" + str(size) for direction in ("A", "B") for size in SIZES]
    for key in size_keys:
        edges[key], pnl[key], seconds[key] = [], [], D(0)
        buckets[key].update({cut: 0 for cut in CUTS})
        positive_seconds[key].update({cut: D(0) for cut in CUTS})
    observations = 0
    for record in records(path, "observations"):
        payload = record["payload"]
        rows = payload["rows"]
        window = int(
            datetime.strptime(payload["window"], "BTC-5M-%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        )
        if window not in windows:
            continue
        observations += 1
        windows[window]["observations"] += 1
        mono = record["monotonic_received_ns"]
        for row in rows:
            direction = "A" if row["venue_yes"] == "Limitless" else "B"
            key = direction + ":" + row["share_size"]
            prior_key = (window, key)
            if prior_key in previous:
                old, old_mono = previous[prior_key]
                if old["observed_edge"] is not None:
                    elapsed = min(
                        D(mono - old_mono) / D(1e9),
                        remaining_valid_seconds(old, old_mono, run["max_age_ms"], invalidations[window]),
                    )
                    seconds[key] += elapsed
                    for cut in CUTS:
                        if D(old["observed_edge"]) > D(cut):
                            positive_seconds[key][cut] += elapsed
            previous[prior_key] = (row, mono)
            statuses[key][row["status"]] += 1
            if row["observed_edge"] is not None:
                edge = D(row["observed_edge"])
                edges[key].append(edge)
                pnl[key].append(D(row["gross_pnl"]))
                windows[window]["valid_rows"] += 1
                for cut in CUTS:
                    if edge > D(cut):
                        buckets[key][cut] += 1
    # Tail interval: retain only time bounded by staleness and expiry, never extrapolate beyond the run.
    for (window, key), (old, mono) in previous.items():
        if old["observed_edge"] is not None:
            elapsed = remaining_valid_seconds(old, mono, run["max_age_ms"], invalidations[window])
            seconds[key] += elapsed
            for cut in CUTS:
                if D(old["observed_edge"]) > D(cut):
                    positive_seconds[key][cut] += elapsed
    lifetimes, censored, lifetime_buckets, reasons = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(Counter),
        defaultdict(Counter),
    )
    censored_buckets = defaultdict(Counter)
    labels = ("<25 ms", "25-50 ms", "50-100 ms", "100-250 ms", "250-500 ms", "0.5-1 sec", "1-5 sec", ">=5 sec")
    for key in size_keys:
        lifetimes[key], censored[key] = [], []
        lifetime_buckets[key].update({label: 0 for label in labels})
        censored_buckets[key].update({label: 0 for label in labels})
    for record in records(path, "opportunities"):
        event = record["payload"]
        if event["event_type"] != "CLOSE":
            continue
        window = int(
            datetime.strptime(event["canonical_market_id"], "BTC-5M-%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        if window not in windows:
            continue
        key = ("A" if event["venue_yes"] == "Limitless" else "B") + ":" + event["share_size"]
        reasons[key][event["close_reason"]] += 1
        if event["censored"]:
            censored[key].append(D(event["duration_ms"]))
            censored_buckets[key][event["lifetime_bucket"]] += 1
        else:
            lifetimes[key].append(D(event["duration_ms"]))
            lifetime_buckets[key][event["lifetime_bucket"]] += 1
    depths, levels, gaps, first_last = defaultdict(list), defaultdict(list), defaultdict(list), {}
    books_counts = Counter()
    for venue in ("polymarket", "limitless"):
        previous_book = {}
        for record in records(path, "books_" + venue):
            book = record["payload"]
            window = int(
                datetime.strptime(book["canonical_market_id"], "BTC-5M-%Y%m%dT%H%M%SZ")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
            utc = datetime.fromisoformat(record["received_timestamp"]).timestamp()
            if window not in windows or not window <= utc < window + 300:
                continue
            key = venue + ":" + book["outcome"]
            identity = (window, key)
            mono = record["monotonic_received_ns"]
            if identity in previous_book:
                gaps[key].append(D(mono - previous_book[identity]) / D(1e6))
            previous_book[identity] = mono
            if identity not in first_last:
                first_last[identity] = [utc, utc]
            first_last[identity][1] = utc
            books_counts[key] += 1
            if book["status"] == "VALID":
                for side in ("bids", "asks"):
                    levels[key + ":" + side].append(D(len(book[side])))
                    depths[key + ":" + side].append(sum((D(v["size"]) for v in book[side]), D(0)))
    errors = Counter(
        (r["payload"].get("venue", "HTTP") + ": " + r["payload"].get("detail", "")) for r in records(path, "errors")
    )
    manifest = {}
    for record in records(path, "markets"):
        market = record["payload"]
        if int(datetime.fromisoformat(market["start"]).timestamp()) in windows:
            manifest[market["slug"]] = dict(market, last_metadata_received=record["received_timestamp"])
    quality = Counter(r["payload"]["quality"] for r in records(path, "matches"))
    for stream, values in metrics["streams"].items():
        values["stored_bytes"] = (path / (stream + ".jsonl.gz")).stat().st_size
        values["stored_mb_per_hour"] = D(values["stored_bytes"]) / metrics["duration_seconds"] * 3600 / D(1e6)
    result = dict(
        run=run,
        stopped=final["status"] == "STOPPED",
        duration_seconds=D(str(end - begin)),
        full_windows=list(windows.values()),
        market_manifest=list(manifest.values()),
        observations=observations,
        status_counts=dict(statuses),
        observed_edge={k: distribution(v) for k, v in edges.items()},
        gross_pnl={k: distribution(v) for k, v in pnl.items()},
        edge_bucket_counts=dict(buckets),
        valid_depth_seconds=dict(seconds),
        edge_bucket_seconds=dict(positive_seconds),
        lifetime_ms={k: distribution(v) for k, v in lifetimes.items()},
        censored_lifetime_ms={k: distribution(v) for k, v in censored.items()},
        lifetime_bucket_counts=dict(lifetime_buckets),
        censored_lifetime_bucket_counts=dict(censored_buckets),
        close_reasons=dict(reasons),
        depth_shares={k: distribution(v) for k, v in depths.items()},
        depth_levels={k: distribution(v) for k, v in levels.items()},
        book_update_gap_ms={k: distribution(v) for k, v in gaps.items()},
        book_update_counts=dict(books_counts),
        window_book_coverage=[
            dict(window=w, book=k, first=times[0], last=times[1]) for (w, k), times in first_last.items()
        ],
        errors=dict(errors),
        matching_quality=dict(quality),
        metrics=metrics,
        limitations=[
            "Update-weighted edge distribution; time estimates capped at staleness and expiry.",
            "Lifetimes are first-to-last-positive local observations, not network latency.",
            "Displayed depth only; fees, atomic fills, trading minimum and settlement not fully verified.",
            "Volume includes current and pre-subscribed next market. Limitless NO is not stored or added twice.",
        ],
    )
    atomic_json(path / "analysis.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Analyze a completed public L2 collection")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = analyze(args.directory)
    if "cross_venue_valid_coverage" in result:
        print(
            json.dumps(
                dict(
                    duration_seconds=str(result["duration_seconds"]),
                    cross_venue_valid_coverage=str(result["cross_venue_valid_coverage"]),
                    decision=result["decision"],
                )
            )
        )
        return
    print(
        json.dumps(
            dict(full_windows=len(result["full_windows"]), observations=result["observations"], errors=result["errors"])
        )
    )


if __name__ == "__main__":
    main()
