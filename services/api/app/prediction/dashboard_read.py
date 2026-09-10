"""Immutable local journal projection. No collector, database or execution writes."""

import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from statistics import median

from app.prediction.shadow_storage import JsonlShadowJournal
from app.prediction.target_profit import execute_leg
from app.scripts.verify_prediction_shadow import verify


def decimal_sum(values):
    return sum((D(v) for v in values), D(0))


def ratio(n, d):
    return str(D(n) / D(d)) if d else None


def category(attempt):
    if attempt["result"] == "TARGET_CAPTURED":
        return "success"
    filled = sum(D(leg["retained_q"]) > 0 for leg in attempt["legs"])
    if filled == 1:
        return "one-leg"
    if any(leg["status"] == "PARTIAL_FILL" for leg in attempt["legs"]):
        return "partial"
    return "failed"


def paginate(items, offset, limit):
    return dict(total=len(items), offset=offset, limit=limit, items=items[offset : offset + limit])


def statistics(windows, attempts):
    result = {}
    for ms in (100, 250):
        ws = sorted((w for w in windows if w["scenario_ms"] == ms), key=lambda w: w["spec"]["start"])
        ats = sorted((a for a in attempts if a["scenario_ms"] == ms), key=lambda a: a["signal_ns"])
        hours = (
            sum(
                (
                    D(
                        str(
                            (
                                datetime.fromisoformat(w["spec"]["end"]) - datetime.fromisoformat(w["spec"]["start"])
                            ).total_seconds()
                        )
                    )
                    for w in ws
                ),
                D(0),
            )
            / 3600
        )
        captures = sum(w["target_captured"] for w in ws)
        nets = [D(a["simulated_net"]) for a in ats]
        total = sum(nets, D(0))
        cumulative, running, hourly = [], D(0), defaultdict(lambda: D(0))
        for a in ats:
            running += D(a["simulated_net"])
            cumulative.append(dict(time=a["transitions"][-1]["timestamp"], value=str(running)))
        for w in ws:
            hour = datetime.fromisoformat(w["spec"]["start"]).timestamp() // 3600 * 3600
            hourly[int(hour)] += D(w["simulated_net"])
        capture_times = [D(w["capture_seconds"]) for w in ws if w["capture_seconds"] is not None]
        result[str(ms)] = dict(
            scenario_ms=ms,
            windows=len(ws),
            attempts=len(ats),
            captures=captures,
            monitored_hours=str(hours),
            valid_hours=str(sum(w["valid_coverage_ns"] for w in ws) / D(3600_000_000_000)),
            success_rate=ratio(captures, len(ws)),
            captures_per_hour=ratio(captures, hours),
            simulated_net=str(total),
            net_per_hour=ratio(total, hours),
            net_per_attempt=ratio(total, len(ats)),
            median_net=str(median(nets)) if nets else None,
            worst_attempt=str(min(nets)) if nets else None,
            one_leg_rate=ratio(sum(category(a) == "one-leg" for a in ats), len(ats)),
            two_leg_fill_rate=ratio(sum(all(x["status"] == "FULL_FILL" for x in a["legs"]) for a in ats), len(ats)),
            fill_rate=ratio(sum(D(x["filled_q"]) > 0 for a in ats for x in a["legs"]), 2 * len(ats)),
            median_capture_seconds=str(median(capture_times)) if capture_times else None,
            failures=dict(Counter(a["result"] for a in ats if a["result"] != "TARGET_CAPTURED")),
            window_failures=dict(Counter(w["failure_reason"] for w in ws if not w["target_captured"])),
            cumulative=cumulative,
            by_window=[dict(time=w["spec"]["end"], value=w["simulated_net"]) for w in ws],
            by_hour=[dict(time=t, value=str(v)) for t, v in sorted(hourly.items())],
        )
    return result


