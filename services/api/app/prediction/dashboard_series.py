"""Bounded visualization from hash-pinned cache; exact invalid intervals kept separately."""

import gzip
import hashlib
import json
from bisect import bisect_right
from decimal import Decimal as D
from functools import lru_cache

from app.prediction.execution_simulator import KEYS as DIRECTIONS
from app.prediction.shadow_models import KEYS, utc_from_us
from app.prediction.target_profit import economics, execute_leg, gross_request


@lru_cache(maxsize=4)
def project_series(path, expected_hash, points=601):
    with path.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != expected_hash:
            raise ValueError("Historical cache hash mismatch")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    rows = data["rows"]
    if not rows:
        return dict(points=[], quality_events=[], books={}, status="EMPTY")
    levels = [(tuple((D(p), D(q)) for p, q in ladder[0]), ()) for ladder in data["levels"]]
    times = [r[0] for r in rows]
    anchor_ns, anchor_us = rows[0][:2]
    end_ns = anchor_ns + ((data["window"] + 300) * 1_000_000 - anchor_us) * 1000

    def timestamp(ns):
        return utc_from_us(anchor_us + (ns - anchor_ns) // 1000).isoformat()

    def snapshot(row, ns):
        books = {}
        for key, ref in zip(KEYS, row[4:]):
            if ref >= 0:
                ladder, status, book_ns, deadline, minimum = data["books"][ref]
                books[key] = dict(
                    ladder=ladder,
                    status="STALE" if status == "VALID" and ns >= deadline else status,
                    book_ns=book_ns,
                    min_order=D(minimum) if minimum is not None else None,
                )
        return dict(source_ns=row[0], ordinal=row[2], quality=row[3], books=books)

    # Scan status/deadlines only at receive boundaries, including expiries between messages.
    intervals = []
    for index, row in enumerate(rows):
        start, stop = row[0], min(times[index + 1] if index + 1 < len(rows) else end_ns, end_ns)
        if start >= stop:
            continue
        refs = row[4:]
        valid = row[3] != "NOT_EQUIVALENT" and all(ref >= 0 and data["books"][ref][1] == "VALID" for ref in refs)
        deadline = min(data["books"][ref][3] for ref in refs) if valid else start
        invalid_start = max(start, deadline) if valid else start
        if invalid_start < stop:
            reason = "STALE" if valid else "INVALID_OR_DESYNC"
            if intervals and intervals[-1][1] == invalid_start and intervals[-1][2] == reason:
                intervals[-1] = (intervals[-1][0], stop, reason)
            else:
                intervals.append((invalid_start, stop, reason))
    values, last_books, segment, previous_ns = [], {}, 0, anchor_ns
    interval_ends = [i[1] for i in intervals]
    for index in range(points):
        ns = anchor_ns + (end_ns - anchor_ns - 1) * index // (points - 1)
        row = rows[bisect_right(times, ns) - 1]
        snap = snapshot(row, ns)
        interval_index = bisect_right(interval_ends, previous_ns)
        if interval_index < len(intervals) and intervals[interval_index][0] <= ns:
            segment += 1
        previous_ns = ns
        valid = len(snap["books"]) == 4 and all(b["status"] == "VALID" for b in snap["books"].values())
        valid = valid and snap["quality"] != "NOT_EQUIVALENT"
        point = dict(time=timestamp(ns), segment=segment, valid=valid, prices={}, edges={}, predicted_net={})
        last_books = {}
        for key in KEYS:
            b = snap["books"].get(key)
            asks = levels[b["ladder"]][0] if b else ()
            available = b is not None and b["status"] == "VALID"
            price = str(asks[0][0]) if asks and available else None
            point["prices"][key] = price if valid else None
            last_books[key] = dict(
                ask=price,
                bid=None,
                bid_status="NOT_RECORDED",
                status=b["status"] if b else "MISSING",
                depth=str(sum((q for _, q in asks), D(0))) if available else None,
                depth_scope="cached ask prefix, at least 200 gross shares when available",
            )
        if valid:
            for direction, keys in DIRECTIONS.items():
                fills = [
                    execute_leg(
                        snap,
                        levels,
                        key,
                        ns,
                        gross_request(snap, levels, key, D(10), "conservative", {}),
                        {},
                        "conservative",
                        commit=False,
                    )
                    for key in keys
                ]
                if all(f["status"] == "FULL_FILL" for f in fills):
                    point["edges"][direction] = str(1 - sum(f["vwap"] for f in fills))
                    point["predicted_net"][direction] = str(economics(fills, D(".0025"))["net"])
        values.append(point)
    return dict(
        status="READY",
        points=values,
        books=last_books,
        as_of=values[-1]["time"],
        quality_events=[
            dict(start=timestamp(a), end=timestamp(b), reason=r, start_ns=str(a), end_ns=str(b))
            for a, b, r in intervals
        ],
        source_rows=len(rows),
        sampling="601 evenly spaced receive-state samples; gaps split lines; not extrema",
        edge_basis="Q10 gross requests, no own depletion; exact attempt edges in journal",
    )
