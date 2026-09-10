"""Replay the frozen local shadow profile and compare to saved Target Profit V1 only."""

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from app.prediction.shadow_models import ShadowStrategyConfig, canonical
from app.prediction.shadow_replay import compact_results, replay_window, spec_for
from app.prediction.shadow_storage import InMemoryShadowJournal, JsonlShadowJournal
from app.scripts.analyze_prediction import records


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def comparison(result, baseline):
    rows = []
    for window in result["windows"]:
        ms = window["scenario_ms"]
        old = next(r for r in baseline if r["parent_config_id"] == 583 and
                   r["variant"] == ("cap15" if ms == 100 else "250ms_cap15"))
        if not (old["fixed_q"] == 10 and D(old["threshold"]) == D(".10") and D(old["edge_cap"]) == D(".15")
                and old["fee_case"] == "conservative" and old["retry"] == "crossing"
                and old["poly_ms"] == ms and old["limitless_ms"] == ms and old["mode"] == "parallel"
                and D(old["friction"]) == D(".0025") and D(old["target"]) == D(".5") and old["min_tte"] == 30):
            raise ValueError("Baseline is not the frozen profile")
        new = [a for a in result["attempts"] if a["scenario_ms"] == ms]
        new_signals = [(a["signal_ns"], a["direction"]) for a in new]
        old_signals = [(a["signal_ns"], a["direction"]) for a in old["trades"]]
        common = []
        for a in new:
            b = next((t for t in old["trades"] if t["signal_ns"] == a["signal_ns"] and t["direction"] == a["direction"]), None)
            if b:
                common.append(dict(signal_ns=a["signal_ns"], same_net=D(a["simulated_net"]) == D(b["net"]),
                                   old_net=b["net"], new_net=a["simulated_net"]))
        same = (new_signals == old_signals and D(window["simulated_net"]) == D(old["net"])
                and window["target_captured"] == old["captured"])
        rows.append(dict(window=old["window"], scenario_ms=ms, exact_match=same,
                         old_signals=old_signals, new_signals=new_signals, old_net=old["net"],
                         new_net=window["simulated_net"], old_captured=old["captured"],
                         new_captured=window["target_captured"], common_attempts=common))
    return rows


