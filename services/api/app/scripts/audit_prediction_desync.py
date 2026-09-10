"""Forensic audit of every legacy DESYNC; never changes the source dataset."""

import argparse
import gzip
from collections import Counter, deque
from decimal import Decimal as D
from pathlib import Path

from app.prediction.live_storage import atomic_json, decode
from app.prediction.storage import dumps
from app.scripts.analyze_prediction import records


def audit(path):
    expected = {
        r["connection"]
        for r in records(path, "errors")
        if r["payload"].get("detail") == "Book DESYNC; fresh subscription required"
    }
    states, histories, last_valid = {}, {}, {}
    counts, cases = Counter(), []
    with gzip.open(path / "desync-forensic.jsonl.gz", "wt", encoding="utf8") as output:
        for record in records(path, "raw_ws_polymarket"):
            connection = record["connection"]
            history = histories.setdefault(connection, deque(maxlen=20))
            frame = record["payload"]["frame"]
            if frame == "PONG":
                history.append(record)
                continue
            payload = decode(frame)
            for message in payload if isinstance(payload, list) else [payload]:
                kind = message.get("event_type")
                counts[kind] += 1
                if kind not in ("book", "price_change"):
                    continue
                stamp, reasons, advertised, before, tokens = int(message["timestamp"]), [], {}, {}, set()
                changes = [message] if kind == "book" else message["price_changes"]
                counts["multi_token_payload"] += int(len({x["asset_id"] for x in changes}) > 1)
                for change in changes:
                    token = change["asset_id"]
                    tokens.add(token)
                    state = states.setdefault((connection, token), dict(bids={}, asks={}, stamp=0, initialized=False))
                    before.setdefault(
                        token,
                        dict(
                            stamp=state["stamp"],
                            bid=max(state["bids"], default=D(0)),
                            ask=min(state["asks"], default=D(1)),
                        ),
                    )
                    if kind == "price_change":
                        side = "bids" if change["side"] == "BUY" else "asks"
                        price, size = D(change["price"]), D(change["size"])
                        unchanged = state[side].get(price, D(0)) == size
                        counts["repeated_absolute_update"] += int(unchanged)
                    else:
                        unchanged = False
                    if stamp < state["stamp"]:
                        reasons.append(
                            dict(
                                reason="TIMESTAMP_REGRESSION",
                                token=token,
                                regression_ms=state["stamp"] - stamp,
                                idempotent=unchanged,
                            )
                        )
                    if kind == "book":
                        for side in ("bids", "asks"):
                            state[side] = {D(x["price"]): D(x["size"]) for x in change[side] if D(x["size"])}
                        state["initialized"] = True
                    elif state["initialized"]:
                        if size:
                            state[side][price] = size
                        else:
                            state[side].pop(price, None)
                        if "best_bid" in change:
                            advertised[token] = [D(change["best_bid"]), D(change["best_ask"])]
                    else:
                        counts["delta_before_snapshot"] += 1
                    state["stamp"] = stamp
                for token, bbo in advertised.items():
                    state = states[connection, token]
                    calculated = [max(state["bids"], default=D(0)), min(state["asks"], default=D(1))]
                    if calculated != bbo:
                        reasons.append(dict(reason="BBO_MISMATCH", token=token, calculated=calculated, advertised=bbo))
                if reasons:
                    category = "+".join(sorted({r["reason"] for r in reasons}))
                    summary = dict(
                        connection=connection,
                        raw_ordinal=record["local_ordinal"],
                        category=category,
                        timestamp=record["received_timestamp"],
                        monotonic_received_ns=record["monotonic_received_ns"],
                        reasons=reasons,
                    )
                    cases.append(summary)
                    calculated_books = {
                        t: {
                            side: [[p, q] for p, q in sorted(states[connection, t][side].items())]
                            for side in ("bids", "asks")
                        }
                        for t in tokens
                    }
                    output.write(
                        dumps(
                            dict(
                                summary,
                                before=before,
                                last_valid_bbo=last_valid.get(connection),
                                raw_frame=frame,
                                calculated_books=calculated_books,
                                previous_events=list(history),
                            )
                        )
                        + "\n"
                    )
                else:
                    last_valid.setdefault(connection, {}).update(
                        {
                            token: dict(
                                timestamp=record["received_timestamp"],
                                monotonic_ns=record["monotonic_received_ns"],
                                bid=max(states[connection, token]["bids"], default=D(0)),
                                ask=min(states[connection, token]["asks"], default=D(1)),
                            )
                            for token in tokens
                        }
                    )
            history.append(record)
    result = dict(
        expected_desync_connections=len(expected),
        cases=len(cases),
        all_legacy_events_accounted_for=expected == {c["connection"] for c in cases},
        categories=Counter(c["category"] for c in cases),
        protocol_counts=counts,
        timestamp_only_all_idempotent=sum(
            all(r.get("idempotent") for r in c["reasons"]) for c in cases if c["category"] == "TIMESTAMP_REGRESSION"
        ),
        events=cases,
    )
    atomic_json(path / "desync-audit.json", result)
    return {k: v for k, v in result.items() if k != "events"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit legacy Polymarket DESYNC from raw transport")
    parser.add_argument("directory", type=Path)
    print(dumps(audit(parser.parse_args().directory)))
