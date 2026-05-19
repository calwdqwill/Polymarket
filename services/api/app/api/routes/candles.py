from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.analysis.drilldown import calculate_candle_features
from app.candles.storage import build_candle_query, serialize_candle, serialize_tick
from app.db.models import AssetSymbol, Candle, PriceTick
from app.db.session import get_db

router = APIRouter()


@router.get("")
def list_candles(
    asset: AssetSymbol | None = None,
    timeframe: str = "5m",
    source: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = (
        build_candle_query(asset_symbol=asset, timeframe=timeframe, source=source, from_ts=from_ts, to_ts=to_ts)
        .options(joinedload(Candle.asset))
        .limit(limit)
    )
    candles = db.scalars(query).all()
    return [serialize_candle(candle) for candle in candles]


@router.get("/{candle_id}/drilldown")
def get_candle_drilldown(candle_id: int, db: Session = Depends(get_db)) -> dict:
    candle = db.scalar(select(Candle).where(Candle.id == candle_id).options(joinedload(Candle.asset)))
    if not candle:
        raise HTTPException(status_code=404, detail=f"Свеча {candle_id} не найдена.")

    ticks = db.scalars(
        select(PriceTick)
        .where(
            PriceTick.asset_id == candle.asset_id,
            PriceTick.timestamp >= candle.timestamp_start,
            PriceTick.timestamp < candle.timestamp_end,
        )
        .options(joinedload(PriceTick.asset))
        .order_by(PriceTick.timestamp)
    ).all()

    features = calculate_candle_features(candle)
    return {
        "candle": serialize_candle(candle),
        "features": features,
        "ticks": [serialize_tick(tick) for tick in ticks],
        "tick_count": len(ticks),
        "has_realtime_ticks": len(ticks) > 0,
    }