class LocalDashboardRepository:
    def __init__(self, root: Path, cache: Path):
        self.root, self.cache = root, cache
        self.windows, self.attempts, self.quality, self.sources = {}, {}, {}, {}
        self.config = None
        if not root.exists():
            self.health = dict(status="NO_DATA", source="LOCAL_HISTORICAL", research_only=True)
            return
        verify(root)
        self.summary = json.loads((root / "summary.json").read_text())
        self.config = json.loads((root / "config.json").read_text())
        for source, hashes in self.summary["sources"].items():
            epoch = source.split(".")[0]
            records = list(JsonlShadowJournal.read(root / f"{epoch}.journal.jsonl"))
            for r in records:
                p = r["payload"]
                if r["kind"] == "window":
                    wid = p["spec"]["canonical_market_id"]
                    self.windows[r["key"]] = dict(p, id=wid)
                    self.sources[wid] = (epoch, hashes)
                elif r["kind"] == "attempt":
                    self.attempts[r["key"]] = dict(p, category=category(p))
                elif r["kind"] == "quality":
                    self.quality.setdefault(p["canonical_market_id"], []).append(p)
        self.health = dict(
            status="READY",
            source="LOCAL_HISTORICAL",
            research_only=True,
            journal_verification="PASS",
            windows=len(self.sources),
            live_connected=False,
        )
        self._signal_quotes()

    def _signal_quotes(self):
        ledgers = defaultdict(dict)
        for a in sorted(self.attempts.values(), key=lambda a: a["signal_ns"]):
            ledger = ledgers[(a["window_id"], a["scenario_ms"])]
            levels, books = [], {}
            for b in a["signal_books"]:
                books[b["key"]] = dict(
                    ladder=len(levels),
                    status=b["status"],
                    book_ns=b["book_ns"],
                    min_order=D(b["min_order"]) if b["min_order"] is not None else None,
                )
                levels.append((tuple((D(p), D(q)) for p, q in b["asks"]), ()))
            snap = dict(books=books, source_ns=a["signal_ns"], ordinal=a["signal_ordinal"], quality="UNKNOWN")
            a["signal_quotes"] = {}
            for key, requested in a["requests"].items():
                fill = execute_leg(
                    snap, levels, key, a["signal_ns"], D(requested), ledger, "conservative", commit=False
                )
                a["signal_quotes"][key] = dict(
                    vwap=str(fill["vwap"]) if fill["vwap"] is not None else None,
                    requested_q=requested,
                    status=fill["status"],
                )
            for leg in a["legs"]:
                for p, q in leg["depth_consumed"]:
                    key = (leg["key"], D(p))
                    ledger[key] = ledger.get(key, D(0)) + D(q)

    def stats(self):
        return statistics(self.windows.values(), self.attempts.values())

    def window(self, wid):
        rows = [w for w in self.windows.values() if w["id"] == wid]
        if not rows:
            return None
        attempts = [a for a in self.attempts.values() if a["window_id"] == wid]
        events = []
        for a in attempts:
            events.append(
                dict(
                    timestamp=a["signal_time"],
                    monotonic_ns=a["signal_ns"],
                    state="SIGNAL_RECEIVED",
                    attempt_id=a["attempt_id"],
                    scenario_ms=a["scenario_ms"],
                )
            )
            for t in a["transitions"]:
                events.append(dict(t, attempt_id=a["attempt_id"], scenario_ms=a["scenario_ms"]))
            if a["state"] == "FAILED":
                events.append(
                    dict(
                        a["transitions"][-1],
                        state=a["result"],
                        attempt_id=a["attempt_id"],
                        scenario_ms=a["scenario_ms"],
                    )
                )
            for leg in a["legs"]:
                if leg["arrival_time"]:
                    events.append(
                        dict(
                            timestamp=leg["arrival_time"],
                            monotonic_ns=leg["ns"],
                            state=f'{leg["venue"]} {leg["status"]}',
                            attempt_id=a["attempt_id"],
                            scenario_ms=a["scenario_ms"],
                        )
                    )
        return dict(
            id=wid,
            metadata=rows[0]["spec"],
            scenarios={str(w["scenario_ms"]): w for w in rows},
            attempts=attempts,
            timeline=sorted(events, key=lambda e: e["monotonic_ns"]),
        )
