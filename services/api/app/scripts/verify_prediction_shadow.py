"""Read-only independent arithmetic/causality checks for completed shadow replay journals."""

import argparse
import hashlib
import json
from decimal import ROUND_CEILING
from decimal import Decimal as D
from pathlib import Path

from app.prediction.shadow_models import ShadowStrategyConfig, canonical
from app.prediction.shadow_replay import compact_results
from app.prediction.shadow_storage import JsonlShadowJournal


class ReadJournal:
    def __init__(self, path):
        self.path = path

    def records(self, kind=None):
        return JsonlShadowJournal.read(self.path, kind)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(path):
    summary = json.loads((path / "summary.json").read_text())
    require(summary["status"] == "COMPLETE" and summary["deterministic"], "Incomplete/non-deterministic run")
    config = ShadowStrategyConfig.from_json((path / "config.json").read_text())
    require(config.config_hash == summary["config_hash"], "Wrong experiment config")
    counts = dict(windows=0, attempts=0, legs=0, captured=0)
    for source, hashes in sorted(summary["sources"].items()):
        window_id = source.split(".")[0]
        journal = ReadJournal(path / f"{window_id}.journal.jsonl")
        digest = hashlib.sha256(canonical(list(journal.records())).encode()).hexdigest()
        require(digest == hashes["journal_sha256"] == hashes["repeat_sha256"], "Journal digest mismatch")
        result = compact_results(journal)
        for w in result["windows"]:
            counts["windows"] += 1
            require(w["closed_at"] is not None and w["close_reason"] == "WINDOW_END", "Incomplete window")
            require(0 <= w["valid_coverage_ns"] <= 300_000_000_000, "Coverage outside window")
            attempts = [a for a in result["attempts"] if a["scenario_ms"] == w["scenario_ms"]]
            require(len(attempts) <= 3 and w["attempts"] == [a["attempt_id"] for a in attempts], "Attempt cap/IDs")
            require(sum(a["result"] == "TARGET_CAPTURED" for a in attempts) <= 1, "More than one capture")
            cumulative = D(0)
            for a in attempts:
                counts["attempts"] += 1
                require(D(a["q"]) == 10 and a["config_hash"] == config.config_hash, "Q/config drift")
                require(D(".10") < D(a["signal_edge"]) <= D(".15"), "Signal edge guard")
                require(D(a["predicted_net"]) > 0 and D(a["tte"]) > 30, "Net/TTE guard")
                require(len(a["legs"]) == 2 and len(a["signal_books"]) == 2, "Missing legs")
                for book in a["signal_books"]:
                    require(book["status"] == "VALID" and book["book_ns"] <= a["signal_ns"], "Future/invalid signal")
                    require(a["signal_ns"] < min(book["freshness_deadline_ns"], book["connection_deadline_ns"]), "Stale signal")
                fills = a["legs"]
                for leg in fills:
                    counts["legs"] += 1
                    require(leg["ns"] == a["signal_ns"] + a["scenario_ms"] * 1_000_000, "Wrong arrival clock")
                    gross, retained = D(leg["filled_q"]), D(leg["retained_q"])
                    require(0 <= retained <= gross <= D(leg["requested_q"]), "Invalid fill size")
                    levels = [(D(p), D(q)) for p, q in leg["depth_consumed"]]
                    require(sum((q for _, q in levels), D(0)) == gross, "Depth/gross mismatch")
                    cost = sum((p * q for p, q in levels), D(0))
                    require(cost == D(leg["cost"]), "Cost mismatch")
                    if gross:
                        require(leg["book_ns"] <= leg["ns"] and leg["arrival_book"]["status"] == "VALID", "Future fill")
                        book = leg["arrival_book"]
                        require(leg["ns"] < min(book["freshness_deadline_ns"], book["connection_deadline_ns"]), "Stale fill")
                        require(D(leg["vwap"]) == cost / gross and D(leg["worst_price"]) == levels[-1][0], "Fill prices")
                    if leg["venue"] == "Polymarket":
                        fee = sum((q * D(".07") * p * (1 - p) for p, q in levels), D(0)).quantize(D(".00001"), rounding=ROUND_CEILING)
                        require(D(leg["cash_fee"]) == fee and D(leg["contracts_fee"]) == 0, "Poly fees")
                    else:
                        withheld = (gross * D(".03")).quantize(D(".000001"), rounding=ROUND_CEILING)
                        require(D(leg["contracts_fee"]) == withheld and D(leg["cash_fee"]) == 0, "LL fees")
                    require(retained == gross - D(leg["contracts_fee"]), "Retained contracts mismatch")
                matched = min(D(f["retained_q"]) for f in fills)
                residual = sum((D(f["retained_q"]) - matched for f in fills), D(0))
                friction = max(D(f["retained_q"]) for f in fills) * D(".0025")
                cash = sum((D(f["cost"]) + D(f["cash_fee"]) for f in fills), D(0))
                net = matched - cash - friction
                require(D(a["matched_q"]) == matched and D(a["residual_q"]) == residual, "Inventory mismatch")
                require(D(a["friction"]) == friction and D(a["simulated_net"]) == net, "Net mismatch")
                cumulative += net
                stamps = [t["monotonic_ns"] for t in a["transitions"]]
                require(stamps == sorted(stamps), "Transition order")
                require(stamps[-1] == a["signal_ns"] + 2 * a["scenario_ms"] * 1_000_000, "ACK clock")
                if a["result"] == "TARGET_CAPTURED":
                    counts["captured"] += 1
                    require(all(f["status"] == "FULL_FILL" for f in fills), "Partial capture")
                    require(net >= D(".5") and cumulative >= D(".5") and residual <= D(".01"), "Invalid capture")
                    require(a is attempts[-1] and w["target_captured"], "Capture did not stop window")
            require(cumulative == D(w["simulated_net"]), "Window lost prior attempt costs")
    require(counts["windows"] == summary["windows"] * 2, "Missing scenario window")
    return dict(status="PASS", **counts, config_hash=config.config_hash,
                source="HISTORICAL_JOURNAL", checks="hashes, causality, sizes, prices, fees, inventory, PnL, capture, coverage")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify(args.output.resolve())
    (args.output / "verification.json").write_text(canonical(result), encoding="utf-8")
    print(canonical(result))


if __name__ == "__main__":
    main()
