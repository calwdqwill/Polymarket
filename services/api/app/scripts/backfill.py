import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from app.candles.storage import CHAINLINK_CANDLE_SOURCE, get_asset_by_symbol, upsert_chainlink_candles
from app.core.config import settings
from app.db.models import AssetSymbol, DataSourceRun, RunStatus
from app.db.session import SessionLocal
from app.feeds.chainlink import ChainlinkClient
from app.feeds.symbols import chainlink_symbol_for_asset


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def chunk_range(start: datetime, end: datetime, chunk_days: int) -> list[tuple[datetime, datetime]]:
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    chunk_delta = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + chunk_delta, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


def parse_assets(value: str) -> list[AssetSymbol]:
    if value.lower() == "all":
        return [AssetSymbol.BTC, AssetSymbol.ETH, AssetSymbol.SOL]
    return [AssetSymbol(item.strip().upper()) for item in value.split(",") if item.strip()]


async def backfill_asset(
    *,
    client: ChainlinkClient,
    asset_symbol: AssetSymbol,
    start: datetime,
    end: datetime,
    timeframe: str,
    chunk_days: int,
    dry_run: bool,
) -> dict:
    chainlink_symbol = chainlink_symbol_for_asset(asset_symbol)
    chunks = chunk_range(start, end, chunk_days)
    result = {"asset": asset_symbol.value, "chunks": len(chunks), "inserted": 0, "updated": 0}

    if dry_run:
        return result

    client.require_credentials()

    with SessionLocal() as db:
        asset = get_asset_by_symbol(db, asset_symbol)
        run = DataSourceRun(
            source=CHAINLINK_CANDLE_SOURCE,
            job_type="backfill_5m",
            status=RunStatus.RUNNING,
            checkpoint={
                "asset": asset_symbol.value,
                "chainlink_symbol": chainlink_symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        db.add(run)
        db.commit()

        try:
            for chunk_start, chunk_end in chunks:
                candles = await client.fetch_candles(
                    symbol=chainlink_symbol,
                    start=chunk_start,
                    end=chunk_end,
                    resolution=timeframe,
                )
                inserted, updated = upsert_chainlink_candles(
                    db,
                    asset=asset,
                    candles=candles,
                    timeframe=timeframe,
                    source=CHAINLINK_CANDLE_SOURCE,
                )
                result["inserted"] += inserted
                result["updated"] += updated
                run.checkpoint = {
                    **(run.checkpoint or {}),
                    "last_chunk_start": chunk_start.isoformat(),
                    "last_chunk_end": chunk_end.isoformat(),
                }
                db.commit()

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
    parser = argparse.ArgumentParser(description="Backfill 5m свечей из Chainlink Candlestick API.")
    parser.add_argument("--asset", default="all", help="BTC, ETH, SOL или all. Можно передать BTC,ETH.")
    parser.add_argument("--from", dest="from_ts", default=None, help="ISO datetime начала периода.")
    parser.add_argument("--to", dest="to_ts", default=None, help="ISO datetime конца периода.")
    parser.add_argument("--days", type=int, default=settings.backfill_lookback_days, help="Lookback в днях.")
    parser.add_argument("--timeframe", default=settings.backfill_timeframe, help="Пока поддерживаем 5m.")
    parser.add_argument("--chunk-days", type=int, default=settings.backfill_chunk_days, help="Размер чанка в днях.")
    parser.add_argument("--dry-run", action="store_true", help="Показать план без запросов к Chainlink.")
    args = parser.parse_args()

    end = parse_datetime(args.to_ts) or datetime.now(timezone.utc)
    start = parse_datetime(args.from_ts) or (end - timedelta(days=args.days))
    assets = parse_assets(args.asset)
    client = ChainlinkClient()

    for asset_symbol in assets:
        try:
            result = await backfill_asset(
                client=client,
                asset_symbol=asset_symbol,
                start=start,
                end=end,
                timeframe=args.timeframe,
                chunk_days=args.chunk_days,
                dry_run=args.dry_run,
            )
        except RuntimeError as exc:
            print(f"Backfill cannot start: {exc}")
            return 2
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
