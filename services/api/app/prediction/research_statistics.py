"""Exact disk-backed quantiles and duration-integrated opportunity episodes."""

import sqlite3
from collections import Counter
from decimal import Decimal as D

THRESHOLDS = tuple(D(x) for x in ("0", ".005", ".01", ".02", ".03", ".05"))
QUANTILES = {"median": D(".5"), "p75": D(".75"), "p90": D(".9"), "p95": D(".95"), "p99": D(".99")}


class ExactStatistics:
    """Disposable analysis cache, separate from the application database; bounded Python memory."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.create_collation("DECIMAL", lambda a, b: (D(a) > D(b)) - (D(a) < D(b)))
        self.db.execute(
            "CREATE TABLE weights(scope TEXT, key TEXT, value TEXT, events INTEGER, ns INTEGER, "
            "PRIMARY KEY(scope, key, value)) WITHOUT ROWID"
        )
        self.pending = Counter()

    def add(self, scope, key, value, events=0, ns=0):
        self.pending[scope, key, str(D(value).normalize()), "events"] += events
        self.pending[scope, key, str(D(value).normalize()), "ns"] += ns
        if len(self.pending) >= 10000:
            self.flush()

    def flush(self):
        keys = {k[:3] for k in self.pending}
        self.db.executemany(
            "INSERT INTO weights VALUES(?,?,?,?,?) ON CONFLICT(scope,key,value) DO UPDATE SET "
            "events=events+excluded.events, ns=ns+excluded.ns",
            [(*k, self.pending[*k, "events"], self.pending[*k, "ns"]) for k in keys],
        )
        self.db.commit()
        self.pending.clear()

    def distribution(self, scope, key, column, positive=False):
        if column not in ("events", "ns"):
            raise ValueError(column)
        where = "scope=? AND key=?" + (" AND value COLLATE DECIMAL > '0'" if positive else "")
        total = self.db.execute(f"SELECT SUM({column}) FROM weights WHERE {where}", (scope, key)).fetchone()[0] or 0
        result = dict(weight=total, min=None, max=None, **{k: None for k in QUANTILES})
        cumulative = 0
        for value, weight in self.db.execute(
            f"SELECT value,{column} FROM weights WHERE {where} AND {column}>0 ORDER BY value COLLATE DECIMAL",
            (scope, key),
        ):
            cumulative += weight
            value = D(value)
            if result["min"] is None:
                result["min"] = value
            result["max"] = value
            for label, q in QUANTILES.items():
                if result[label] is None and cumulative >= D(total) * q:
                    result[label] = value
        return result

    def report(self, scope):
        self.flush()
        return {
            key: {
                mode + suffix: self.distribution(scope, key, column, positive)
                for mode, column in (("event_weighted", "events"), ("time_weighted", "ns"))
                for positive, suffix in ((False, ""), (True, "_positive_only"))
            }
            for (key,) in self.db.execute("SELECT DISTINCT key FROM weights WHERE scope=? ORDER BY key", (scope,))
        }

    def close(self):
        self.db.close()


class Episodes:
    def __init__(self, emit):
        self.active = {}
        self.emit = emit
        self.previous_available = set()

    def update(self, window, start_ns, end_ns, rows, reason="NO_EDGE", left_censored=False):
        available = {
            direction: {
                D(q): D(edge)
                for d, q, status, edge in rows
                if d == direction and edge is not None and status in ("OPPORTUNITY", "NO_EDGE")
            }
            for direction in ("A", "B")
        }
        candidates = {}
        for direction, edges in available.items():
            capacity = {
                str(cut): max((q for q, edge in edges.items() if edge > cut), default=D(0)) for cut in THRESHOLDS
            }
            for cut in THRESHOLDS:
                positive = {q: e for q, e in edges.items() if e > cut}
                for anchor, edge in [
                    *[(str(q), e) for q, e in positive.items()],
                    *([("ANY_Q", max(positive.values()))] if positive else []),
                ]:
                    key = (window, direction, anchor, str(cut))
                    candidates[key] = dict(
                        edge=edge,
                        capacity=capacity,
                        max_executable_q=max(edges, default=D(0)),
                        gross=max((q * e for q, e in positive.items()), default=D(0)),
                    )
        for key in list(self.active):
            if key not in candidates:
                edge_present = (
                    key[2] == "ANY_Q"
                    and bool(available[key[1]])
                    or (key[2] != "ANY_Q" and D(key[2]) in available[key[1]])
                )
                self.close(key, start_ns, reason if not edge_present else "BELOW_THRESHOLD")
        for key, value in candidates.items():
            old = self.active.get(key)
            if old is None:
                old = self.active[key] = dict(
                    window=window,
                    direction=key[1],
                    quantity=key[2],
                    threshold=D(key[3]),
                    start_ns=start_ns,
                    end_ns=start_ns,
                    integral=D(0),
                    max_edge=value["edge"],
                    capacity_by_threshold={},
                    theoretical_maximum_gross_pnl=D(0),
                    max_executable_q=D(0),
                    left_censored=left_censored or key[:3] not in self.previous_available,
                )
            dt = end_ns - start_ns
            old["integral"] += value["edge"] * dt
            old["end_ns"] = end_ns
            old["max_edge"] = max(old["max_edge"], value["edge"])
            old["theoretical_maximum_gross_pnl"] = max(old["theoretical_maximum_gross_pnl"], value["gross"])
            old["max_executable_q"] = max(old["max_executable_q"], value["max_executable_q"])
            for threshold, q in value["capacity"].items():
                old["capacity_by_threshold"][threshold] = max(old["capacity_by_threshold"].get(threshold, D(0)), q)
        self.previous_available = {(window, direction, str(q)) for direction, edges in available.items() for q in edges}
        self.previous_available.update((window, direction, "ANY_Q") for direction, edges in available.items() if edges)

    def close(self, key, end_ns, reason):
        event = self.active.pop(key)
        event["end_ns"] = end_ns
        duration = end_ns - event["start_ns"]
        event["duration_ms"] = D(duration) / 1_000_000
        event["mean_time_weighted_edge"] = event.pop("integral") / duration if duration else None
        event["close_reason"] = reason
        event["right_censored"] = reason != "BELOW_THRESHOLD"
        self.emit(event)

    def close_all(self, ns, reason):
        for key in list(self.active):
            self.close(key, ns, reason)
