import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from math import ceil

from app.candles.aggregator import floor_to_5m
from app.candles.storage import BINANCE_KLINE_SOURCE, get_asset_by_symbol, upsert_candles
from app.candles.validator import expected_step_for_timeframe
from app.core.config import settings
from app.db.models import AssetSymbol, DataSourceRun, RunStatus
from app.db.session import SessionLocal
from app.feeds.binance import BinanceClient
from app.feeds.symbols import binance_symbol_for_asset
from app.scripts.backfill import parse_assets, parse_datetime


def configure_output_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def resolve_range(*, from_ts: str | None, to_ts: str | None, days: int, years: int | None) -> tuple[datetime, datetime]:
    end = parse_datetime(to_ts) or floor_to_5m(datetime.now(timezone.utc))
    if from_ts:
        start = parse_datetime(from_ts)
        if start is None:
            raise ValueError("Invalid --from value.")
        return floor_to_5m(start), floor_to_5m(end)

    lookback_days = years * 365 if years is not None else days
    return floor_to_5m(end - timedelta(days=lookback_days)), floor_to_5m(end)


def estimate_requests(*, start: datetime, end: datetime, timeframe: str, limit: int) -> int:
    step = expected_step_for_timeframe(timeframe)
    candle_count = max(0, ceil((end - start) / step))
    return ceil(candle_count / limit) if candle_count else 0


async def backfill_binance_asset(
    *,
    client: BinanceClient,
    asset_symbol: AssetSymbol,
    start: datetime,
    end: datetime,
    timeframe: str,
    limit: int,
    request_sleep: float,
    dry_run: bool,
) -> dict:
    binance_symbol = binance_symbol_for_asset(asset_symbol)
    expected_requests = estimate_requests(start=start, end=end, timeframe=timeframe, limit=limit)
    result = {
        "asset": asset_symbol.value,
        "source": BINANCE_KLINE_SOURCE,
        "binance_symbol": binance_symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "expected_requests": expected_requests,
        "requests": 0,
        "inserted": 0,
        "updated": 0,
    }

    if dry_run:
        return result

    step = expected_step_for_timeframe(timeframe)
    with SessionLocal() as db:
        asset = get_asset_by_symbol(db, asset_symbol)
        run = DataSourceRun(
            source=BINANCE_KLINE_SOURCE,
            job_type="backfill_5m",
            status=RunStatus.RUNNING,
            checkpoint={
                "asset": asset_symbol.value,
                "binance_symbol": binance_symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timeframe": timeframe,
            },
        )
        db.add(run)
        db.commit()

        cursor = start
        try:
            while cursor < end:
                klines = await client.fetch_klines(
                    symbol=binance_symbol,
                    start=cursor,
                    end=end,
                    interval=timeframe,
                    limit=limit,
                )
                result["requests"] += 1
                if not klines:
                    break

                filtered_klines = [kline for kline in klines if start <= kline.timestamp_start < end]
                inserted, updated = upsert_candles(
                    db,
                    asset=asset,
                    candles=filtered_klines,
                    timeframe=timeframe,
                    source=BINANCE_KLINE_SOURCE,
                )
                result["inserted"] += inserted
                result["updated"] += updated

                last_start = max(kline.timestamp_start for kline in klines)
                next_cursor = last_start + step
                cursor = next_cursor if next_cursor > cursor else cursor + step
                run.checkpoint = {
                    **(run.checkpoint or {}),
                    "last_request_start": filtered_klines[0].timestamp_start.isoformat() if filtered_klines else cursor.isoformat(),
                    "last_request_end": filtered_klines[-1].timestamp_start.isoformat() if filtered_klines else cursor.isoformat(),
                    "next_cursor": cursor.isoformat(),
                    "requests": result["requests"],
                    "inserted": result["inserted"],
                    "updated": result["updated"],
                }
                db.commit()

                if cursor < end and request_sleep > 0:
                    await asyncio.sleep(request_sleep)

            run.status = RunStatus.COMPLETED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(timezone.utc)
            run.error = str(exc)
            db.commit()
            raise

    return result


async def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(description="Backfill 5m spot candles from Binance public klines.")
    parser.add_argument("--asset", default="all", help="BTC, ETH, SOL, all, or comma-separated symbols.")
    parser.add_argument("--from", dest="from_ts", default=None, help="ISO datetime начала периода.")
    parser.add_argument("--to", dest="to_ts", default=None, help="ISO datetime конца периода.")
    parser.add_argument("--days", type=int, default=settings.backfill_lookback_days, help="Lookback в днях.")
    parser.add_argument("--years", type=int, default=None, help="Lookback в годах, например 2, 3 или 5.")
    parser.add_argument("--timeframe", default=settings.backfill_timeframe, help="Binance interval, MVP default 5m.")
    parser.add_argument("--limit", type=int, default=settings.binance_klines_limit, help="Klines per request, max 1000.")
    parser.add_argument("--request-sleep", type=float, default=settings.binance_request_sleep_seconds)
    parser.add_argument("--retry-attempts", type=int, default=settings.binance_retry_attempts)
    parser.add_argument("--retry-sleep", type=float, default=settings.binance_retry_sleep_seconds)
    parser.add_argument("--timeout", type=float, default=settings.binance_timeout_seconds)
    parser.add_argument("--dry-run", action="store_true", help="Показать план без запросов к Binance.")
    args = parser.parse_args()

    limit = min(args.limit, 1000)
    start, end = resolve_range(from_ts=args.from_ts, to_ts=args.to_ts, days=args.days, years=args.years)
    assets = parse_assets(args.asset)
    client = BinanceClient(
        limit=limit,
        retry_attempts=args.retry_attempts,
        retry_sleep=args.retry_sleep,
        timeout=args.timeout,
    )

    for asset_symbol in assets:
        result = await backfill_binance_asset(
            client=client,
            asset_symbol=asset_symbol,
            start=start,
            end=end,
            timeframe=args.timeframe,
            limit=limit,
            request_sleep=args.request_sleep,
            dry_run=args.dry_run,
        )
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
