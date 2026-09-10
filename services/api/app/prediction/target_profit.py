"""Causal minimum-size decisions and bounded window retry policies."""

from bisect import bisect_left
from collections import deque
from dataclasses import asdict, dataclass, replace
from decimal import ROUND_CEILING
from decimal import Decimal as D
from functools import lru_cache
from itertools import product

from app.prediction.execution_simulator import KEYS, empty_fill, fill

SIZES = (5, 10, 15, 20, 25, 30, 40, 50)
TARGETS = (".25", ".50", "1", "2")
CURVE = tuple(
    (D(p), D(f))
    for p, f in (
        ("0", ".03"),
        (".50", ".03"),
        (".55", ".0252"),
        (".60", ".0213"),
        (".65", ".0180"),
        (".70", ".0151"),
        (".75", ".0126"),
        (".80", ".0105"),
        (".85", ".0085"),
        (".90", ".0068"),
        (".95", ".0053"),
        (".99", ".0042"),
        (".999", ".004"),
        ("1", ".004"),
    )
)


@dataclass(frozen=True)
class Config:
    target: str = ".50"
    fee_case: str = "conservative"
    friction: str = ".0025"
    poly_ms: int = 100
    limitless_ms: int = 100
    retry: str = "crossing"
    min_tte: int = 30
    max_tte: int = 301
    edge_cap: str = "1"
    threshold: str = "0"
    fixed_q: int = 0
    mode: str = "parallel"
    family: str = "baseline"


def configs():
    result = []
    for target, fee in product(TARGETS, ("low", "base", "conservative")):
        c = Config(target=target, fee_case=fee)
        result.append(c)
        # Predeclared one-factor sensitivities, not a Cartesian optimization search.
        for threshold, q in product((".01", ".02", ".03", ".04", ".05", ".06", ".08", ".10"), SIZES):
            result.append(replace(c, threshold=threshold, fixed_q=q, family="fixed_threshold"))
        for q in SIZES:
            result.append(replace(c, fixed_q=q, family="fixed_size_target"))
        for p, ll in ((25, 25), (50, 50), (250, 250), (500, 500), (50, 100), (100, 50), (100, 250), (250, 100)):
            result.append(replace(c, poly_ms=p, limitless_ms=ll, family="latency"))
        for retry in ("none", "two", "three"):
            result.append(replace(c, retry=retry, family="retry"))
        for tte in (240, 180, 120, 60, 10):
            result.append(replace(c, min_tte=tte, family="tte"))
        for lo, hi in ((0, 10), (10, 30), (30, 60), (60, 120), (120, 180), (180, 240), (240, 301)):
            result.append(replace(c, min_tte=lo, max_tte=hi, family="tte_bucket"))
        for friction in ("0", ".001", ".005", ".01"):
            result.append(replace(c, friction=friction, family="friction"))
        for cap in (".10", ".15", ".20"):
            result.append(replace(c, edge_cap=cap, family="outlier_cap"))
        for mode in ("polymarket_first", "limitless_first"):
            result.append(replace(c, mode=mode, family="sequential"))
    return result


