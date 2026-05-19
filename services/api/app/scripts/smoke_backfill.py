import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.candles.smoke_checks import SmokeCheckIssue, summarize_price_range, validate_smoke_candles
from app.candles.storage import CHAINLINK_CANDLE_SOURCE, build_candle_query, get_asset_by_symbol
from app.core.config import settings
from app.db.models import AssetSymbol, Candle
from app.db.session import SessionLocal
from app.feeds.chainlink import ChainlinkClient
from app.scripts.backfill import backfill_asset, parse_datetime


def count_duplicate_candles(
    *,
    asset_id: int,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> int:
    duplicate_groups = (
        select(Candle.timestamp_start)
        .where(
            Candle.asset_id == asset_id,
            Candle.timeframe == timeframe,
            Candle.source == CHAINLINK_CANDLE_SOURCE,
            Candle.timestamp_start >= start,
            Candle.timestamp_start < end,
        )
        .group_by(Candle.asset_id, Candle.timeframe, Candle.timestamp_start, Candle.source)
        .having(func.count(Candle.id) > 1)
        .subquery()
    )
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(duplicate_groups)) or 0


def load_smoke_candles(
    *,
    asset_symbol: AssetSymbol,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> tuple[int, list[Candle]]:
    with SessionLocal() as db:
        asset = get_asset_by_symbol(db, asset_symbol)
        candles = db.scalars(
            build_candle_query(
                asset_symbol=asset_symbol,
                timeframe=timeframe,
                source=CHAINLINK_CANDLE_SOURCE,
                from_ts=start,
                to_ts=end,
            ).options(joinedload(Candle.asset))
        ).all()
        return asset.id, list(candles)


async def run_smoke_backfill(
    *,
    asset_symbol: AssetSymbol,
    start: datetime,
    end: datetime,
    timeframe: str,
    chunk_days: int,
    repeat: bool,
) -> int:
    client = ChainlinkClient()
    client.require_credentials()

    first_result = await backfill_asset(
        client=client,
        asset_symbol=asset_symbol,
        start=start,
        end=end,
        timeframe=timeframe,
        chunk_days=chunk_days,
        dry_run=False,
    )
    second_result = None
    if repeat:
        second_result = await backfill_asset(
            client=client,
            asset_symbol=asset_symbol,
            start=start,
            end=end,
            timeframe=timeframe,
            chunk_days=chunk_days,
            dry_run=False,
        )

    asset_id, candles = load_smoke_candles(asset_symbol=asset_symbol, timeframe=timeframe, start=start, end=end)
    duplicate_count = count_duplicate_candles(asset_id=asset_id, timeframe=timeframe, start=start, end=end)

    issues = validate_smoke_candles(candles, asset_symbol=asset_symbol, timeframe=timeframe)
    if duplicate_count:
        issues.append(SmokeCheckIssue("duplicate_candles", f"Found {duplicate_count} duplicate candle timestamp groups."))
    if second_result and second_result["inserted"] != 0:
        issues.append(
            SmokeCheckIssue(
                "repeat_inserted_new_rows",
                f"Repeat backfill inserted {second_result['inserted']} rows instead of only updating existing candles.",
            )
        )

    price_range = summarize_price_range(candles)
    print(
        {
            "asset": asset_symbol.value,
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "first_run": first_result,
            "repeat_run": second_result,
            "candles_loaded": len(candles),
            "duplicate_groups": duplicate_count,
            "price_min_low": str(price_range.min_low) if price_range else None,
            "price_max_high": str(price_range.max_high) if price_range else None,
        }
    )

    if issues:
        print("Smoke check failed:")
        for issue in issues:
            suffix = f" candle_id={issue.candle_id}" if issue.candle_id is not None else ""
            print(f"- {issue.code}: {issue.message}{suffix}")
        return 1

    print("Smoke check passed.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run phase 4 Chainlink BTC smoke backfill and DB checks.")
    parser.add_argument("--asset", default="BTC", choices=[symbol.value for symbol in AssetSymbol])
    parser.add_argument("--from", dest="from_ts", default=None, help="ISO datetime for the smoke period start.")
    parser.add_argument("--to", dest="to_ts", default=None, help="ISO datetime for the smoke period end.")
    parser.add_argument("--days", type=int, default=1, help="Lookback period in days.")
    parser.add_argument("--timeframe", default=settings.backfill_timeframe)
    parser.add_argument("--chunk-days", type=int, default=1)
    parser.add_argument("--no-repeat", action="store_true", help="Skip the repeat run/idempotency check.")
    args = parser.parse_args()

    end = parse_datetime(args.to_ts) or datetime.now(timezone.utc)
    start = parse_datetime(args.from_ts) or (end - timedelta(days=args.days))
    try:
        return await run_smoke_backfill(
            asset_symbol=AssetSymbol(args.asset),
            start=start,
            end=end,
            timeframe=args.timeframe,
            chunk_days=args.chunk_days,
            repeat=not args.no_repeat,
        )
    except RuntimeError as exc:
        print(f"Smoke check cannot start: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
