from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candles.aggregator import candle_end, floor_to_5m
from app.candles.builder import get_candle_color
from app.db.models import Asset, Candle, PriceTick
from app.feeds.chainlink_streams import ChainlinkStreamReport

CHAINLINK_STREAMS_SOURCE = "chainlink_streams"


def rebuild_stream_candles_from_ticks(
    db: Session,
    *,
    asset: Asset,
    timeframe: str = "5m",
    source: str = CHAINLINK_STREAMS_SOURCE,
) -> tuple[int, int]:
    ticks = db.scalars(
        select(PriceTick)
        .where(
            PriceTick.asset_id == asset.id,
            PriceTick.source == source,
        )
        .order_by(PriceTick.timestamp)
    ).all()
    grouped: dict[datetime, list[PriceTick]] = defaultdict(list)
    for tick in ticks:
        observed_at = tick.timestamp.astimezone(timezone.utc).replace(tzinfo=None) if tick.timestamp.tzinfo else tick.timestamp
        grouped[floor_to_5m(observed_at)].append(tick)

    inserted = 0
    updated = 0
    for timestamp_start, bucket_ticks in grouped.items():
        ordered_ticks = sorted(bucket_ticks, key=lambda tick: tick.timestamp)
        prices = [Decimal(tick.price) for tick in ordered_ticks]
        open_price = prices[0]
        close_price = prices[-1]
        high_price = max(prices)
        low_price = min(prices)
        first_tick_time = ordered_ticks[0].timestamp
        last_tick_time = ordered_ticks[-1].timestamp
        latest_payload = ordered_ticks[-1].raw_payload

        candle = db.scalar(
            select(Candle).where(
                Candle.asset_id == asset.id,
                Candle.timeframe == timeframe,
                Candle.timestamp_start == timestamp_start,
                Candle.source == source,
            )
        )
        if candle:
            candle.open = open_price
            candle.high = high_price
            candle.low = low_price
            candle.close = close_price
            candle.tick_count = len(ordered_ticks)
            candle.first_tick_time = first_tick_time
            candle.last_tick_time = last_tick_time
            candle.color = get_candle_color(open_price, close_price)
            candle.raw_payload = latest_payload
            updated += 1
        else:
            db.add(
                Candle(
                    asset_id=asset.id,
                    timeframe=timeframe,
                    timestamp_start=timestamp_start,
                    timestamp_end=candle_end(timestamp_start),
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=None,
                    tick_count=len(ordered_ticks),
                    first_tick_time=first_tick_time,
                    last_tick_time=last_tick_time,
                    color=get_candle_color(open_price, close_price),
                    source=source,
                    raw_payload=latest_payload,
                )
            )
            inserted += 1

    db.commit()
    return inserted, updated


def upsert_stream_report(
    db: Session,
    *,
    asset: Asset,
    report: ChainlinkStreamReport,
    timeframe: str = "5m",
    source: str = CHAINLINK_STREAMS_SOURCE,
) -> tuple[bool, Candle]:
    observed_at = report.observations_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    existing_tick = db.scalar(
        select(PriceTick).where(
            PriceTick.asset_id == asset.id,
            PriceTick.timestamp == observed_at,
            PriceTick.source == source,
        )
    )
    if existing_tick:
        candle = db.scalar(
            select(Candle).where(
                Candle.asset_id == asset.id,
                Candle.timeframe == timeframe,
                Candle.timestamp_start == floor_to_5m(observed_at),
                Candle.source == source,
            )
        )
        if not candle:
            raise RuntimeError("Existing Chainlink stream tick has no matching candle.")
        return False, candle

    tick = PriceTick(
        asset_id=asset.id,
        timestamp=observed_at,
        price=report.price,
        source=source,
        raw_payload=report.raw_payload,
    )
    db.add(tick)

    timestamp_start = floor_to_5m(observed_at)
    candle = db.scalar(
        select(Candle).where(
            Candle.asset_id == asset.id,
            Candle.timeframe == timeframe,
            Candle.timestamp_start == timestamp_start,
            Candle.source == source,
        )
    )

    if candle:
        candle.high = max(candle.high, report.price)
        candle.low = min(candle.low, report.price)
        candle.close = report.price
        candle.tick_count += 1
        candle.first_tick_time = min(candle.first_tick_time or observed_at, observed_at)
        candle.last_tick_time = max(candle.last_tick_time or observed_at, observed_at)
        candle.color = get_candle_color(candle.open, candle.close)
        candle.raw_payload = report.raw_payload
    else:
        candle = Candle(
            asset_id=asset.id,
            timeframe=timeframe,
            timestamp_start=timestamp_start,
            timestamp_end=candle_end(timestamp_start),
            open=report.price,
            high=report.price,
            low=report.price,
            close=report.price,
            volume=None,
            tick_count=1,
            first_tick_time=observed_at,
            last_tick_time=observed_at,
            color=get_candle_color(report.price, report.price),
            source=source,
            raw_payload=report.raw_payload,
        )
        db.add(candle)

    db.commit()
    db.refresh(candle)
    return True, candle