@lru_cache(maxsize=4096)
def rate(price, case):
    if case != "base":
        return D(".004") if case == "low" else D(".03")
    # Official frontend non-legacy BUY branch, baseFeeBps=300 hypothesis.
    p = int(max(D(".0000001"), min(D(".9999999"), price)) * 10**18)
    return D(40 + 260 * min(p, 10**18 - p) // p) / 10000


def gross_request(snapshot, levels, key, target, case, ledger):
    if key.startswith("Polymarket"):
        return target
    book = snapshot.get("books", {}).get(key)
    if not book:
        return target
    left, gross = target, D(0)
    for p, q in levels[book["ladder"]][0]:
        available = max(D(0), q - ledger.get((key, p), D(0)))
        take = min(available, left / (1 - rate(p, case)))
        gross += take
        left -= take * (1 - rate(p, case))
        if left <= D("1e-20"):
            return gross.quantize(D(".000001"), rounding=ROUND_CEILING)
    # Request remaining size too, so insufficient depth remains PARTIAL rather than FULL.
    return (gross + left / D(".97")).quantize(D(".000001"), rounding=ROUND_CEILING)


def execute_leg(snapshot, levels, key, ns, req, ledger, case, commit=True):
    if snapshot.get("quality") == "NOT_EQUIVALENT":
        return empty_fill(key, ns, req, "NOT_EQUIVALENT")
    result = fill(snapshot, levels, key, ns, req, ledger, D(0), commit=commit)
    if key.startswith("Limitless") and result["filled_q"]:
        withheld = sum((q * rate(p, case) for p, q in result["depth_consumed"]), D(0))
        result["contracts_fee"] = withheld.quantize(D(".000001"), rounding=ROUND_CEILING)
        result["retained_q"] = result["filled_q"] - result["contracts_fee"]
    return result


def economics(fs, friction):
    matched = min(f["retained_q"] for f in fs)
    cash = sum((f["cost"] + f["cash_fee"] for f in fs), D(0))
    penalty = max(f["retained_q"] for f in fs) * D(friction)
    return dict(
        net=matched - cash - penalty,
        capital=cash,
        friction=penalty,
        matched_q=matched,
        residual_q=sum((f["retained_q"] - matched for f in fs), D(0)),
    )


def quote(snapshot, levels, direction, q, case, ledger):
    if snapshot["quality"] == "NOT_EQUIVALENT":
        return None, "NOT_EQUIVALENT"
    keys = KEYS[direction]
    if any(snapshot["books"].get(k, {}).get("status") != "VALID" for k in keys):
        return None, "stale_desync"
    asks = [levels[snapshot["books"][k]["ladder"]][0] for k in keys]
    if any(not side for side in asks):
        return None, "insufficient_depth"
    if sum(side[0][0] for side in asks) >= 1:
        return None, "edge_insufficient"
    fs = []
    for key in keys:
        req = gross_request(snapshot, levels, key, D(q), case, ledger)
        fs.append(execute_leg(snapshot, levels, key, snapshot["source_ns"], req, ledger, case, commit=False))
    if any(f["reason"] == "BELOW_MIN_ORDER" for f in fs):
        return None, "min_order"
    if any(f["status"] != "FULL_FILL" for f in fs):
        return None, "insufficient_depth"
    edge = 1 - sum((f["vwap"] for f in fs), D(0))
    return dict(
        direction=direction,
        q=q,
        edge=edge,
        requests={f["key"]: f["requested_q"] for f in fs},
        penalty_q=max(f["retained_q"] for f in fs),
        **economics(fs, "0"),
    ), None


def select(options, c):
    eligible = []
    reason = "edge_insufficient"
    friction, cap, target, threshold = D(c.friction), D(c.edge_cap), D(c.target), D(c.threshold)
    for o in options:
        if o is None:
            continue
        if c.fixed_q and o["q"] != c.fixed_q:
            continue
        net = o["net"] - o["penalty_q"] * friction
        if o["edge"] > cap:
            reason = "outlier_cap"
            continue
        if c.family == "fixed_threshold":
            allowed = o["edge"] > threshold and net > 0
        else:
            allowed = net >= target
        if allowed:
            eligible.append(dict(o, expected_net=net))
        elif o["edge"] * o["q"] >= target:
            reason = "fees_friction"
    if not eligible:
        return None, reason
    return min(eligible, key=lambda o: (o["q"], -o["expected_net"], o["residual_q"], o["direction"])), None


class Scanner:
    def __init__(self, timeline):
        self.timeline = timeline
        self.rows = list(timeline.signal_rows())
        self.quote_cache = {}
        self.ledger_cache = {}
        self.entry_cache = {}
        self.potential = []
        self.valid_times = []
        self.first_valid = None
        for row in self.rows:
            snap = timeline.snapshot(row[0])
            if len(snap["books"]) == 4 and all(b["status"] == "VALID" for b in snap["books"].values()):
                self.valid_times.append(row[0])
                if self.first_valid is None:
                    self.first_valid = row[1]
            for keys in KEYS.values():
                bs = [snap["books"].get(k) for k in keys]
                if all(b and b["status"] == "VALID" for b in bs):
                    sides = [timeline.levels[b["ladder"]][0] for b in bs]
                    if all(sides) and sum(s[0][0] for s in sides) < 1:
                        self.potential.append((row, snap))
                        break
        self.valid_set = set(self.valid_times)

    def candidates(self, c):
        key = (
            c.target,
            c.fee_case,
            c.friction,
            c.min_tte,
            c.max_tte,
            c.edge_cap,
            c.threshold,
            c.fixed_q,
            c.family == "fixed_threshold",
        )
        if key in self.entry_cache:
            return self.entry_cache[key]
        entries, reasons = [], set()
        qualified = set()
        for row, snap in self.potential:
            tte = D((self.timeline.window + 300) * 1_000_000 - row[1]) / 1_000_000
            if tte <= c.min_tte or tte > c.max_tte:
                reasons.add("TTE_filter")
                continue
            options, problems = self.options(snap, c.fee_case, {})
            chosen, reason = select(options, c)
            reasons.update(problems)
            if chosen or D(c.edge_cap) < 1:
                entries.append((row, chosen))
                qualified.add(row[0])
            elif reason:
                reasons.add(reason)
        qualifying_valid = sorted(qualified & self.valid_set)
        result = entries, qualifying_valid, reasons
        if not c.fixed_q:
            if len(self.entry_cache) >= 32:
                self.entry_cache.clear()
            self.entry_cache[key] = result
        return result

    def options(self, snapshot, case, ledger, fixed_q=0):
        signature = tuple((k, b["ladder"], b["status"], b["min_order"]) for k, b in snapshot["books"].items())
        cache_key = (snapshot["quality"], signature, case)
        if not ledger and cache_key in self.quote_cache:
            return self.quote_cache[cache_key]
        ledger_key = (cache_key, tuple(sorted(ledger.items())), fixed_q)
        if ledger and ledger_key in self.ledger_cache:
            return self.ledger_cache[ledger_key]
        options, reasons = [], set()
        sizes = (fixed_q,) if ledger and fixed_q else SIZES
        for q, direction in product(sizes, ("A", "B")):
            value, reason = quote(snapshot, self.timeline.levels, direction, q, case, ledger)
            options.append(value)
            if reason:
                reasons.add(reason)
        result = (options, reasons)
        if not ledger:
            if len(self.quote_cache) >= 50000:
                self.quote_cache.clear()
            self.quote_cache[cache_key] = result
        else:
            if len(self.ledger_cache) > 2000:
                self.ledger_cache.clear()
            self.ledger_cache[ledger_key] = result
        return result


def attempt(timeline, row, chosen, c, ledger):
    ns, utc_us = row[:2]
    expiry = ns + ((timeline.window + 300) * 1_000_000 - utc_us) * 1000
    fs = []
    keys = list(KEYS[chosen["direction"]])

    def latency(key):
        return (c.poly_ms if key.startswith("Polymarket") else c.limitless_ms) * 1_000_000

    def do(key, arrival, req):
        if arrival >= expiry:
            return empty_fill(key, arrival, req, "EXPIRED")
        return execute_leg(timeline.snapshot(arrival), timeline.levels, key, arrival, req, ledger, c.fee_case)

    requests = chosen["requests"]
    if c.mode == "parallel":
        for key in sorted(keys, key=lambda k: (latency(k), k)):
            fs.append(do(key, ns + latency(key), requests[key]))
        done = ns + 2 * max(latency(k) for k in keys)
    else:
        venue = "Polymarket" if c.mode == "polymarket_first" else "Limitless"
        keys.sort(key=lambda k: not k.startswith(venue))
        first = do(keys[0], ns + latency(keys[0]), requests[keys[0]])
        fs.append(first)
        ack = ns + 2 * latency(keys[0])
        req = gross_request(timeline.snapshot(ack), timeline.levels, keys[1], first["retained_q"], c.fee_case, ledger)
        fs.append(
            do(keys[1], ack + latency(keys[1]), req) if req else empty_fill(keys[1], ack, req, "FIRST_LEG_NO_FILL")
        )
        done = ack + 2 * latency(keys[1])
    value = economics(fs, c.friction)
    full = all(f["status"] == "FULL_FILL" for f in fs)
    one_leg = sum(f["retained_q"] > 0 for f in fs) == 1
    valid = all(f["retained_q"] > 0 for f in fs)
    reason = (
        "one_leg_failure"
        if one_leg
        else "stale_desync"
        if not valid
        else "insufficient_depth"
        if not full
        else "latency_decay"
    )
    return dict(
        signal_ns=ns,
        signal_ordinal=row[2],
        direction=chosen["direction"],
        q=chosen["q"],
        signal_edge=chosen["edge"],
        expected_net=chosen["expected_net"],
        fills=fs,
        execution_edge=(1 - sum(f["vwap"] for f in fs)) if valid else None,
        done_ns=done,
        capture_seconds=D(utc_us - timeline.window * 1_000_000) / 1_000_000 + D(done - ns) / 1_000_000_000,
        full=full,
        one_leg=one_leg,
        reason=reason,
        **value,
    )


def run(scanner, c):
    timeline = scanner.timeline
    ledger, trades = {}, []
    entries, qualifying_valid, cached_reasons = scanner.candidates(c)
    reasons = set(cached_reasons)
    armed, blocked_until, cumulative, captured = True, 0, D(0), False
    limit = {"none": 1, "two": 2, "three": 3, "crossing": 3}[c.retry]
    pending = deque(entries)
    for _ in range(len(entries) + limit):
        if not pending:
            break
        row, chosen = pending.popleft()
        ns, utc_us = row[:2]
        if ns < blocked_until:
            continue
        tte = D((timeline.window + 300) * 1_000_000 - utc_us) / 1_000_000
        snap = timeline.snapshot(ns)
        all_valid = len(snap["books"]) == 4 and all(b["status"] == "VALID" for b in snap["books"].values())
        if tte <= c.min_tte or tte > c.max_tte:
            reasons.add("TTE_filter")
            continue
        if ledger or chosen is None:
            options, problems = scanner.options(snap, c.fee_case, ledger, c.fixed_q)
            chosen, reason = select(options, c)
            reasons.update(problems)
        else:
            reason = None
        if chosen is None:
            reasons.add(reason)
            if all_valid:
                armed = True
            continue
        valid_count = bisect_left(scanner.valid_times, ns) - bisect_left(scanner.valid_times, blocked_until)
        qualified_count = bisect_left(qualifying_valid, ns) - bisect_left(qualifying_valid, blocked_until)
        if valid_count > qualified_count:
            armed = True
        if c.retry == "crossing" and not armed:
            continue
        trade = attempt(timeline, row, chosen, c, ledger)
        trades.append(trade)
        cumulative += trade["net"]
        blocked_until = trade["done_ns"]
        armed = False
        captured = (
            trade["full"]
            and trade["residual_q"] <= D(".01")
            and trade["net"] >= D(c.target)
            and cumulative >= D(c.target)
        )
        if captured or len(trades) >= limit:
            break
        if c.retry in ("two", "three"):
            index = bisect_left(timeline.times, blocked_until)
            if index < len(timeline.rows):
                pending.appendleft((timeline.rows[index], None))
    if captured:
        reason = ""
    elif trades:
        reason = trades[-1]["reason"]
        if trades[-1]["net"] >= D(c.target):
            reason = "prior_losses_or_residual"
    else:
        priority = (
            "fees_friction",
            "edge_insufficient",
            "min_order",
            "insufficient_depth",
            "outlier_cap",
            "stale_desync",
            "TTE_filter",
        )
        reason = next(
            (r for r in priority if r in reasons),
            "edge_insufficient" if scanner.first_valid else "no_valid_opportunity",
        )
    return dict(
        window=timeline.window,
        **asdict(c),
        captured=captured,
        first_valid_us=scanner.first_valid,
        attempts=len(trades),
        failed_attempts=sum(t["net"] < D(c.target) or not t["full"] for t in trades),
        net=cumulative,
        one_leg_attempts=sum(t["one_leg"] for t in trades),
        failure_reason=reason,
        diagnostic_reasons=sorted(reasons),
        trades=trades,
    )


def fixed_target_prefix(high_result, config):
    """Entry is target-independent for fixed thresholds; lower targets use a causal prefix."""
    if config.family != "fixed_threshold" or high_result["family"] != "fixed_threshold":
        raise ValueError("Only fixed-threshold entries are independent of target")
    if D(config.target) > D(high_result["target"]):
        raise ValueError("Cannot extend a stopped strategy using a prefix")
    total, captured, trades = D(0), False, []
    for t in high_result["trades"]:
        trades.append(t)
        total += t["net"]
        captured = (
            t["full"] and t["residual_q"] <= D(".01") and t["net"] >= D(config.target) and total >= D(config.target)
        )
        if captured:
            break
    reason = "" if captured else high_result["failure_reason"]
    if trades and not captured:
        reason = "prior_losses_or_residual" if trades[-1]["net"] >= D(config.target) else trades[-1]["reason"]
    return dict(
        high_result,
        **asdict(config),
        captured=captured,
        trades=trades,
        net=total,
        attempts=len(trades),
        failed_attempts=sum(t["net"] < D(config.target) or not t["full"] for t in trades),
        one_leg_attempts=sum(t["one_leg"] for t in trades),
        failure_reason=reason,
    )
