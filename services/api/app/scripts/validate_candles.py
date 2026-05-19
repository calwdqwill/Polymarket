import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import Row, func, select
from sqlalchemy.orm import joinedload

from app.candles.storage import CHAINLINK_CANDLE_SOURCE, build_candle_query, get_asset_by_symbol
from app.candles.validator import CandleValidationIssue, expected_step_for_timeframe
from app.candles.validator import validate_candle, validate_candle_sequence
from app.core.config import settings
from app.db.models import AssetSymbol, Candle
from app.db.session import SessionLocal
from app.scripts.backfill import parse_datetime


@dataclass(frozen=True)
class ValidationSummary:
    asset: str
    timeframe: str
    source: str | None
    start: str
    end: str
    candle_count: int
    duplicate_groups: int
    error_count: int
    warning_count: int
    first_candle: str | None
    last_candle: str | None
    expected_step_seconds: int


@dataclass(frozen=True)
class ValidationRecord:
    asset: str
    code: str
    message: str
    candle_id: int | None
    severity: str


def parse_assets(value: str) -> list[AssetSymbol]:
    if value.lower() == "all":
        return [AssetSymbol.BTC, AssetSymbol.ETH, AssetSymbol.SOL]
    return [AssetSymbol(item.strip().upper()) for item in value.split(",") if item.strip()]


def parse_source(value: str) -> str | None:
    normalized = value.strip()
    return None if normalized.lower() in {"all", "*", ""} else normalized


def configure_output_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def load_candles(
    *,
    asset_symbol: AssetSymbol,
    timeframe: str,
    source: str | None,
    start: datetime,
    end: datetime,
) -> tuple[int, list[Candle]]:
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
        return asset.id, list(candles)


def load_duplicate_groups(
    *,
    asset_id: int,
    timeframe: str,
    source: str | None,
    start: datetime,
    end: datetime,
) -> Sequence[Row]:
    query = (
        select(Candle.timestamp_start, Candle.source, func.count(Candle.id).label("row_count"))
        .where(
            Candle.asset_id == asset_id,
            Candle.timeframe == timeframe,
            Candle.timestamp_start >= start,
            Candle.timestamp_start < end,
        )
        .group_by(Candle.asset_id, Candle.timeframe, Candle.timestamp_start, Candle.source)
        .having(func.count(Candle.id) > 1)
        .order_by(Candle.timestamp_start)
    )
    if source:
        query = query.where(Candle.source == source)

    with SessionLocal() as db:
        return db.execute(query).all()


def issue_record(asset_symbol: AssetSymbol, issue: CandleValidationIssue) -> ValidationRecord:
    return ValidationRecord(
        asset=asset_symbol.value,
        code=issue.code,
        message=issue.message,
        candle_id=issue.candle_id,
        severity=issue.severity,
    )


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_window_edges(
    *,
    candles: Sequence[Candle],
    start: datetime,
    end: datetime,
    timeframe: str,
    strict: bool,
) -> list[CandleValidationIssue]:
    if not candles:
        return []

    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp_start)
    first = ordered_candles[0]
    last = ordered_candles[-1]
    step = expected_step_for_timeframe(timeframe)
    severity = "error" if strict else "warning"
    issues: list[CandleValidationIssue] = []
    first_start = utc_naive(first.timestamp_start)
    last_start = utc_naive(last.timestamp_start)
    window_start = utc_naive(start)
    window_end = utc_naive(end)

    if first_start > window_start:
        issues.append(
            CandleValidationIssue(
                "window_starts_after_requested_from",
                (
                    f"первая свеча {first.timestamp_start.isoformat()} позже начала окна "
                    f"{start.isoformat()}"
                ),
                first.id,
                severity,
            )
        )
    if last_start + step < window_end:
        issues.append(
            CandleValidationIssue(
                "window_ends_before_requested_to",
                (
                    f"последняя свеча {last.timestamp_start.isoformat()} раньше конца окна "
                    f"{end.isoformat()}"
                ),
                last.id,
                severity,
            )
        )
    return issues


