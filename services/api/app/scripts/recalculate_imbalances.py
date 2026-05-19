import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.analysis.imbalance_engine import ImbalanceDetectionConfig, detect_imbalances
from app.analysis.imbalance_storage import delete_imbalance_events, insert_imbalance_events
from app.candles.storage import BINANCE_KLINE_SOURCE, build_candle_query, get_asset_by_symbol
from app.core.config import settings
from app.db.models import AssetSymbol, Candle
from app.db.session import SessionLocal
from app.scripts.backfill import parse_assets, parse_datetime


@dataclass(frozen=True)
class RecalculateSummary:
    asset: str
    timeframe: str
    source: str
    start: str
    end: str
    candles_loaded: int
    events_detected: int
    events_deleted: int
    events_inserted: int
    event_counts: dict[str, int]
    dry_run: bool


def configure_output_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def parse_source(value: str) -> str | None:
    normalized = value.strip()
    return None if normalized.lower() in {"all", "*", ""} else normalized


def decimal_arg(value: str) -> Decimal:
    return Decimal(value)


def build_config(args: argparse.Namespace) -> ImbalanceDetectionConfig:
    return ImbalanceDetectionConfig(
        window_size=args.window,
        baseline_window=args.baseline_window,
        min_baseline_points=args.min_baseline_points,
        percent_move_threshold=args.percent_move_threshold,
        streak_length=args.streak_length,
        body_anomaly_multiplier=args.body_anomaly_multiplier,
        wick_anomaly_multiplier=args.wick_anomaly_multiplier,
        body_range_ratio_threshold=args.body_range_ratio_threshold,
        mean_deviation_threshold_percent=args.mean_deviation_threshold,
        median_deviation_threshold_percent=args.median_deviation_threshold,
        z_score_threshold=args.z_score_threshold,
        percentile_threshold=args.percentile_threshold,
        percentile_min_move_percent=args.percentile_min_move,
    )


def grouped_by_source(candles: list[Candle], source: str | None) -> dict[str, list[Candle]]:
    if source:
        return {source: candles}

    groups: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        groups[candle.source].append(candle)
    return dict(groups)


def recalculate_asset(
    *,
    asset_symbol: AssetSymbol,
    timeframe: str,
    source: str | None,
    start: datetime,
    end: datetime,
    config: ImbalanceDetectionConfig,
    replace: bool,
    dry_run: bool,
) -> list[RecalculateSummary]:
    with SessionLocal() as db:
        asset = get_asset_by_symbol(db, asset_symbol)
        candles = db.scalars(
            build_candle_query(
                asset_symbol=asset_symbol,
                timeframe=timeframe,
                source=source,
                from_ts=start,
                to_ts=end,
            ).options(joinedload(Candle.asset))
        ).all()

        summaries: list[RecalculateSummary] = []
        for candle_source, source_candles in grouped_by_source(list(candles), source).items():
            events = detect_imbalances(source_candles, config=config)
            deleted = 0
            inserted = 0
            if not dry_run:
                if replace:
                    deleted = delete_imbalance_events(
                        db,
                        asset=asset,
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        source=candle_source,
                    )
                inserted = insert_imbalance_events(
                    db,
                    asset=asset,
                    timeframe=timeframe,
                    source=candle_source,
                    events=events,
                )

            summaries.append(
                RecalculateSummary(
                    asset=asset_symbol.value,
                    timeframe=timeframe,
                    source=candle_source,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    candles_loaded=len(source_candles),
                    events_detected=len(events),
                    events_deleted=deleted,
                    events_inserted=inserted,
                    event_counts=dict(Counter(event.event_type for event in events)),
                    dry_run=dry_run,
                )
            )
        return summaries


def print_text_report(summaries: list[RecalculateSummary]) -> None:
    print("Imbalance recalculation summary")
    if not summaries:
        print("No candle sources found for the requested window.")
        return

    for summary in summaries:
        counts = ", ".join(f"{key}={value}" for key, value in sorted(summary.event_counts.items())) or "none"
        print(
            f"- {summary.asset} {summary.timeframe} source={summary.source}: "
            f"candles={summary.candles_loaded}, detected={summary.events_detected}, "
            f"deleted={summary.events_deleted}, inserted={summary.events_inserted}, events=[{counts}]"
        )


def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(description="Recalculate imbalance events from stored candles.")
    parser.add_argument("--asset", default="all", help="BTC, ETH, SOL, all, or comma-separated list.")
    parser.add_argument("--from", dest="from_ts", default=None, help="ISO datetime for window start.")
    parser.add_argument("--to", dest="to_ts", default=None, help="ISO datetime for window end.")
    parser.add_argument("--days", type=int, default=settings.backfill_lookback_days, help="Lookback period in days.")
    parser.add_argument("--timeframe", default=settings.backfill_timeframe, help="Candle timeframe.")
    parser.add_argument("--source", default=BINANCE_KLINE_SOURCE, help="Candle source, or all.")
    parser.add_argument("--window", type=int, default=12, help="N-candle percent-move window.")
    parser.add_argument("--baseline-window", type=int, default=36, help="Rolling baseline size.")
    parser.add_argument("--min-baseline-points", type=int, default=10)
    parser.add_argument("--percent-move-threshold", type=decimal_arg, default=Decimal("2.5"))
    parser.add_argument("--streak-length", type=int, default=5)
    parser.add_argument("--body-anomaly-multiplier", type=decimal_arg, default=Decimal("4"))
    parser.add_argument("--wick-anomaly-multiplier", type=decimal_arg, default=Decimal("5"))
    parser.add_argument("--body-range-ratio-threshold", type=decimal_arg, default=Decimal("0.95"))
    parser.add_argument("--mean-deviation-threshold", type=decimal_arg, default=Decimal("3"))
    parser.add_argument("--median-deviation-threshold", type=decimal_arg, default=Decimal("3"))
    parser.add_argument("--z-score-threshold", type=decimal_arg, default=Decimal("3.5"))
    parser.add_argument("--percentile-threshold", type=decimal_arg, default=Decimal("0.95"))
    parser.add_argument("--percentile-min-move", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--no-replace", action="store_true", help="Append events instead of replacing existing ones.")
    parser.add_argument("--dry-run", action="store_true", help="Detect and print summary without DB writes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")
    args = parser.parse_args()

    end = parse_datetime(args.to_ts) or datetime.now(timezone.utc)
    start = parse_datetime(args.from_ts) or (end - timedelta(days=args.days))
    source = parse_source(args.source)
    config = build_config(args)

    summaries: list[RecalculateSummary] = []
    for asset_symbol in parse_assets(args.asset):
        summaries.extend(
            recalculate_asset(
                asset_symbol=asset_symbol,
                timeframe=args.timeframe,
                source=source,
                start=start,
                end=end,
                config=config,
                replace=not args.no_replace,
                dry_run=args.dry_run,
            )
        )

    if args.json:
        print(json.dumps([asdict(summary) for summary in summaries], ensure_ascii=False, indent=2))
    else:
        print_text_report(summaries)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
