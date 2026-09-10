"""Deterministic, event-ordered taker research with explicit residual inventory."""

import heapq
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING
from decimal import Decimal as D
from itertools import product

from app.prediction.execution_fees import fees, retained_fraction
from app.prediction.execution_replay import LATENCIES
from app.prediction.models import vwap

KEYS = {"A": ("Limitless:YES", "Polymarket:NO"), "B": ("Polymarket:YES", "Limitless:NO")}


@dataclass(frozen=True)
class Scenario:
    q: int = 25
    threshold: str = ".02"
    poly_ms: int = 100
    limitless_ms: int = 100
    mode: str = "parallel"
    policy: str = "force"
    loss_limit: str = ".01"
    cap: int = 0
    cooldown_ms: int = 0
    min_tte: int = 0
    ttl_ms: int = 0
    limitless_fee: str = ".03"
    policy_penalty: str = ".0025"
    family: str = "core"

    def latency(self, key):
        return self.poly_ms if key.startswith("Polymarket") else self.limitless_ms


def scenarios():
    result = [
        Scenario(q=q, threshold=t, poly_ms=p, limitless_ms=ll, mode=m)
        for q, t, (p, ll), m in product(
            (10, 25, 50, 100),
            (".005", ".01", ".02", ".03", ".05"),
            LATENCIES,
            ("parallel", "limitless_first", "polymarket_first", "liquidity_first"),
        )
    ]
    for q, m, policy, limit in product(
        (10, 25, 50, 100),
        ("limitless_first", "polymarket_first", "liquidity_first"),
        ("abort", "max_loss"),
        (".005", ".01", ".02"),
    ):
        if policy == "abort" and limit != ".01":
            continue
        result.append(Scenario(q=q, mode=m, policy=policy, loss_limit=limit, family="policy"))
    for cap in (1, 2):
        for q in (10, 25, 50, 100):
            result.append(Scenario(q=q, cap=cap, family="position_cap"))
    for cd in (250, 500, 1000, 5000):
        result.append(Scenario(cooldown_ms=cd, family="cooldown"))
    for tte in (10, 30, 60):
        for q in (10, 25, 50, 100):
            result.append(Scenario(q=q, min_tte=tte, family="expiry"))
    for q in (10, 25, 50, 100):
        result.append(Scenario(q=q, ttl_ms=2000, family="freshness"))
        result.append(Scenario(q=q, limitless_fee=".004", family="fee_lower_sensitivity"))
    return result


def empty_fill(key, ns, requested, reason):
    return dict(
        key=key,
        ns=ns,
        requested_q=requested,
        filled_q=D(0),
        retained_q=D(0),
        cost=D(0),
        cash_fee=D(0),
        contracts_fee=D(0),
        vwap=None,
        worst_price=None,
        depth_consumed=[],
        status="NO_FILL",
        reason=reason,
    )