def validate_asset(
    *,
    asset_symbol: AssetSymbol,
    timeframe: str,
    source: str | None,
    start: datetime,
    end: datetime,
    strict_window: bool,
    allow_empty: bool,
) -> tuple[ValidationSummary, list[ValidationRecord]]:
    asset_id, candles = load_candles(
        asset_symbol=asset_symbol,
        timeframe=timeframe,
        source=source,
        start=start,
        end=end,
    )
    duplicate_groups = load_duplicate_groups(
        asset_id=asset_id,
        timeframe=timeframe,
        source=source,
        start=start,
        end=end,
    )

    issues: list[ValidationRecord] = []
    if not candles and not allow_empty:
        issues.append(
            issue_record(
                asset_symbol,
                CandleValidationIssue(
                    "no_candles",
                    f"нет свечей за период {start.isoformat()}..{end.isoformat()}",
                ),
            )
        )

    for row in duplicate_groups:
        issues.append(
            issue_record(
                asset_symbol,
                CandleValidationIssue(
                    "duplicate_candle_group",
                    (
                        f"найдены дубликаты timestamp_start={row.timestamp_start.isoformat()} "
                        f"source={row.source}: {row.row_count} rows"
                    ),
                ),
            )
        )

    candles_by_source: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        candles_by_source[candle.source].append(candle)
        for issue in validate_candle(candle, timeframe=timeframe, expected_source=source):
            issues.append(issue_record(asset_symbol, issue))

    for source_candles in candles_by_source.values():
        for issue in validate_candle_sequence(source_candles, timeframe=timeframe):
            issues.append(issue_record(asset_symbol, issue))
        for issue in validate_window_edges(
            candles=source_candles,
            start=start,
            end=end,
            timeframe=timeframe,
            strict=strict_window,
        ):
            issues.append(issue_record(asset_symbol, issue))

    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp_start)
    summary = ValidationSummary(
        asset=asset_symbol.value,
        timeframe=timeframe,
        source=source,
        start=start.isoformat(),
        end=end.isoformat(),
        candle_count=len(candles),
        duplicate_groups=len(duplicate_groups),
        error_count=sum(1 for issue in issues if issue.severity == "error"),
        warning_count=sum(1 for issue in issues if issue.severity == "warning"),
        first_candle=ordered_candles[0].timestamp_start.isoformat() if ordered_candles else None,
        last_candle=ordered_candles[-1].timestamp_start.isoformat() if ordered_candles else None,
        expected_step_seconds=int(expected_step_for_timeframe(timeframe).total_seconds()),
    )
    return summary, issues


def print_text_report(
    summaries: Sequence[ValidationSummary],
    issues: Sequence[ValidationRecord],
    *,
    limit_issues: int,
) -> None:
    print("Candle validation summary")
    for summary in summaries:
        source = summary.source or "all"
        print(
            f"- {summary.asset} {summary.timeframe} source={source}: "
            f"candles={summary.candle_count}, duplicates={summary.duplicate_groups}, "
            f"errors={summary.error_count}, warnings={summary.warning_count}, "
            f"first={summary.first_candle}, last={summary.last_candle}"
        )

    if not issues:
        print("No validation issues found.")
        return

    print("Validation issues:")
    for issue in issues[:limit_issues]:
        suffix = f" candle_id={issue.candle_id}" if issue.candle_id is not None else ""
        print(f"- [{issue.severity}] {issue.asset} {issue.code}: {issue.message}{suffix}")
    if len(issues) > limit_issues:
        print(f"... {len(issues) - limit_issues} more issues omitted. Increase --limit-issues to see more.")


def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(description="Validate candles for phase 5 backfill readiness.")
    parser.add_argument("--asset", default="all", help="BTC, ETH, SOL, all, or comma-separated list.")
    parser.add_argument("--from", dest="from_ts", default=None, help="ISO datetime for validation window start.")
    parser.add_argument("--to", dest="to_ts", default=None, help="ISO datetime for validation window end.")
    parser.add_argument("--days", type=int, default=settings.backfill_lookback_days, help="Lookback period in days.")
    parser.add_argument("--timeframe", default=settings.backfill_timeframe, help="Expected candle timeframe.")
    parser.add_argument("--source", default=CHAINLINK_CANDLE_SOURCE, help="Expected source, or all.")
    parser.add_argument("--strict-window", action="store_true", help="Treat partial start/end coverage as errors.")
    parser.add_argument("--allow-empty", action="store_true", help="Do not fail when no candles exist for an asset.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")
    parser.add_argument("--limit-issues", type=int, default=100, help="Maximum issues to print in text mode.")
    args = parser.parse_args()

    end = parse_datetime(args.to_ts) or datetime.now(timezone.utc)
    start = parse_datetime(args.from_ts) or (end - timedelta(days=args.days))
    assets = parse_assets(args.asset)
    source = parse_source(args.source)

    summaries: list[ValidationSummary] = []
    issues: list[ValidationRecord] = []
    for asset_symbol in assets:
        summary, asset_issues = validate_asset(
            asset_symbol=asset_symbol,
            timeframe=args.timeframe,
            source=source,
            start=start,
            end=end,
            strict_window=args.strict_window,
            allow_empty=args.allow_empty,
        )
        summaries.append(summary)
        issues.extend(asset_issues)

    if args.json:
        print(
            json.dumps(
                {
                    "summaries": [asdict(summary) for summary in summaries],
                    "issues": [asdict(issue) for issue in issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_text_report(summaries, issues, limit_issues=args.limit_issues)

    return 1 if any(issue.severity == "error" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
