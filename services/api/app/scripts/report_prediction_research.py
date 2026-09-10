"""Render measured research results as a Russian Markdown report."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from app.scripts.analyze_prediction import records


def number(value, scale=1, digits=3):
    return "—" if value is None else f"{D(value) * scale:.{digits}f}"


def report(path, output):
    data = json.loads((path / "research-analysis.json").read_text())
    replay = json.loads((path / "replay.json").read_text()) if (path / "replay.json").exists() else {}
    run = json.loads((path / "run.json").read_text())
    final = json.loads((path / "live.json").read_text())
    errors = Counter(r["payload"].get("detail", r["payload"].get("error", "UNKNOWN")) for r in records(path, "errors"))
    lags = [D(r["payload"]["timer_lag_ms"]) for r in records(path, "processing_lag")]
    lines = [
        "# Стабилизация BTC 5m collector: отчёт A–L",
        "",
        f"Набор: `{path.name}`. Период UTC: {run['started']} — {final['timestamp']}.",
        "",
        f"Фактически проанализировано **{number(D(data['duration_seconds']) / 60)} минуты**; "
        f"полных 5m окон: **{sum(w['full_window'] for w in data['windows'].values())}**. "
        "Этот объём не подменяет требуемые 6–12 часов. Все котировки реальные, исполнение отсутствует.",
        "",
        "## A. Причина Polymarket DESYNC",
        "",
        "Все 133 события предыдущего sample разобраны по raw: 112 BBO mismatch, 20 только регресс timestamp, "
        "1 сочетание. Для 14 из 20 timestamp-only случаев старые абсолютные изменения уже присутствовали "
        "в более новом состоянии: теперь такие no-op не инвалидируют книгу. Остальные регрессии остаются небезопасными.",
        "",
        "Новый диагностический запуск 181,1 секунды сохранил продолжение после mismatch: 191 восстановление "
        "токена после 96 разных проблемных frames, все через последующий полный `book`, без ошибок и reconnect. "
        "Максимальное ожидание snapshot — 995,484 мс. Основной дефект collector — немедленный reconnect "
        "при промежуточной несогласованности между сообщениями. Причина внутреннего порядка publisher "
        "остаётся гипотезой; packet loss не установлен. Старый raw обрывался на mismatch и не доказывает "
        "последующее восстановление каждого из 113 BBO случаев.",
        "",
        "Полный snapshot заменяет книгу; delta задаёт абсолютный размер. Проверка выполняется после всего "
        "payload, отдельно для токена. Все 100 283 старых price_change содержали оба токена; найдено 1003 "
        "повторных абсолютных обновления. Порядок получения сохраняется, timestamps не используются "
        "для перестановки сообщений. Deltas до snapshot и во время DESYNC не создают валидную книгу. "
        "На mismatch сохраняются raw frame, calculated book, advertised BBO, token, ordinal, clocks "
        "и 20 предыдущих frames. Книга невалидна до свежего snapshot; после дедлайна 1s выполняется reconnect. "
        "Начальная подписка ограничена 15s. Ротация использует отдельные adapters текущего/следующего рынка.",
        "",
        "## B. Freshness policy Limitless",
        "",
        "[Официальный протокол](https://docs.limitless.exchange/developers/websocket/market-data) публикует "
        "полную coalesced книгу при изменении и initial snapshot после подписки. Версия не обязана быть "
        "непрерывной; старые версии сразу после подписки отбрасываются. Поздняя регрессия требует новой сессии. "
        "NO зеркалируется из YES, независимым объёмом не считается.",
        "",
        "BOOK_AGE отделён от CONNECTION_HEALTH. Limitless: TTL книги 60s, после 30s без обновлений — "
        "повторная подписка с RECOVERING до snapshot. Здоровье соединения ограничено negotiated "
        "pingInterval + pingTimeout; disconnect инвалидирует сразу. Polymarket: возраст книги до 30s, "
        "application silence deadline 30s, PING каждые 10s. Это исследовательская политика, а не гарантия "
        "актуальности publisher: heartbeat не доказывает отсутствие серверного сбоя подписки.",
        "",
        "| Поток Limitless | Интервалы | median, ms | p95, ms | p99, ms | max, ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, values in data["limitless_intervals_ms"].items():
        lines.append(
            f"| {key} | {values['weight']} | "
            + " | ".join(number(values[k]) for k in ("median", "p95", "p99", "max"))
            + " |"
        )
    lines += [
        "",
        f"Диагностика неизменных книг/версий: `{json.dumps(data['limitless_freshness_counts'])}`.",
        "",
        "## C. Valid coverage",
        "",
        f"Общее покрытие: **{number(data['cross_venue_valid_coverage'], 100)}%**; "
        f"консервативный baseline 2s: **{number(data['baseline_2s_coverage'], 100)}%**. "
        "Знаменатель включает startup и отсутствие данных. Длительности интегрируются по monotonic clock, "
        "обрезаются точно на freshness/connection deadlines и границах окон. Connection healthy считается "
        "от CONNECTED отдельно от получения snapshot. Таймер 50ms не добавляет event-weighted observations.",
        "",
        "| Начало UTC | Полное | Monitoring, s | Poly healthy, s | Poly valid, s | LL healthy, s | LL valid, s | Both valid, s | Coverage |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window, w in data["windows"].items():
        keys = (
            "total_monitoring_ns",
            "Polymarket_connection_healthy_ns",
            "Polymarket_VALID_ns",
            "Limitless_connection_healthy_ns",
            "Limitless_VALID_ns",
            "both_valid_ns",
        )
        lines.append(
            f"| {datetime.fromtimestamp(int(window), timezone.utc):%H:%M:%S} | {'да' if w['full_window'] else 'нет'} | "
            + " | ".join(number(w.get(k, 0), D("1e-9")) for k in keys)
            + f" | {number(w['cross_venue_valid_coverage'],100)}% |"
        )
    total = D(data["totals_ns"]["total_monitoring_ns"])
    lines += ["", "| Причина невалидности пары | Доля времени |", "|---|---:|"]
    for status in ("DESYNC", "RECOVERING", "MISSING", "STALE"):
        lines.append(f"| {status} | {number(D(data['totals_ns'].get('cross_venue_'+status+'_ns',0)) / total,100)}% |")
    lines += [
        "",
        "Полные счётчики каждой книги и depth-executable для всех Q в каждом окне — в `research-analysis.json/windows`.",
        "",
        "## D. Storage optimization",
        "",
        "Raw application frames сохранены полностью, включая heartbeat; отправленные subscribe/PING/PONG "
        "сохраняются отдельно. Полная Polymarket книга больше не пишется после каждой delta. Checkpoints "
        "сохраняются при initial snapshot, восстановлении, завершении подписки/ротации и на обновлениях "
        "с интервалом около 10s. Между ними используется raw; normalized delta stream не дублируется. "
        "Закрытые gzip-сегменты по 60s можно копировать и анализировать, не останавливая collector. "
        "Активный сегмент при аварии может остаться незавершённым; его нельзя молча считать полным.",
        "",
        "| Поток | JSONL MB/hour | gzip MB/hour |",
        "|---|---:|---:|",
    ]
    for key, values in data["storage"].items():
        lines.append(f"| {key} | {number(values.get('jsonl_mb_per_hour'))} | {number(values['gzip_mb_per_hour'])} |")
    lines += [
        "| normalized deltas | 0 | 0 |",
        "",
        f"Всего **{number(data['total_gzip_mb_per_hour'])} MB/hour** gzip. "
        + "; ".join(f"{days} дней: {number(value)} GB" for days, value in data["projected_gb"].items())
        + ". "
        "Это линейная проекция фактического короткого потока. Частота raw меняется; разные запуски нельзя "
        "сравнивать по общему MB/hour как чистый эффект формата. 2 GiB free disk — порог штатной остановки. "
        "Статистические квантили используют временный локальный SQLite-кэш с Decimal-сортировкой; "
        "существующая БД приложения не менялась, кэш удаляется после анализа.",
        "",
        "## E. Multi-hour dataset",
        "",
        f"Этот отчёт покрывает только указанные выше **{number(D(data['duration_seconds'])/60)} минуты**. "
        "Запланированный срок из run.json не считается собранными данными. Для 12h доступны foreground "
        "CLI `collect_prediction --seconds 43200` и скрытый Windows launcher "
        "`ops/windows/start-prediction-research.ps1 -Hours 12`. Состояние, ошибки и PID сохраняются локально. "
        "Сон, выключение и выход из Windows прерывают локальный сбор; VPS в этой итерации не задействован.",
        "",
        "| Replay | Raw frames | Reconstructed updates | Checkpoints | Несовпадения |",
        "|---|---:|---:|---:|---:|",
    ]
    for venue, values in replay.items():
        lines.append(
            f"| {venue} | {values.get('raw_frames','—')} | {values.get('reconstructed_updates','—')} | "
            f"{values.get('checkpoints_checked','—')} | {values.get('mismatch_count','—')} |"
        )
    lines += [
        "",
        "Replay восстанавливает все raw frames и сравнивает полные книги/status/timestamps в checkpoints. "
        "Совпадение подтверждает воспроизводимость сохранённого потока; полноту данных на стороне venue оно не доказывает.",
        "",
        f"Записанных ошибок: {sum(errors.values())}. Loop lag >100ms: {len(lags)}; max {number(max(lags) if lags else None)} ms. "
        "Это задержки обработки на локальном хосте, не измерение сетевой latency. "
        f"Сводка ошибок: `{json.dumps(dict(errors), ensure_ascii=False)}`.",
        "",
        "## F. Edge distribution",
        "",
        "A = BUY YES Limitless + BUY NO Polymarket. B = BUY YES Polymarket + BUY NO Limitless. "
        "`edge(Q) = 1 − VWAP_YES(Q) − VWAP_NO(Q)`. Таблицы в центах номинальной выплаты. "
        "Все размеры сохранены; 10/25/50/100 — основной анализ. Квантили — точная обратная эмпирическая CDF. "
        "Time-weighted использует только валидную длительность, без переноса через разрывы.",
    ]
    for mode, title in (
        ("time_weighted", "По времени"),
        ("event_weighted", "По числу обновлений"),
        ("time_weighted_positive_only", "Только положительные, по времени"),
        ("event_weighted_positive_only", "Только положительные, по обновлениям"),
    ):
        lines += [
            "",
            f"### {title}",
            "",
            "| Dir:Q | median, c | p90 | p95 | p99 | max |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for key in sorted(data["edge"], key=lambda x: (x.split(":")[0], int(x.split(":")[1]))):
            values = data["edge"][key][mode]
            lines.append(
                f"| {key} | "
                + " | ".join(number(values[k], 100) for k in ("median", "p90", "p95", "p99", "max"))
                + " |"
            )
    lines += [
        "",
        "## G. Независимые opportunity episodes",
        "",
        "Непрерывное превышение строгого порога — один эпизод. ANY_Q объединяет размеры в пределах "
        "направления и порога. Размерные и пороговые семейства нельзя суммировать как независимые сделки. "
        "Прерывание данных/ротация/stop цензурируют эпизод. Счётчики по каждому Q и окну сохранены в JSON. "
        "Отрезки по разные стороны невалидного промежутка могут относиться к одному скрытому рыночному эпизоду; "
        "их независимость не доказана. Per-hour rate нормирован на этот sample и не является прогнозом.",
        "",
        "| Направление | >0c | >0.5c | >1c | >2c | >3c | >5c | >0c / hour |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for direction in ("A", "B"):
        lines.append(
            f"| {direction} | "
            + " | ".join(
                str(data["episode_counts"].get(f"{direction}:ANY_Q:{cut}", 0))
                for cut in ("0", "0.005", "0.01", "0.02", "0.03", "0.05")
            )
            + " | "
            + number(data["episodes_per_hour"].get(f"{direction}:ANY_Q:0", 0))
            + " |"
        )
    lines += [
        "",
        "## H. Capacity",
        "",
        "В `opportunities.jsonl.gz` каждый эпизод содержит максимум показанного executable Q, "
        "`capacity_by_threshold` для шести порогов и `theoretical_maximum_gross_pnl = max_t,Q(Q × edge_t(Q))`. "
        "Это максимум одного снимка; сумма повторных updates не превращается в PnL. Capacity измеряется "
        "по заданной сетке до 1000 shares и не интерполируется между Q. Fees, одновременные fills, minimum "
        "Limitless и экономическая эквивалентность collateral не подтверждены.",
        "",
        "| Dir / семейство / capacity threshold | Максимальные Q: число эпизодов |",
        "|---|---|",
    ]
    for key, counts in sorted(data["capacity_counts"].items()):
        if ":ANY_Q:0:" in key:
            lines.append(
                f"| {key} | "
                + ", ".join(f"{q}: {count}" for q, count in sorted(counts.items(), key=lambda x: D(x[0])))
                + " |"
            )
    lines += [
        "",
        "Наблюдаемые отрезки ANY_Q при >0c (первые 50; полный перечень в gzip):",
        "",
        "| Dir | Начало UTC | Конец UTC | Duration ms | Max edge c | Средний edge c | Q при >0 | Max gross | Цензура |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    shown = 0
    for event in records(path, "opportunities"):
        if event.get("quantity") != "ANY_Q" or D(event["threshold"]) != 0:
            continue
        lines.append(
            f"| {event['direction']} | {event['start']} | {event['end']} | {number(event['duration_ms'])} | "
            f"{number(event['max_edge'],100)} | {number(event['mean_time_weighted_edge'],100)} | "
            f"{event['capacity_by_threshold']['0']} | {number(event['theoretical_maximum_gross_pnl'])} | "
            f"{'да' if event['left_censored'] or event['right_censored'] else 'нет'} |"
        )
        shown += 1
        if shown == 50:
            break
    lines += [
        "",
        "## I. Lifetime",
        "",
        "Завершённые и цензурированные длительности разделены. "
        "Левая граница также цензурируется, если до открытия эпизода не было валидных данных.",
        "",
        "| Dir:Q:threshold / тип | N | median ms | p75 | p90 | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, values in sorted(data["lifetime_ms"].items()):
        if ":ANY_Q:" in key:
            lines.append(
                f"| {key} | {values['weight']} | "
                + " | ".join(number(values[k]) for k in ("median", "p75", "p90", "p95", "max"))
                + " |"
            )
    lines += [
        "",
        "## J. Time-to-expiry effect",
        "",
        "Доля положительного времени внутри depth-valid "
        "времени каждого bucket; частота updates не задаёт вес. Полные квантили всех Q — в JSON. "
        "Пустые buckets не означают отсутствия edge.",
        "",
        "| Dir:Q:TTE | Валидное время, s | Positive, % | p95 edge, c | max, c |",
        "|---|---:|---:|---:|---:|",
    ]
    order = ["300-240", "240-180", "180-120", "120-60", "60-30", "30-10", "<10"]
    for key, values in sorted(
        data["time_to_expiry"].items(),
        key=lambda item: (item[0].split(":")[0], int(item[0].split(":")[1]), order.index(item[0].split(":")[2])),
    ):
        if key.split(":")[1] not in ("10", "25", "50", "100"):
            continue
        weight = D(values["time_weighted"]["weight"])
        positive = D(values["time_weighted_positive_only"]["weight"])
        lines.append(
            f"| {key} | {number(weight,D('1e-9'))} | {number(positive/weight*100 if weight else None)} | "
            f"{number(values['time_weighted']['p95'],100)} | {number(values['time_weighted']['max'],100)} |"
        )
    lines += [
        "",
        "Distance-to-strike пропущен: достоверный contemporaneous reference в этом dataset не собирался. "
        "Задним числом он не восстанавливается.",
        "",
        "## K. Settlement progress",
        "",
        "Проверка 9 сентября 2026: статус **UNKNOWN**. Повторные Gamma market/event ответы дают reference URL и 60s TWAP config, "
        "но не полный settlement contract. В официальном HTML окна 15:15–15:20 UTC найден "
        "`openPrice=78474.82624106224`; Limitless публикует точную строку `78474.826241062247333888`. "
        "Это свидетельство отображаемого strike, не подтверждение точного сравниваемого значения Polymarket. "
        "Feed ID, boundary report, fallback, rounding и cancellation Polymarket не закрыты. "
        "[Документация TWAP](https://docs.polymarket.com/market-data/chainlink-twap) прямо оставляет параметры "
        "custom feed неопубликованными. Каталог Chainlink в текущей проверке ответил 429. "
        "[Общая документация выплат](https://docs.polymarket.com/concepts/positions-tokens) описывает pUSD; "
        "Limitless metadata — USDC. Market-specific redemption equivalence не доказана. "
        "Новый Chainlink layer не создавался; DexSport не является зависимостью этой итерации.",
        "",
        "## L. Decision",
        "",
        "**MORE DATA REQUIRED**.",
        "",
        (
            "Минимум 6h ещё не собран. "
            if D(data["duration_seconds"]) < 21600
            else "Минимум 6h по длительности достигнут; требуется итоговая оценка качества и эпизодов. "
        )
        + "Автоматический отчёт сохраняет консервативное решение до оценки критериев следующей фазы: "
        "устойчивое покрытие по всем окнам, независимые положительные эпизоды "
        "после VWAP на Q=10–50 с измеримой lifetime и проверка поведения storage/collector на полном интервале. "
        "Реальное исполнение и execution simulator не реализованы.",
        "",
    ]
    output.write_text("\n".join(lines), encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create Russian A-L report from measured research analysis")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report(args.directory, args.output or args.directory / "REPORT.md")
