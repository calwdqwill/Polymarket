from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.analysis.window_metrics import calculate_window_metrics
from app.candles.storage import build_candle_query, decimal_to_float
from app.db.models import AssetSymbol, Candle
from app.db.session import get_db

router = APIRouter()


def serialize_window_metrics(candles: list[Candle]) -> dict:
    metrics = calculate_window_metrics(candles)
    return {
        "timestamp_start": candles[0].timestamp_start.isoformat(),
        "timestamp_end": candles[-1].timestamp_end.isoformat(),
        "percentage_change": decimal_to_float(metrics.percentage_change),
        "absolute_change": decimal_to_float(metrics.absolute_change),
        "max_high": decimal_to_float(metrics.max_high),
        "min_low": decimal_to_float(metrics.min_low),
        "range_percent": decimal_to_float(metrics.range_percent),
        "green_count": metrics.green_count,
        "red_count": metrics.red_count,
        "neutral_count": metrics.neutral_count,
        "average_body": decimal_to_float(metrics.average_body),
        "average_upper_wick": decimal_to_float(metrics.average_upper_wick),
        "average_lower_wick": decimal_to_float(metrics.average_lower_wick),
        "average_body_to_range_ratio": decimal_to_float(metrics.average_body_to_range_ratio),
    }


@router.get("/window")
def get_window_analysis(
    asset: AssetSymbol,
    timeframe: str = "5m",
    source: str | None = None,
    window: int = Query(default=10, ge=1, le=500),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    candles = db.scalars(
        build_candle_query(asset_symbol=asset, timeframe=timeframe, source=source, from_ts=from_ts, to_ts=to_ts)
        .options(joinedload(Candle.asset))
        .limit(limit)
    ).all()

    windows = []
    for index in range(0, max(len(candles) - window + 1, 0)):
        window_candles = candles[index : index + window]
        windows.append(serialize_window_metrics(window_candles))

    return {
        "asset": asset.value,
        "timeframe": timeframe,
        "source": source,
        "window": window,
        "candles_loaded": len(candles),
        "windows": windows,
    }