def fill(snapshot, levels, key, ns, requested, ledger, fee_rate=D(".03"), ttl_ms=0, commit=True):
    book = snapshot.get("books", {}).get(key)
    if book is None or book["status"] != "VALID":
        return empty_fill(key, ns, requested, book["status"] if book else "MISSING")
    if snapshot["source_ns"] > ns or book["book_ns"] > ns:
        raise ValueError("Look-ahead in execution snapshot")
    if ttl_ms and ns - book["book_ns"] >= ttl_ms * 1_000_000:
        return empty_fill(key, ns, requested, "STALE_BASELINE")
    if book["min_order"] is not None and requested < D(book["min_order"]):
        return empty_fill(key, ns, requested, "BELOW_MIN_ORDER")
    remaining, taken = requested, []
    # Persistent depletion is deliberately conservative: snapshots never magically replenish fills.
    for price, size in levels[book["ladder"]][0]:
        available = max(D(0), size - ledger.get((key, price), D(0)))
        amount = min(available, remaining)
        if amount:
            taken.append((price, amount))
            remaining -= amount
        if not remaining:
            break
    total = requested - remaining
    if total == 0:
        return empty_fill(key, ns, requested, "INSUFFICIENT_DEPTH")
    cash_fee, contracts_fee = fees(key.split(":")[0], taken, fee_rate)
    cost = sum((p * q for p, q in taken), D(0))
    if commit:
        for price, amount in taken:
            ledger[key, price] = ledger.get((key, price), D(0)) + amount
    return dict(
        key=key,
        ns=ns,
        requested_q=requested,
        filled_q=total,
        retained_q=max(D(0), total - contracts_fee),
        cost=cost,
        cash_fee=cash_fee,
        contracts_fee=contracts_fee,
        vwap=cost / total,
        worst_price=taken[-1][0],
        depth_consumed=taken,
        status="FULL_FILL" if not remaining else "PARTIAL_FILL",
        reason=None if not remaining else "INSUFFICIENT_DEPTH",
        source_ordinal=snapshot["ordinal"],
        book_ns=book["book_ns"],
        minimum_verified=book["min_order"] is not None,
    )


def signal_edge(snapshot, levels, direction, q, ttl_ms=0, ns=0):
    if snapshot.get("quality") == "NOT_EQUIVALENT":
        return None
    prices = []
    for key in KEYS[direction]:
        book = snapshot.get("books", {}).get(key)
        if not book or book["status"] != "VALID" or (ttl_ms and ns - book["book_ns"] >= ttl_ms * 1_000_000):
            return None
        price = vwap(dict(levels[book["ladder"]][0]), D(q))
        if price is None:
            return None
        prices.append(price)
    return 1 - sum(prices)


def requested(target, key, fee_rate):
    return (target / retained_fraction(key.split(":")[0], fee_rate)).quantize(D(".000001"), rounding=ROUND_CEILING)


