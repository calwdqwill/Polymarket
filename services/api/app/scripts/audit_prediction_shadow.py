"""Explain changed historical signals using only the forward prefix at each baseline signal."""

import argparse
import gzip
import json
from decimal import Decimal as D
from pathlib import Path

from app.prediction.shadow_engine import ShadowEngine
from app.prediction.shadow_models import EventKind, ShadowStrategyConfig, canonical
from app.prediction.shadow_replay import cache_events, spec_for
from app.prediction.shadow_storage import InMemoryShadowJournal, JsonlShadowJournal


def audit_window(research, output, window):
    comparisons = json.loads((output / f"{window}.comparison.json").read_text())
    mismatched = [r for r in comparisons if not r["exact_match"]]
    if not mismatched:
        return []
    config = ShadowStrategyConfig.from_json((output / "config.json").read_text())
    rows = list(JsonlShadowJournal.read(output / f"{window}.journal.jsonl"))
    saved_window = next(r["payload"] for r in rows if r["kind"] == "window")
    with gzip.open(research / "cache" / f"{window}.json.gz", "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    targets = {}
    for row in mismatched:
        new_signals = row["new_signals"]
        for ns, direction in row["old_signals"]:
            if [ns, direction] not in new_signals:
                targets.setdefault(ns, []).append((row["scenario_ms"], direction))
    spec = spec_for(window, dict(saved_window["spec"]["market_ids"]), saved_window["spec"]["match_quality"])
    engine = ShadowEngine(config, InMemoryShadowJournal())
    result = []
    for event in cache_events(data, spec, "audit-prefix"):
        engine.on_event(event)
        if event.kind == EventKind.BOOK_VALID and event.monotonic_ns in targets:
            statuses = {k: b.status_at(engine.now_ns) for k, b in engine.books.items()}
            snapshot, levels = engine._snapshot(engine.now_ns)
            for ms, direction in targets.pop(event.monotonic_ns):
                ledger = engine.ledgers[ms]
                ledger_quotes = engine._quotes(snapshot, levels, ledger.consumed)
                quote = next((q for q in ledger_quotes if q["direction"] == direction), None)
                if not engine._valid(engine.now_ns):
                    reason = "ALL_FOUR_BOOKS_VALID_REQUIRED"
                elif ledger.pending:
                    reason = "PREVIOUS_ATTEMPT_PENDING_OR_SIGNAL_SHIFT"
                elif ledger.window.target_captured:
                    reason = "WINDOW_ALREADY_CAPTURED"
                elif not ledger.armed:
                    reason = "NO_NEW_OBSERVED_BELOW_CROSSING_AFTER_ACK_OR_GAP"
                elif quote is None:
                    reason = "INSUFFICIENT_DEPTH_OR_MINIMUM_UNDER_INDEPENDENT_LEDGER"
                elif quote["edge"] > D(".15"):
                    reason = "OUTLIER_CAP_UNDER_INDEPENDENT_LEDGER"
                elif quote["edge"] <= D(".10"):
                    reason = "BELOW_THRESHOLD_UNDER_INDEPENDENT_LEDGER"
                elif quote["net"] <= 0:
                    reason = "NONPOSITIVE_NET_UNDER_INDEPENDENT_LEDGER"
                else:
                    reason = "UNEXPLAINED"
                result.append(dict(window=window, scenario_ms=ms, baseline_signal_ns=event.monotonic_ns,
                                   direction=direction, reason=reason, statuses=statuses, armed=ledger.armed,
                                   attempts=len(ledger.window.attempts),
                                   observed=engine._quotes(snapshot, levels, {}),
                                   ledger_quotes=ledger_quotes))
        if not targets:
            break
    if targets:
        raise ValueError("Baseline signal missing from receive stream")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("research", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summary = json.loads((args.output / "summary.json").read_text())
    if summary["status"] != "COMPLETE":
        raise ValueError("Replay must finish before the audit")
    results = []
    for source in summary["sources"]:
        window = int(source.split(".")[0])
        results.extend(audit_window(args.research, args.output, window))
    # This report is evidence at rejected old signals, not a counterfactual PnL attribution.
    (args.output / "parity-audit.json").write_text(canonical(results), encoding="utf-8")
    print(canonical(dict(audited_rejected_signals=len(results), unexplained=sum(
        r["reason"] == "UNEXPLAINED" for r in results))), flush=True)


if __name__ == "__main__":
    main()
