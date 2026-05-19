from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.candles.builder import get_candle_color
from app.db.models import Asset, AssetSymbol, Candle, PriceTick
from app.feeds.chainlink import ChainlinkCandle

CHAINLINK_CANDLE_SOURCE = "chainlink_candlestick"
BINANCE_KLINE_SOURCE = "binance_klines"


class CandleInput(Protocol):
    timestamp_start: datetime
    timestamp_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    raw_payload: dict


def decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def get_asset_by_symbol(db: Session, symbol: AssetSymbol | str) -> Asset:
    normalized = symbol if isinstance(symbol, AssetSymbol) else AssetSymbol(symbol.upper())
    asset = db.scalar(select(Asset).where(Asset.symbol == normalized))
    if not asset:
        raise ValueError(f"Актив {normalized.value} не найден. Сначала выполни seed assets.")
    return asset


def timestamp_lookup_key(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def build_candle_query(
    *,
    asset_symbol: AssetSymbol | None = None,
    timeframe: str = "5m",
    source: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> Select[tuple[Candle]]:
    query = select(Candle).join(Asset).where(Candle.timeframe == timeframe)
    if asset_symbol:
        query = query.where(Asset.symbol == asset_symbol)
    if source:
        query = query.where(Candle.source == source)
    if from_ts:
        query = query.where(Candle.timestamp_start >= from_ts)
    if to_ts:
        query = query.where(Candle.timestamp_start < to_ts)
    return query.order_by(Candle.timestamp_start)


def serialize_candle(candle: Candle) -> dict:
    return {
        "id": candle.id,
        "asset": candle.asset.symbol.value if candle.asset else None,
        "timeframe": candle.timeframe,
        "timestamp_start": candle.timestamp_start.isoformat(),
        "timestamp_end": candle.timestamp_end.isoformat(),
        "open": decimal_to_float(candle.open),
        "high": decimal_to_float(candle.high),
        "low": decimal_to_float(candle.low),
        "close": decimal_to_float(candle.close),
        "volume": decimal_to_float(candle.volume),
        "tick_count": candle.tick_count,
        "first_tick_time": candle.first_tick_time.isoformat() if candle.first_tick_time else None,
        "last_tick_time": candle.last_tick_time.isoformat() if candle.last_tick_time else None,
        "color": candle.color.value if candle.color else None,
        "source": candle.source,
        "created_at": candle.created_at.isoformat() if candle.created_at else None,
        "updated_at": candle.updated_at.isoformat() if candle.updated_at else None,
    }


def serialize_tick(tick: PriceTick) -> dict:
    return {
        "id": tick.id,
        "asset": tick.asset.symbol.value if tick.asset else None,
        "timestamp": tick.timestamp.isoformat(),
        "price": decimal_to_float(tick.price),
        "source": tick.source,
        "raw_payload": tick.raw_payload,
        "created_at": tick.created_at.isoformat() if tick.created_at else None,
    }


def upsert_candles(
    db: Session,
    *,
    asset: Asset,
    candles: list[CandleInput],
    timeframe: str = "5m",
    source: str,
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    timestamp_starts = [candle_input.timestamp_start for candle_input in candles]
    existing_by_start = {}
    if timestamp_starts:
        existing_candles = db.scalars(
            select(Candle).where(
                Candle.asset_id == asset.id,
                Candle.timeframe == timeframe,
                Candle.source == source,
                Candle.timestamp_start.in_(timestamp_starts),
            )
        ).all()
        existing_by_start = {timestamp_lookup_key(candle.timestamp_start): candle for candle in existing_candles}

    for candle_input in candles:
        existing = existing_by_start.get(timestamp_lookup_key(candle_input.timestamp_start))
        color = get_candle_color(candle_input.open, candle_input.close)
        if existing:
            existing.timestamp_end = candle_input.timestamp_end
            existing.open = candle_input.open
            existing.high = candle_input.high
            existing.low = candle_input.low
            existing.close = candle_input.close
            existing.volume = candle_input.volume
            existing.tick_count = 0
            existing.first_tick_time = None
            existing.last_tick_time = None
            existing.color = color
            existing.raw_payload = candle_input.raw_payload
            updated += 1
            continue

        db.add(
            Candle(
                asset_id=asset.id,
                timeframe=timeframe,
                timestamp_start=candle_input.timestamp_start,
                timestamp_end=candle_input.timestamp_end,
                open=candle_input.open,
                high=candle_input.high,
                low=candle_input.low,
                close=candle_input.close,
                volume=candle_input.volume,
                tick_count=0,
                first_tick_time=None,
                last_tick_time=None,
                color=color,
                source=source,
                raw_payload=candle_input.raw_payload,
            )
        )
        inserted += 1

    db.commit()
    return inserted, updated


def upsert_chainlink_candles(
    db: Session,
    *,
    asset: Asset,
    candles: list[ChainlinkCandle],
    timeframe: str = "5m",
    source: str = CHAINLINK_CANDLE_SOURCE,
) -> tuple[int, int]:
    return upsert_candles(db, asset=asset, candles=candles, timeframe=timeframe, source=source)