def replay_job(job):
    research, output, window, expected_hash, market_ids, session = job
    config = ShadowStrategyConfig(datetime(2026, 9, 10, tzinfo=timezone.utc))
    path = research / "cache" / f"{window}.json.gz"
    before = sha(path)
    if before != expected_hash:
        raise ValueError(f"Cache hash mismatch: {path.name}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data["window"] != window:
        raise ValueError("Cache window mismatch")
    spec = spec_for(window, market_ids, data["rows"][0][3])
    with JsonlShadowJournal(output / f"{window}.journal.jsonl") as repo:
        replay_window(data, spec, session, config, repo)
        first = compact_results(repo)
        first_hash = hashlib.sha256(canonical(list(repo.records())).encode()).hexdigest()
    repeated = replay_window(data, spec, session, config, InMemoryShadowJournal())
    second_hash = hashlib.sha256(canonical(list(repeated.records())).encode()).hexdigest()
    if first_hash != second_hash or first != compact_results(repeated):
        raise ValueError(f"Non-deterministic replay: {window}")
    baseline_path = research / "selected_runs" / f"{window}.json.gz"
    baseline_hash = sha(baseline_path)
    with gzip.open(baseline_path, "rt", encoding="utf-8") as handle:
        baseline = json.load(handle)
    rows = comparison(first, baseline)
    if sha(path) != before or sha(baseline_path) != baseline_hash:
        raise ValueError("Historical source changed during replay")
    hashes = dict(source_sha256=before, baseline_sha256=baseline_hash,
                  journal_sha256=first_hash, repeat_sha256=second_hash)
    (output / f"{window}.comparison.json").write_text(canonical(rows), encoding="utf-8")
    return window, rows, hashes, len(data["rows"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("research", type=Path)
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--limit", type=int, default=0, help="Optional local smoke subset; 0 means all approved windows")
    p.add_argument("--workers", type=int, default=1, choices=(1, 2, 3), help="Bounded local processes, never VPS jobs")
    args = p.parse_args()
    research, dataset, output = args.research.resolve(), args.dataset.resolve(), args.output.resolve()
    for source in (research, dataset):
        if output == source or source in output.parents or output in source.parents:
            raise ValueError("Output must be separate from historical source directories")
    if args.limit < 0:
        raise ValueError("Nonnegative limit required")
    manifest = json.loads((research / "cache-manifest.json").read_text())
    exclusions = json.loads((dataset / "observation-replay.json").read_text())
    if not exclusions["consistent"]:
        raise ValueError("Historical observation replay is not consistent")
    windows = manifest["windows"]
    if set(exclusions["strategy_excluded_windows"]) & set(windows):
        raise ValueError("Known protocol-unsafe recovery windows must remain excluded")
    if args.limit:
        windows = windows[:args.limit]
    markets, sessions = defaultdict(dict), set()
    for record in records(dataset, "markets"):
        market = record["payload"]
        if market["asset"] != "BTC" or market["venue"] not in ("Polymarket", "Limitless"):
            raise ValueError("Unexpected universe in historical metadata")
        start = int(datetime.fromisoformat(market["start"]).timestamp())
        markets[start][market["venue"]] = market["market_id"]
        sessions.add(record["session"])
    if len(sessions) != 1:
        raise ValueError("Explicit single historical clock session required")
    session = sessions.pop()
    config = ShadowStrategyConfig(datetime(2026, 9, 10, tzinfo=timezone.utc))
    # One immutable, reproducible experiment config; runtime start is separate from creation.
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(config.to_json(), encoding="utf-8")
    all_comparisons, hashes, row_count = [], {}, 0
    jobs = [(research, output, w, manifest["hashes"][f"{w}.json.gz"], markets[w], session) for w in windows]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, (window, rows, source_hashes, count) in enumerate(pool.map(replay_job, jobs)):
            all_comparisons.extend(rows)
            hashes[f"{window}.json.gz"] = source_hashes
            row_count += count
            print(canonical(dict(completed=index + 1, total=len(windows), window=window,
                                 exact_matches=sum(r["exact_match"] for r in rows))), flush=True)
    aggregates = {}
    for ms in (100, 250):
        rows = [r for r in all_comparisons if r["scenario_ms"] == ms]
        aggregates[ms] = dict(windows=len(rows), exact_matches=sum(r["exact_match"] for r in rows),
                              old_captures=sum(r["old_captured"] for r in rows),
                              new_captures=sum(r["new_captured"] for r in rows),
                              old_net=sum((D(r["old_net"]) for r in rows), D(0)),
                              new_net=sum((D(r["new_net"]) for r in rows), D(0)),
                              old_attempts=sum(len(r["old_signals"]) for r in rows),
                              new_attempts=sum(len(r["new_signals"]) for r in rows),
                              common_attempts=sum(len(r["common_attempts"]) for r in rows),
                              common_net_mismatches=sum(not a["same_net"] for r in rows for a in r["common_attempts"]))
    summary = dict(status="COMPLETE", source_type="HISTORICAL_RECEIVE_CACHE", windows=len(windows),
                   receive_rows=row_count, repeats=2, deterministic=True, config_hash=config.config_hash,
                   scenarios=aggregates, sources=hashes, orders_sent=0, production_connected=False,
                   parity="EXACT" if all(r["exact_match"] for r in all_comparisons) else "SEMANTIC_DIFFERENCES")
    (output / "summary.json").write_text(canonical(summary), encoding="utf-8")
    print(canonical({k: v for k, v in summary.items() if k != "sources"}), flush=True)


if __name__ == "__main__":
    main()