def run_window(data, config, window):
    snapshots, levels = data["snapshots"], data["levels"]
    entries = [
        e for e in data["episodes"] if int(e["quantity"]) == config.q and D(e["threshold"]) == D(config.threshold)
    ]
    events, serial, trades, ledger, active = [], 0, [], {}, set()
    fee_rate = D(config.limitless_fee)

    def push(ns, kind, payload):
        nonlocal serial
        serial += 1
        # Incoming fills/acks precede new signals at ties; venue tie order is deterministic.
        heapq.heappush(events, (ns, 1 if kind == "signal" else 0, serial, kind, payload))

    for eid, e in enumerate(entries):
        starts = [e["start_ns"]]
        if config.cooldown_ms:
            starts.extend(
                range(e["start_ns"] + config.cooldown_ms * 1_000_000, e["end_ns"], config.cooldown_ms * 1_000_000)
            )
        for ns in starts:
            push(ns, "signal", (eid, e, ns))

    def snap(ns):
        if str(ns) not in snapshots:
            raise ValueError(f"Unprepared query time {ns}; rebuild cache for this latency configuration")
        return snapshots[str(ns)]

    def execute(t, key, ns, target):
        req = requested(target, key, fee_rate)
        if ns >= t["expiry_ns"]:
            return empty_fill(key, ns, req, "EXPIRED")
        return fill(snap(ns), levels, key, ns, req, ledger, fee_rate, config.ttl_ms)

    while events:
        ns, _, _, kind, payload = heapq.heappop(events)
        if kind == "signal":
            eid, e, start = payload
            snapshot = snap(ns)
            edge = signal_edge(snapshot, levels, e["direction"], config.q, config.ttl_ms, ns)
            tte = D(e["start_time_to_expiry_seconds"]) - D(ns - e["start_ns"]) / 1_000_000_000
            if edge is None or edge <= D(config.threshold):
                continue
            t = dict(
                window=window,
                direction=e["direction"],
                episode=eid,
                signal_ns=ns,
                signal_edge=edge,
                signal_tte=tte,
                completed_episode=not e["right_censored"],
                settlement=snapshot["quality"],
                expiry_ns=e["start_ns"] + int(D(e["start_time_to_expiry_seconds"]) * 1_000_000_000),
                fills=[],
                status="PENDING",
                signal_ordinal=snapshot.get("ordinal"),
            )
            trades.append(t)
            if tte <= config.min_tte or (config.cap and len(active) >= config.cap):
                t["status"] = "TTE_FILTER" if tte <= config.min_tte else "POSITION_LIMIT"
                continue
            tid = len(trades) - 1
            active.add(tid)
            push(t["expiry_ns"], "release", tid)
            keys = KEYS[e["direction"]]
            if config.mode == "parallel":
                for key in keys:
                    push(ns + config.latency(key) * 1_000_000, "parallel", (tid, key))
            else:
                if config.mode == "liquidity_first":

                    def score(key):
                        book = snapshot["books"][key]
                        depth = sum((q for _, q in levels[book["ladder"]][0]), D(0))
                        return (depth >= requested(D(config.q), key, fee_rate), depth, -config.latency(key), key)

                    first = max(keys, key=score)
                else:
                    venue = "Limitless" if config.mode == "limitless_first" else "Polymarket"
                    first = next(k for k in keys if k.startswith(venue))
                t["first"] = first
                t["second"] = next(k for k in keys if k != first)
                push(ns + config.latency(first) * 1_000_000, "first", tid)
        elif kind == "release":
            active.discard(payload)
        elif kind == "parallel":
            tid, key = payload
            t = trades[tid]
            t["fills"].append(execute(t, key, ns, D(config.q)))
            if len(t["fills"]) == 2 and not any(f["retained_q"] for f in t["fills"]):
                active.discard(tid)
        elif kind == "first":
            t = trades[payload]
            first_fill = execute(t, t["first"], ns, D(config.q))
            t["fills"].append(first_fill)
            if first_fill["retained_q"]:
                # Confirmation travels back with the same one-way latency before second dispatch.
                push(ns + config.latency(t["first"]) * 1_000_000, "ack", payload)
            else:
                active.discard(payload)
        elif kind == "ack":
            t = trades[payload]
            f = t["fills"][0]
            req = requested(f["retained_q"], t["second"], fee_rate)
            estimate = fill(snap(ns), levels, t["second"], ns, req, ledger, fee_rate, config.ttl_ms, commit=False)
            estimated_edge = None
            if estimate["retained_q"]:
                estimated_edge = (
                    1
                    - (f["cost"] + f["cash_fee"]) / f["retained_q"]
                    - (estimate["cost"] + estimate["cash_fee"]) / estimate["retained_q"]
                    - D(config.policy_penalty)
                )
            allowed = config.policy == "force" or (
                estimated_edge is not None
                and (estimated_edge > 0 if config.policy == "abort" else estimated_edge >= -D(config.loss_limit))
            )
            if allowed:
                if config.policy != "force":
                    t["second_cost_ceiling"] = (
                        1
                        - (f["cost"] + f["cash_fee"]) / f["retained_q"]
                        - D(config.policy_penalty)
                        + (D(config.loss_limit) if config.policy == "max_loss" else D(0))
                    )
                push(ns + config.latency(t["second"]) * 1_000_000, "second", payload)
            else:
                t["fills"].append(empty_fill(t["second"], ns, req, "POLICY_ABORT"))
        elif kind == "second":
            t = trades[payload]
            if "second_cost_ceiling" in t and ns < t["expiry_ns"]:
                req = requested(t["fills"][0]["retained_q"], t["second"], fee_rate)
                preview = fill(snap(ns), levels, t["second"], ns, req, ledger, fee_rate, config.ttl_ms, commit=False)
                if preview["retained_q"]:
                    unit_cost = (preview["cost"] + preview["cash_fee"]) / preview["retained_q"]
                    rejected = (
                        unit_cost >= t["second_cost_ceiling"]
                        if config.policy == "abort"
                        else unit_cost > t["second_cost_ceiling"]
                    )
                    if rejected:
                        t["fills"].append(empty_fill(t["second"], ns, req, "PRECOMMITTED_COST_LIMIT"))
                        continue
            t["fills"].append(execute(t, t["second"], ns, t["fills"][0]["retained_q"]))

    for t in trades:
        if t["status"] != "PENDING":
            continue
        fs = t["fills"]
        if len(fs) == 1:
            missing = next(k for k in KEYS[t["direction"]] if k != fs[0]["key"])
            fs.append(empty_fill(missing, fs[0]["ns"], D(config.q), "FIRST_LEG_NO_FILL"))
        gross_matched = min(f["filled_q"] for f in fs)
        matched = min(f["retained_q"] for f in fs)
        cost = sum((f["cost"] for f in fs), D(0))
        cash = sum((f["cash_fee"] for f in fs), D(0))
        residual = sum((f["retained_q"] - matched for f in fs), D(0))
        priced = all(f["retained_q"] > 0 for f in fs)
        allocated_cost = sum(
            ((f["cost"] + f["cash_fee"]) * matched / f["retained_q"] for f in fs if f["retained_q"]), D(0)
        )
        locked = matched - allocated_cost
        execution_edge = locked / matched if matched else None
        t.update(
            matched_q=matched,
            unhedged_q=residual,
            gross_execution_pnl=gross_matched - cost,
            cash_fees=cash,
            contracts_fees=sum((f["contracts_fee"] for f in fs), D(0)),
            net_execution_pnl=None,
            fee_status="FEE_UNKNOWN",
            estimated_locked_pnl=locked,
            estimated_portfolio_floor=matched - cost - cash,
            residual_cost=cost + cash - allocated_cost,
            residual_settlement_risk="UNKNOWN",
            residual_duration_censored=bool(residual),
            execution_books_valid=all(
                snap(f["ns"]).get("books", {}).get(f["key"], {}).get("status") == "VALID" and f["ns"] < t["expiry_ns"]
                for f in fs
            ),
            execution_edge=execution_edge,
            edge_decay=t["signal_edge"] - execution_edge if execution_edge is not None else None,
            slippage=(sum((f["vwap"] for f in fs), D(0)) - (1 - t["signal_edge"]))
            if all(f["vwap"] is not None for f in fs)
            else None,
            leg_exposure_duration_ms=(D(abs(fs[0]["ns"] - fs[1]["ns"])) / 1_000_000 if priced else None),
            residual_exposure_to_expiry_ms=(
                D(max(0, t["expiry_ns"] - min(f["ns"] for f in fs if f["retained_q"]))) / 1_000_000
                if residual
                else D(0)
            ),
            status="FULL_TWO_LEG"
            if matched >= D(config.q)
            else "PARTIAL_TWO_LEG"
            if matched > 0
            else "ONE_LEG"
            if any(f["retained_q"] for f in fs)
            else "NO_FILL",
        )
        # A depth-limited bid mark is diagnostic, not an executed unwind or a risk-free valuation.
        mark, mark_q = D(0), D(0)
        terminal_ns = max(f["ns"] for f in fs)
        terminal = snap(terminal_ns)
        for f in fs:
            remaining = f["retained_q"] - matched
            b = terminal.get("books", {}).get(f["key"])
            if remaining and b and b["status"] == "VALID":
                for price, size in levels[b["ladder"]][1]:
                    amount = min(remaining, size)
                    mark += amount * price
                    mark_q += amount
                    remaining -= amount
                    if not remaining:
                        break
        t["residual_bid_mark"] = mark if mark_q == residual else None
        t["residual_marked_q"] = mark_q
        t["residual_mtm_pnl"] = mark - t["residual_cost"] if mark_q == residual else None
        t["max_residual_loss_bound"] = t["residual_cost"]
    return trades


def scenario_dict(config):
    return asdict(config)
