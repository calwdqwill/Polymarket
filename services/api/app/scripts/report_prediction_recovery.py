"""Report recovered research with fixed-size, one-trade-per-episode scenarios."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from app.prediction.live_storage import atomic_json
from app.scripts.analyze_prediction import records
from app.scripts.analyze_prediction_research import TTE

LATENCIES = (25, 50, 100, 250, 500)
LIFETIME_BINS = (
    (10, "<10"),
    (25, "10-25"),
    (50, "25-50"),
    (100, "50-100"),
    (250, "100-250"),
    (500, "250-500"),
    (1000, "500-1000"),
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_float=D)


def number(value, scale=1):
    return "—" if value is None else f"{D(value) * scale:.4f}"


def quantiles(values):
    values = sorted(values)
    if not values:
        return dict(count=0, median=None, p75=None, p90=None, p95=None, p99=None, max=None)
    return dict(
        count=len(values),
        max=values[-1],
        **{
            label: values[max(0, int((D(len(values)) * q).to_integral_value(rounding="ROUND_CEILING")) - 1)]
            for label, q in (
                ("median", D(".5")),
                ("p75", D(".75")),
                ("p90", D(".9")),
                ("p95", D(".95")),
                ("p99", D(".99")),
            )
        },
    )


def scenario(events, quantity, hours):
    gross = sum((D(e["max_edge"]) * quantity for e in events), D(0))
    return dict(
        episodes=len(events),
        trades_per_hour=D(len(events)) / hours,
        gross_theoretical_pnl=gross,
        gross_per_hour=gross / hours,
        gross_per_day_extrapolation=gross / hours * 24,
    )


def build(root, decision="MORE DATA REQUIRED", decision_reason=None):
    case_manifest = read(root / "frozen-manifest.json")
    if Path(case_manifest["source"]).name != "research-20260909T151749Z":
        raise ValueError("This forensic report is specific to research-20260909T151749Z; review a new case separately")
    path = root / "dataset"
    data = read(path / "research-analysis.json")
    replay = read(path / "replay.json")
    observation_replay = read(path / "observation-replay.json")
    if not all(v["consistent"] for v in replay.values()) or not observation_replay["consistent"]:
        raise ValueError("Replay mismatch: strategy report is blocked")
    if not data.get("full_windows_only"):
        raise ValueError("Main strategy statistics must use complete windows only")
    replay["observations"] = observation_replay
    replay["observations"]["all_windows_safety_qualified"] = not observation_replay["strategy_excluded_windows"]
    atomic_json(root / "replay.json", replay)
    run, final = read(path / "run.json"), read(path / "live.json")
    manifest, tails = case_manifest, read(root / "tail-recovery.json")
    full = {int(w): v for w, v in data["windows"].items() if v["strategy_window"]}
    calendar_full = {int(w): v for w, v in data["windows"].items() if v["full_window"]}
    hours = D(data["strategy_duration_seconds"]) / 3600
    groups, tte_counts, tte_completed_counts, by_window = defaultdict(list), Counter(), Counter(), defaultdict(Counter)
    if not (root / "episode-timestamp-repair.json").exists():
        raise ValueError("Correct episode UTC labels before generating the TTE report")
    for record in records(root, "opportunities"):
        e = record
        if int(e["window"]) not in full:
            raise ValueError("Partial-window episode leaked into the primary sample")
        key = f"{e['direction']}:{e['quantity']}:{e['threshold']}"
        groups[key].append(e)
        tte = D(e["start_time_to_expiry_seconds"])
        bucket = next((label for lower, label in TTE if tte > lower), "<10")
        if D(e["threshold"]) == 0:
            tte_counts[f"{e['direction']}:{e['quantity']}:{bucket}"] += 1
            completed = not e["left_censored"] and not e["right_censored"]
            if completed:
                tte_completed_counts[f"{e['direction']}:{e['quantity']}:{bucket}"] += 1
            w = by_window[e["window"]]
            if e["quantity"] == "ANY_Q":
                w["observed_fragment_count"] += 1
                w["max_edge"] = max(w["max_edge"], D(e["max_edge"]))
                if completed:
                    w["episode_count"] += 1
                    w["gross_ANY_Q"] += D(e["theoretical_maximum_gross_pnl"])
            if completed and e["quantity"] in ("10", "25", "50"):
                w["gross_Q" + e["quantity"]] += D(e["quantity"]) * D(e["max_edge"])
    scenarios, lifetimes, latency = {}, {}, {}
    for direction in ("A", "B"):
        for q in ("10", "25", "50", "100", "250", "500", "1000"):
            key = direction + ":" + q
            events = groups[key + ":0"]
            completed = [e for e in events if not e["left_censored"] and not e["right_censored"]]
            censored = [e for e in events if e["left_censored"] or e["right_censored"]]
            lifetimes[key] = {
                category: quantiles([D(e["duration_ms"]) for e in subset])
                for category, subset in (("completed", completed), ("censored", censored))
            }
            if q in ("10", "25", "50"):
                scenarios[key] = {
                    category: scenario(subset, D(q), hours)
                    for category, subset in (("all_observed", events), ("completed", completed), ("censored", censored))
                }
                latency[key] = {}
                for category, subset in (("completed", completed), ("censored", censored)):
                    bins = Counter()
                    for e in subset:
                        label = next((label for upper, label in LIFETIME_BINS if D(e["duration_ms"]) < upper), ">=1000")
                        bins[label] += 1
                    total_gross = sum((D(q) * D(e["max_edge"]) for e in subset), D(0))
                    latency[key][category] = dict(
                        lifetime_buckets=bins,
                        budgets={
                            str(ms): dict(
                                surviving_episodes=sum(D(e["duration_ms"]) >= ms for e in subset),
                                gross_opportunity_share=(
                                    sum((D(q) * D(e["max_edge"]) for e in subset if D(e["duration_ms"]) >= ms), D(0))
                                    / total_gross
                                    if total_gross
                                    else None
                                ),
                            )
                            for ms in LATENCIES
                        },
                    )
    distribution = {}
    for direction in ("A", "B", "BOTH"):
        for q in ("10", "25", "50", "100", "250", "500", "1000", "ANY_Q"):
            for threshold in ("0", "0.005", "0.01", "0.02", "0.03", "0.05"):
                subset = [
                    e
                    for d in (("A", "B") if direction == "BOTH" else (direction,))
                    for e in groups[f"{d}:{q}:{threshold}"]
                ]
                present = sorted({e["window"] for e in subset})
                distribution[f"{direction}:{q}:{threshold}"] = dict(
                    windows=present,
                    count=len(present),
                    share=D(len(present)) / len(full),
                    episodes_per_market=D(len(subset)) / len(full),
                    completed_windows=sorted(
                        {e["window"] for e in subset if not e["left_censored"] and not e["right_censored"]}
                    ),
                    completed_count=sum(not e["left_censored"] and not e["right_censored"] for e in subset),
                )
    tte = {
        key: dict(
            valid_seconds=D(v["time_weighted"]["weight"]) / 1_000_000_000,
            positive_time_pct=D(v["time_weighted_positive_only"]["weight"]) / v["time_weighted"]["weight"] * 100
            if v["time_weighted"]["weight"]
            else None,
            episodes_started=tte_counts[key],
            completed_episodes_started=tte_completed_counts[key],
            median_positive_edge=v["time_weighted_positive_only"]["median"],
            max_edge=v["time_weighted"]["max"],
        )
        for key, v in data["time_to_expiry"].items()
        if key.split(":")[1] in ("10", "25", "50")
    }
    rankings = {
        metric: [
            dict(window=w, **by_window[w]) for w in sorted(full, key=lambda w: by_window[w][metric], reverse=True)[:20]
        ]
        for metric in ("max_edge", "episode_count", "gross_ANY_Q", "gross_Q10", "gross_Q25", "gross_Q50")
    }
    errors = Counter()
    lags, per_window_diag, connection_counts = [], defaultdict(Counter), Counter()
    for record in records(path, "errors"):
        errors[record["payload"].get("detail", "UNKNOWN")] += 1
    for record in records(path, "processing_lag"):
        value = D(record["payload"]["timer_lag_ms"])
        lags.append(value)
        w = int(datetime.fromisoformat(record["received_timestamp"]).timestamp()) // 300 * 300
        per_window_diag[w]["lag_events_gt_100ms"] += 1
        per_window_diag[w]["max_lag_ms"] = max(per_window_diag[w]["max_lag_ms"], value)
    for record in records(path, "connections"):
        p = record["payload"]
        if p["event"] == "CONNECTED":
            connection_counts[p["venue"], p["market"]] += 1
    reconnects = Counter()
    for (venue, market), count in connection_counts.items():
        reconnects[venue] += max(0, count - 1)
        w = int(market.rsplit("-", 1)[1])
        per_window_diag[w][venue + "_reconnects"] += max(0, count - 1)

    def quality_counts(counts):
        total = D(counts["total_monitoring_ns"])
        return {
            key.removesuffix("_ns") + "_pct": D(value) / total * 100
            for key, value in counts.items()
            if key.endswith("_ns") and not key.startswith("total_")
        }

    accepted_counts, strategy_counts = Counter(), Counter()
    for w, v in data["windows"].items():
        numeric = {k: n for k, n in v.items() if k.endswith("_ns")}
        if not v["replay_excluded"]:
            accepted_counts.update(numeric)
        if v["strategy_window"]:
            strategy_counts.update(numeric)
    quality = dict(
        monitoring_duration_seconds=data["duration_seconds"],
        complete_window_count=len(calendar_full),
        strategy_window_count=len(full),
        strategy_duration_seconds=data["strategy_duration_seconds"],
        analysis_cutoff_timestamp=final["timestamp"],
        strategy_cutoff_timestamp=datetime.fromtimestamp(max(full) + 300, timezone.utc),
        aggregate=quality_counts(accepted_counts),
        nominal_collector_aggregate=quality_counts(data["totals_ns"]),
        strategy_aggregate=quality_counts(strategy_counts),
        qualified_monitoring_seconds=D(accepted_counts["total_monitoring_ns"]) / 1_000_000_000,
        protocol_unsafe_intervals=observation_replay["protocol_unsafe_intervals"],
        protocol_excluded_windows=data["excluded_windows"],
        protocol_excluded_seconds=sum(
            D(v["total_monitoring_ns"]) for v in data["windows"].values() if v["replay_excluded"]
        )
        / 1_000_000_000,
        windows={
            w: dict(v, percentages=quality_counts(v), diagnostics=per_window_diag[int(w)])
            for w, v in data["windows"].items()
        },
        reconnects=reconnects,
        errors=errors,
        processing_lag_ms=quantiles(lags),
        processing_lag_scope="Only timer lag >100ms was logged; not network latency and not all timer ticks",
        queue_backlog=None,
        dropped_events=None,
        queue_scope="No direct queue occupancy/drop telemetry in this run",
        closed_corrupted_segments=0,
        unfinalized_segments=1,
        unfinalized_gzip_files=len(tails),
        accepted_segments=manifest["segments"]["closed"],
        repaired_segments=[manifest["segments"]["active"]],
        excluded_segments=[manifest["segments"]["active"]],
        excluded_partial_window_seconds=sum(
            D(v["total_monitoring_ns"]) for v in data["windows"].values() if not v["full_window"]
        )
        / 1_000_000_000,
        complete_windows=sorted(full),
        partial_windows=[w for w, v in data["windows"].items() if not v["full_window"]],
    )
    atomic_json(root / "quality.json", quality)
    all_seconds = D(
        str(
            (
                datetime.fromisoformat(read(root / "source-metadata" / "live.json")["timestamp"])
                - datetime.fromisoformat(run["started"])
            ).total_seconds()
        )
    )
    streams = defaultdict(lambda: dict(compressed_bytes=0, logical_bytes=0))
    frozen_metrics = read(path / "metrics.json")["streams"]
    for name, info in manifest["files"].items():
        if name.endswith(".jsonl.gz"):
            streams[name.split(".")[0]]["compressed_bytes"] += info["bytes"]
    for stream, info in frozen_metrics.items():
        streams[stream]["logical_bytes"] = info["bytes"]
    for name, info in tails.items():
        streams[name.split(".")[0]]["logical_bytes"] += info.get("decompressed_bytes", 0)
    for info in streams.values():
        info.update(
            mb_per_hour=D(info["compressed_bytes"]) / all_seconds * 3600 / 1_000_000,
            gzip_ratio=D(info["logical_bytes"]) / info["compressed_bytes"] if info["compressed_bytes"] else None,
        )
    total_size = sum(v["bytes"] for v in manifest["files"].values())
    rate = D(total_size) / all_seconds * 3600 / 1_000_000
    storage = dict(
        total_dataset_bytes=total_size,
        raw_transport_bytes=sum(v["compressed_bytes"] for k, v in streams.items() if k.startswith("raw_")),
        observations_bytes=streams["observations"]["compressed_bytes"],
        diagnostics_bytes=sum(
            v["compressed_bytes"]
            for k, v in streams.items()
            if "diagnostics" in k or k in ("processing_lag", "errors", "recoveries", "view_warnings")
        ),
        observed_duration_seconds=all_seconds,
        mb_per_hour=rate,
        projected_24h_gb=rate * 24 / 1000,
        projected_30d_gb=rate * 24 * 30 / 1000,
        streams=streams,
    )
    data.update(
        decision=decision,
        fixed_quantity_scenarios=scenarios,
        positive_episode_lifetime_ms=lifetimes,
        market_distribution=distribution,
        top20_windows=rankings,
        tte_primary=tte,
        latency_survivability=latency,
        observed_storage=storage,
        full_window_count=len(full),
        calendar_full_window_count=len(calendar_full),
        qualified_cross_venue_coverage=D(accepted_counts["both_valid_ns"]) / accepted_counts["total_monitoring_ns"],
        strategy_cross_venue_coverage=D(strategy_counts["both_valid_ns"]) / strategy_counts["total_monitoring_ns"],
        qualified_baseline_2s_coverage=D(accepted_counts["baseline_2s_both_valid_ns"])
        / accepted_counts["total_monitoring_ns"],
        strategy_baseline_2s_coverage=D(strategy_counts["baseline_2s_both_valid_ns"])
        / strategy_counts["total_monitoring_ns"],
        episode_timestamp_correction=read(root / "episode-timestamp-repair.json"),
        independent_completed_counts={
            k: sum(not e["left_censored"] and not e["right_censored"] for e in v) for k, v in groups.items()
        },
        censored_fragment_counts={
            k: sum(e["left_censored"] or e["right_censored"] for e in v) for k, v in groups.items()
        },
        methodology=dict(
            pnl="One hindsight maximum Q*edge per independent Q/direction/threshold=0 episode; never sum observations or thresholds",
            latency="Lifetime-only proxy: fraction of episode-maximum gross with observed lifetime >= budget. Not post-latency edge, fill probability or realized PnL. Censored separate.",
            capacity="Measured size grid 10..1000 only; cap 1000 is not a continuous-depth maximum; NO is mirrored, not a second pool.",
            tte="Valid time is depth-executable time at Q; episode count assigned once to its start bucket",
            fees="Excluded, as are execution risk and simultaneous fills; settlement remains UNKNOWN",
        ),
    )
    if decision_reason is None:
        decision_reason = (
            f"Пригодное покрытие {number(data['qualified_cross_venue_coverage'], 100)}%; "
            f"Q10 positive episodes встречаются в {distribution['BOTH:10:0']['count']} из {len(full)} окон. "
            f"Завершённая lifetime Q10: median A {number(lifetimes['A:10']['completed']['median'])} ms, "
            f"B {number(lifetimes['B:10']['completed']['median'])} ms. "
            "Перед следующим этапом требуется оценить влияние консервативных исключений, цензуры и freshness policy. "
            "Settlement и одновременное исполнение двух ног не подтверждены."
        )
    data["decision_reason"] = decision_reason
    atomic_json(root / "analysis.json", data)
    lines = [
        "# Восстановление BTC 5m research: итоговый отчёт",
        "",
        f"**Вердикт: {decision}.**",
        "",
        f"Исходный запуск `{Path(manifest['source']).name}` прерван обновлением Windows. "
        f"Raw dataset сохранён без изменений. Replay всех закрытых сегментов успешен; основная выборка — **{len(full)} полных окон / {number(hours)} часа**.",
        "",
        f"Закрытый интервал UTC: {run['started']} — {final['timestamp']}. Последний полностью включённый рынок заканчивается {quality['strategy_cutoff_timestamp']}.",
        "",
        "## 1. Причина и восстановление",
        "",
        "Windows System / User32 1074: 10 сентября в 04:41:02.3745765 МСК `MoUsoCoreWorker.exe` инициировал planned restart, "
        "код 0x80020010. Последний raw — 04:41:02.268856; затем Winlogon 7002 в 04:41:09 и Kernel-General 13 в 04:42:23. "
        "Это подтверждённое прерывание во время перезагрузки Windows Update. Exit code Python не сохранился. "
        "Отдельного traceback collector нет; старый PermissionError finalizer относится к предыдущей попытке.",
        "",
        "623 закрытых сегмента проверены и скопированы с SHA-256. У восьми файлов сегмента 623 отсутствует gzip trailer; "
        "весь распакованный текст состоит из полных записей, удалено 0 байт JSONL. Воспроизводимые копии находятся в `repaired-tail/`; "
        "они не включены в основную выборку. Хеши и смещения — `frozen-manifest.json`, `tail-recovery.json`. "
        "Начальное и конечное неполные окна исключены из strategy statistics, но сохранены в quality.",
        "",
        "## 2. Replay и качество",
        "",
        "| Venue | Raw frames | Checkpoints | Mismatch |",
        "|---|---:|---:|---:|",
    ]
    for venue in ("polymarket", "limitless"):
        v = replay[venue]
        lines.append(f"| {venue} | {v['raw_frames']} | {v['checkpoints_checked']} | {v['mismatch_count']} |")
    lines += [
        "",
        f"Независимо проверены {observation_replay['counts'].get('priced_rows_checked', 0)} ценовых строк из raw: "
        "Decimal VWAP, направление, book status/timestamp и matching metadata. Неценовые строки не используются как opportunities. "
        "Точное равенство transport deadlines этим дополнительным replay не утверждается.",
        "",
        f"Календарно полных окон: {len(calendar_full)}; в основную выборку принято {len(full)}. "
        f"Исключения: {data['excluded_windows']}. После rejected Limitless frame инвалидирование collector "
        "может задерживаться до завершения async WebSocket close; котировки при этом остаются VALID. "
        "Первое подтверждённое позднее version regression: raw ordinal 5516782, invalidation через 3084.4455 ms. "
        "Для сохранения safety исключены целые затронутые окна, а не только замеченные ценовые строки. "
        "Первое расхождение и previous frames сохранены отдельно; protocol intervals перечислены в quality.json.",
        "",
        f"Monitoring: {number(D(data['duration_seconds']) / 3600)} ч. Cross-venue valid на пригодном интервале: **{number(data['qualified_cross_venue_coverage'], 100)}%**. "
        f"Baseline TTL 2s на том же интервале: **{number(data['qualified_baseline_2s_coverage'], 100)}%**. "
        f"Исключено из strategy statistics {number(quality['excluded_partial_window_seconds'])} с неполных окон; "
        f"{number(quality['protocol_excluded_seconds'])} с protocol-небезопасных окон; "
        f"сверх closed cutoff восстановлено {number(all_seconds - D(str((datetime.fromisoformat(final['timestamp']) - datetime.fromisoformat(run['started'])).total_seconds())))} с хвоста.",
        "",
        f"В основной выборке {len(full)} полных окон: cross-venue valid {number(data['strategy_cross_venue_coverage'], 100)}%, "
        f"baseline 2s {number(data['strategy_baseline_2s_coverage'], 100)}%. "
        "Качество начального и конечного неполных окон учитывается только в сводке пригодного интервала.",
        "",
        "| Venue | VALID % | DESYNC % | RECOVERING % | STALE % | MISSING % | Reconnects |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for venue in ("Polymarket", "Limitless"):
        lines.append(
            f"| {venue} | "
            + " | ".join(
                number(quality["aggregate"].get(venue + "_" + status + "_pct", 0))
                for status in ("VALID", "DESYNC", "RECOVERING", "STALE", "MISSING")
            )
            + f" | {reconnects[venue]} |"
        )
    lines += [
        "",
        f"Timer lag >100ms: {len(lags)} записей; median {number(quality['processing_lag_ms']['median'])} мс, "
        f"p95 {number(quality['processing_lag_ms']['p95'])}, max {number(quality['processing_lag_ms']['max'])}. "
        "Это условное распределение только превышений, не latency сети. Queue backlog и dropped events — UNKNOWN: прямой telemetry нет. "
        "Повреждённых закрытых сегментов: 0. Незавершённых: 1 (8 gzip-файлов).",
        "",
        "## 3. Edge после VWAP",
        "",
        "A = YES Limitless + NO Polymarket. B = YES Polymarket + NO Limitless. Edge в центах на share. "
        "Квантили взвешены длительностью валидного доступного состояния. Fees не включены.",
        "",
        "| Направление / Q | median | p90 | p95 | p99 | max | positive median | positive p90 | positive p95 | positive max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for direction in ("A", "B"):
        for q in ("10", "25", "50", "100", "250", "500", "1000"):
            v = data["edge"].get(direction + ":" + q, {})
            lines.append(
                f"| {direction}/{q} | "
                + " | ".join(
                    number(v.get(mode, {}).get(k), 100)
                    for mode, keys in (
                        ("time_weighted", ("median", "p90", "p95", "p99", "max")),
                        ("time_weighted_positive_only", ("median", "p90", "p95", "max")),
                    )
                    for k in keys
                )
                + " |"
            )
    lines += [
        "",
        "## 4. Независимые эпизоды и lifetime",
        "",
        "Пороги строгие; ячейка: число / в час / на рынок. Q и пороги нельзя суммировать. "
        "Первая таблица включает наблюдаемые цензурированные фрагменты: их независимость через разрывы данных не доказана. "
        "Следующая таблица содержит только полностью наблюдаемые независимые эпизоды, lifetime дан отдельно.",
        "",
        "| Направление / Q | >0c | >0.5c | >1c | >2c | >3c | >5c |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for direction in ("A", "B"):
        for q in ("10", "25", "50", "100", "250", "500", "1000"):
            cells = []
            for cut in ("0", "0.005", "0.01", "0.02", "0.03", "0.05"):
                n = len(groups[f"{direction}:{q}:{cut}"])
                cells.append(f"{n} / {number(D(n) / hours)} / {number(D(n) / len(full))}")
            lines.append(f"| {direction}/{q} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Только полностью наблюдаемые независимые эпизоды: число / в час / на рынок.",
        "",
        "| Направление / Q | >0c | >0.5c | >1c | >2c | >3c | >5c |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for direction in ("A", "B"):
        for q in ("10", "25", "50", "100", "250", "500", "1000"):
            cells = []
            for cut in ("0", "0.005", "0.01", "0.02", "0.03", "0.05"):
                n = data["independent_completed_counts"].get(f"{direction}:{q}:{cut}", 0)
                cells.append(f"{n} / {number(D(n) / hours)} / {number(D(n) / len(full))}")
            lines.append(f"| {direction}/{q} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "| Направление / Q / тип | N | median ms | p75 | p90 | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, categories in lifetimes.items():
        for category, v in categories.items():
            lines.append(
                f"| {key}/{category} | {v['count']} | "
                + " | ".join(number(v[k]) for k in ("median", "p75", "p90", "p95", "max"))
                + " |"
            )
    lines += [
        "",
        "Capacity каждого episode сохранена в `opportunities.jsonl.gz`: `max_executable_q` и `capacity_by_threshold`. "
        "Это максимум на измеренной сетке 10/25/50/100/250/500/1000, не точный непрерывный максимум depth. "
        "Зеркальная NO-книга Limitless не удваивает ликвидность.",
        "",
        "## 5. Gross theoretical PnL",
        "",
        "Одна гипотетическая сделка на независимый положительный episode: Q × max edge этого Q. "
        "Максимум выбран задним числом — оптимистическая оценка. Это НЕ realized PnL; fees, execution risk, "
        "одновременность двух fills и равенство settlement не учтены. 24h — линейная экстраполяция этого sample, не прогноз.",
        "",
        "Основной сценарий — completed. all_observed/censored — дополнительные верхние оценки по наблюдаемым фрагментам; "
        "они могут относиться к одной экономической возможности по разные стороны разрыва и не считаются доказанно независимыми сделками.",
        "",
        "| Направление / Q / тип | Trades | Trades/h | Gross/h | Gross/24h |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, categories in scenarios.items():
        for category, v in categories.items():
            lines.append(
                f"| {key}/{category} | {v['episodes']} | {number(v['trades_per_hour'])} | {number(v['gross_per_hour'])} | {number(v['gross_per_day_extrapolation'])} |"
            )
    lines += [
        "",
        "## 6. Распределение по рынкам",
        "",
        f"Число пригодных полных окон с хотя бы одним эпизодом A или B; доля от всех {len(full)} принятых окон. ANY_Q — объединение размеров.",
        "",
        "| Q | >0c | >1c | >2c | >3c | >5c |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for q in ("10", "25", "50", "100", "ANY_Q"):
        lines.append(
            f"| {q} | "
            + " | ".join(
                f"{distribution[f'BOTH:{q}:{cut}']['count']} ({number(distribution[f'BOTH:{q}:{cut}']['share'], 100)}%)"
                for cut in ("0", "0.01", "0.02", "0.03", "0.05")
            )
            + " |"
        )
    for metric in ("max_edge", "episode_count", "gross_ANY_Q"):
        lines += [
            "",
            f"### Top-20: {metric}",
            "",
            "| Окно UTC | max edge c | Completed ANY_Q | Gross ANY_Q | Gross Q10 | Gross Q25 | Gross Q50 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for w in rankings[metric]:
            lines.append(
                f"| {datetime.fromtimestamp(w['window'], timezone.utc).isoformat()} | {number(w.get('max_edge'), 100)} | {w.get('episode_count', 0)} | {number(w.get('gross_ANY_Q', 0))} | "
                + " | ".join(number(w.get("gross_Q" + q, 0)) for q in ("10", "25", "50"))
                + " |"
            )
    lines += [
        "",
        "Rankings episode count и gross используют только completed episodes. Gross ANY_Q: максимум одной сделки "
        "на объединённый episode с выбором Q из сетки 10–1000 задним числом. "
        "Отдельные rankings Q10/Q25/Q50 также сохранены в analysis.json.",
        "",
        "## 7. Time to expiry",
        "",
        "Эпизод учитывается один раз по bucket начала; valid time означает доступный depth при данном Q.",
        "UTC-метки эпизодов уточнены по локальным anchors исходных observations: максимальная поправка "
        f"{number(data['episode_timestamp_correction']['maximum_absolute_utc_shift_ms'])} ms. "
        "Monotonic endpoints, lifetime, edge и time-weighted TTE distributions не менялись. Исходный производный gzip сохранён отдельно.",
        "",
        "| Направление / Q / TTE sec | Valid sec | Positive % | Starts all / completed | Positive median c | Max c |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, v in sorted(tte.items()):
        lines.append(
            f"| {key} | {number(v['valid_seconds'])} | {number(v['positive_time_pct'])} | {v['episodes_started']} / {v['completed_episodes_started']} | {number(v['median_positive_edge'], 100)} | {number(v['max_edge'], 100)} |"
        )
    lines += [
        "",
        "## 8. Предварительная latency survivability",
        "",
        "Оценка только по наблюдаемой длительности эпизода, без моделирования исполнения.",
        "",
        "В таблице доля gross episode maxima у эпизодов с наблюдаемой lifetime не меньше бюджета. "
        "Это не edge после задержки: максимум мог исчезнуть раньше конца episode. Для censored известна только наблюдаемая часть lifetime. "
        "Execution simulator не создавался.",
        "",
        "| Направление / Q / тип | 25ms % | 50ms % | 100ms % | 250ms % | 500ms % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, categories in latency.items():
        for category, v in categories.items():
            lines.append(
                f"| {key}/{category} | "
                + " | ".join(number(v["budgets"][str(ms)]["gross_opportunity_share"], 100) for ms in LATENCIES)
                + " |"
            )
    lines += [
        "",
        "Lifetime buckets, число эпизодов:",
        "",
        "| Направление / Q / тип | <10 | 10–25 | 25–50 | 50–100 | 100–250 | 250–500 | 500–1000 | ≥1000 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, categories in latency.items():
        for category, v in categories.items():
            lines.append(
                f"| {key}/{category} | "
                + " | ".join(
                    str(v["lifetime_buckets"].get(label, 0))
                    for label in [*(label for _, label in LIFETIME_BINS), ">=1000"]
                )
                + " |"
            )
    lines += [
        "",
        "## 9. Хранение",
        "",
        f"Исходный dataset: {number(D(total_size) / 1_000_000_000)} GB; raw transport: {number(D(storage['raw_transport_bytes']) / 1_000_000)} MB; "
        f"observations: {number(D(storage['observations_bytes']) / 1_000_000)} MB; diagnostics: {number(D(storage['diagnostics_bytes']) / 1_000_000)} MB. "
        f"Рост {number(rate)} MB/h; 24h {number(storage['projected_24h_gb'])} GB; 30d {number(storage['projected_30d_gb'])} GB.",
        "",
        "| Stream | gzip MB | MB/hour | JSONL/gzip ratio |",
        "|---|---:|---:|---:|",
    ]
    for key, v in streams.items():
        lines.append(
            f"| {key} | {number(D(v['compressed_bytes']) / 1_000_000)} | {number(v['mb_per_hour'])} | {number(v['gzip_ratio'])} |"
        )
    lines += [
        "",
        "Для будущих runs: после независимого raw→observations replay можно агрегировать производные observations, "
        "заменять повторные diagnostic frames ссылками на raw ordinal, сжимать закрытые сегменты сильнее. "
        "Сохранять raw transport, market metadata, clocks/order, connection/subscription events и checkpoints. "
        "Текущий dataset не очищался; storage projection не включает дополнительную forensic-копию.",
        "",
        "## 10. Качество каждого окна",
        "",
        "Проценты ниже — исходные статусы collector; пометка EXCLUDED запрещает включение окна в стратегию. "
        "Сводное пригодное покрытие выше исключает эти окна целиком; исходная сводка сохранена отдельно в quality.json.",
        "",
        "| Окно UTC | Полное | Cross valid % | Poly valid % | Limitless valid % | Lag >100ms | Max lag ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for w, v in quality["windows"].items():
        lines.append(
            f"| {datetime.fromtimestamp(int(w), timezone.utc).isoformat()} | {'EXCLUDED' if v['replay_excluded'] else v['full_window']} | {number(v['cross_venue_valid_coverage'], 100)} | {number(v['percentages'].get('Polymarket_VALID_pct', 0))} | {number(v['percentages'].get('Limitless_VALID_pct', 0))} | {v['diagnostics'].get('lag_events_gt_100ms', 0)} | {number(v['diagnostics'].get('max_lag_ms'))} |"
        )
    lines += [
        "",
        "Длительности DESYNC/RECOVERING/STALE/MISSING по каждому окну, reconnects и дополнительные метрики — в quality.json. "
        "Полные данные edge/CDF/threshold capacity и отдельные распределения A/B — в analysis.json.",
        "",
        "## 11. Решение и ограничения",
        "",
        f"**{decision}**. {decision_reason} "
        "Наблюдаемая разность котировок сама по себе не доказывает арбитраж. "
        "Новый collector, orders, wallets/private keys и execution simulator не запускались.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    integrity = read(root / "source-integrity.json")
    accounting = read(root / "record-accounting.json")
    recovery_lines = [
        "# Восстановление прерванного prediction research",
        "",
        f"Источник: `{manifest['source']}`. Результаты: `{root.resolve()}`.",
        "",
        "## Подтверждённая причина",
        "",
        "Плановая перезагрузка Windows Update: User32 1074, 2026-09-10 04:41:02.3745765 МСК, "
        "`MoUsoCoreWorker.exe`, reason 0x80020010. Затем Winlogon 7002 (04:41:09), Kernel-Power 109 и "
        "Kernel-General 13 (04:42:23). В 05:27 была дополнительная перезагрузка TrustedInstaller; последняя загрузка ОС — 05:29.",
        "",
        "Логи collector не содержат traceback. Рабочий PID 624 и finalizer PID 22736 отсутствуют. "
        "Индивидуальные exit codes и завершение родительского shell не записаны; Security 4689 не дал событий в 04:40–04:43. "
        "Подтверждён системный restart, отдельная программная ошибка в момент остановки не установлена. "
        "Исторический PermissionError относится к finalizer от 9 сентября, который позднее был заменён; "
        "у последней попытки stdout/stderr пусты и сохранён WAITING.",
        "",
        "В экспортированном System-журнале соответствующего интервала нет свидетельств sleep, Kernel-Power 41, "
        "unexpected shutdown 6008 или exhaustion 2004. Свободное место и память зафиксированы после перезагрузки "
        "в disk.json/os.json; они не доказывают историческое потребление памяти. Обнаруженные WER-записи со старыми "
        "дампами не приняты за причину текущего прерывания. Исходные System/Application/PowerShell events сохранены рядом.",
        "",
        "## Заморозка и целостность",
        "",
        f"До repair сохранены SHA-256 всех {len(manifest['files'])} файлов, manifest, проекции и launcher/finalizer logs. "
        f"Закрытых gzip-файлов скопировано 9028: segments 0–622. Полная gzip/JSONL-проверка пройдена. "
        f"Повторная проверка источника: {integrity['checked_files']} файлов, mismatches={integrity['mismatches']}.",
        "",
        f"Полных записей: {accounting['total_complete_records']}; последний ordinal: {accounting['last_ordinal']}. "
        "Совпадение числа записей с ordinal не доказывает отсутствие сетевых потерь до журналирования.",
        "",
        "## Последний segment 623",
        "",
        "У всех восьми файлов отсутствует gzip EOF/trailer. Поток DEFLATE распакован через zlib, каждая полная JSONL-строка "
        "проверена. Сохранено 2157 полных записей; неполных JSON-строк нет, отброшено 0 байт. Копии пересжаты "
        "детерминированно с mtime=0; оригиналы не редактировались. CRC исходного gzip без trailer не проверяем: "
        "структурная читаемость не означает гарантию сохранения всех ОС-буферов.",
        "",
        "Original/repaired SHA-256, последняя запись каждого потока и `truncation_offset_uncompressed` находятся в "
        "tail-recovery.json. Смещение относится к распакованному JSONL; эквивалентного compressed byte offset нет, "
        "так как оригинальный gzip не обрезался, а полные записи записаны новым gzip. Хвост не включён в стратегию.",
        "",
        f"Последний raw: 2026-09-10T01:41:02.268856Z, ordinal 13659546. Последняя полная observation: ordinal "
        f"{accounting['last_ordinal']}, {accounting['last_record']['received_timestamp']}. "
        f"Последний закрытый segment 622 заканчивается {final['timestamp']}.",
        "",
        "## Диапазон и replay",
        "",
        f"`analysis_cutoff_timestamp`: {final['timestamp']}. Календарно полных окон {len(calendar_full)}, "
        f"в основную стратегию принято {len(full)} ({number(hours)} ч). "
        f"Неполные окна: {quality['partial_windows']}. Protocol-исключения: {data['excluded_windows']}.",
        "",
        "Checkpoint replay: обе площадки без расхождений. Дополнительный raw→VWAP replay проверяет каждую ненулевую "
        "ценовую строку, matching, timestamp и состояние книги; null-строки исключены. "
        "Первое расхождение нового проверяющего кода (ordinal 331179) было вызвано пропущенным RECOVERING после "
        "resubscribe; исправлено по существующему collector и покрыто тестом. Свидетельство сохранено в "
        "observation-first-divergence-verifier-v1.json.",
        "",
        "Затем на ordinal 5516785 выявлен настоящий риск исходного collector: поздняя регрессия Limitless "
        "raw ordinal 5516782, но disconnect выполняется лишь после ожидания закрытия async WebSocket. "
        "Старые котировки остаются VALID примерно 3,08 с. Expected edge -0.004 совпадает с raw VWAP, "
        "однако этот интервал не считается безопасным. Первое расхождение, книги и предыдущие frames — "
        "observation-first-protocol-divergence.json. Все найденные подобные rejected-frame окна целиком исключены; "
        "никакая safety-проверка не отключалась. Исправление live collector отложено в backlog.",
        "",
        "В отдельной производной проекции уточнены UTC-метки эпизодов по локальным anchors observations. "
        f"Максимальная поправка {number(data['episode_timestamp_correction']['maximum_absolute_utc_shift_ms'])} ms. "
        "Исходный derived gzip сохранён; monotonic endpoints, lifetime, edge и capacity не менялись. "
        "Хеши — episode-timestamp-repair.json; исправленная проекция — opportunities.jsonl.gz.",
        "",
        "Все исходные модули prediction совпадают с source_sha256 запуска. Проверены 83 backend-теста и Ruff; "
        "raw transport не изменялся. Восстановление и анализ работают отдельно от основной SQLite.",
        "",
        "## Воспроизведение",
        "",
        "Из services/api, используя новый каталог для новой копии:",
        "",
        "```powershell",
        ".venv/Scripts/python.exe -m app.scripts.recover_prediction data/prediction/research-20260909T151749Z data/prediction/НОВАЯ_КОПИЯ",
        ".venv/Scripts/python.exe -m app.scripts.replay_prediction data/prediction/НОВАЯ_КОПИЯ/dataset",
        ".venv/Scripts/python.exe -m app.scripts.verify_prediction_observations data/prediction/НОВАЯ_КОПИЯ/dataset",
        ".venv/Scripts/python.exe -m app.scripts.analyze_prediction_research data/prediction/НОВАЯ_КОПИЯ/dataset --full-windows-only --replay-exclusions data/prediction/НОВАЯ_КОПИЯ/dataset/observation-replay.json",
        ".venv/Scripts/python.exe -m app.scripts.repair_prediction_episode_timestamps data/prediction/НОВАЯ_КОПИЯ",
        ".venv/Scripts/python.exe -m app.scripts.recover_prediction data/prediction/research-20260909T151749Z data/prediction/НОВАЯ_КОПИЯ --verify-only",
        ".venv/Scripts/python.exe -m app.scripts.report_prediction_recovery data/prediction/НОВАЯ_КОПИЯ",
        "```",
        "",
        "REPORT.md, analysis.json, replay.json и quality.json сформированы модулем report_prediction_recovery; "
        "служебные source-integrity.json и record-accounting.json создаются recovery-командами. "
        "Исходные Windows events исторические и не заменяются результатами последующего запуска.",
        "",
        f"## Итог\n\n**{decision}**. {decision_reason}",
        "",
        "Новый collector, orders, wallets/private keys, execution simulator и очистка данных не запускались.",
    ]
    (root / "recovery-report.md").write_text("\n".join(recovery_lines) + "\n", encoding="utf-8")
    return dict(decision=decision, complete_windows=len(full), hours=hours, coverage=data["cross_venue_valid_coverage"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--decision",
        default="MORE DATA REQUIRED",
        choices=("MORE DATA REQUIRED", "REJECT STRATEGY", "PROCEED TO EXECUTION SIMULATOR"),
    )
    parser.add_argument("--decision-reason")
    args = parser.parse_args()
    print(json.dumps(build(args.directory, args.decision, args.decision_reason), default=str))
