from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Asset, AssetSymbol, Candle, DataSourceRun, PriceTick
from app.db.session import get_db

router = APIRouter()

SOURCE_META = {
    "binance_klines": {
        "label": "Binance historical",
        "kind": "historical_fallback",
        "notes": ["90d OHLCV fallback, used while Chainlink Candlestick historical auth is blocked."],
    },
    "chainlink_streams": {
        "label": "Chainlink accumulated realtime",
        "kind": "realtime_accumulated",
        "notes": ["Local 5m candles are accumulated only while poll_chainlink_streams is running."],
    },
    "chainlink_candlestick": {
        "label": "Chainlink Candlestick API",
        "kind": "historical_primary_candidate",
        "notes": ["Historical Chainlink candles require separate Candlestick API authorization."],
    },
}


@router.get("/status")
def get_status() -> dict[str, str | int]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "external",
        "timeframe": settings.backfill_timeframe,
    }


def serialize_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def datetime_age_seconds(value: datetime | str | None) -> int | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def serialize_run(run: DataSourceRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    return {
        "id": run.id,
        "source": run.source,
        "job_type": run.job_type,
        "status": status,
        "started_at": serialize_datetime(run.started_at),
        "finished_at": serialize_datetime(run.finished_at),
        "error": run.error,
        "checkpoint": run.checkpoint,
    }


def empty_asset_status() -> dict[str, int | str | None]:
    return {
        "candles": 0,
        "ticks": 0,
        "first_candle": None,
        "last_candle": None,
        "last_tick": None,
        "last_candle_age_seconds": None,
        "last_tick_age_seconds": None,
    }


@router.get("/status/sources")
def get_source_status(timeframe: str = "5m", db: Session = Depends(get_db)) -> dict[str, Any]:
    source_status: dict[str, dict[str, Any]] = {
        source: {
            "source": source,
            "label": meta["label"],
            "kind": meta["kind"],
            "notes": list(meta["notes"]),
            "total_candles": 0,
            "total_ticks": 0,
            "first_candle": None,
            "last_candle": None,
            "last_tick": None,
            "last_candle_age_seconds": None,
            "last_tick_age_seconds": None,
            "is_live": False,
            "blocked_reason": None,
            "latest_run": None,
            "assets": {symbol.value: empty_asset_status() for symbol in AssetSymbol},
        }
        for source, meta in SOURCE_META.items()
    }

    candle_rows = db.execute(
        select(
            Candle.source,
            Asset.symbol,
            func.count(Candle.id),
            func.min(Candle.timestamp_start),
            func.max(Candle.timestamp_start),
            func.coalesce(func.sum(Candle.tick_count), 0),
        )
        .join(Asset)
        .where(Candle.timeframe == timeframe)
        .group_by(Candle.source, Asset.symbol)
    ).all()

    for source, symbol, candles, first_candle, last_candle, ticks in candle_rows:
        if source not in source_status:
            source_status[source] = {
                "source": source,
                "label": source,
                "kind": "custom",
                "notes": [],
                "total_candles": 0,
                "total_ticks": 0,
                "first_candle": None,
                "last_candle": None,
                "last_tick": None,
                "last_candle_age_seconds": None,
                "last_tick_age_seconds": None,
                "is_live": False,
                "blocked_reason": None,
                "latest_run": None,
                "assets": {asset_symbol.value: empty_asset_status() for asset_symbol in AssetSymbol},
            }

        symbol_value = symbol.value if hasattr(symbol, "value") else str(symbol)
        asset_status = source_status[source]["assets"].setdefault(symbol_value, empty_asset_status())
        asset_status["candles"] = int(candles or 0)
        asset_status["ticks"] = int(ticks or 0)
        asset_status["first_candle"] = serialize_datetime(first_candle)
        asset_status["last_candle"] = serialize_datetime(last_candle)
        asset_status["last_candle_age_seconds"] = datetime_age_seconds(last_candle)

        source_status[source]["total_candles"] += int(candles or 0)
        if source_status[source]["first_candle"] is None or (
            first_candle is not None and str(first_candle) < str(source_status[source]["first_candle"])
        ):
            source_status[source]["first_candle"] = serialize_datetime(first_candle)
        if source_status[source]["last_candle"] is None or (
            last_candle is not None and str(last_candle) > str(source_status[source]["last_candle"])
        ):
            source_status[source]["last_candle"] = serialize_datetime(last_candle)
            source_status[source]["last_candle_age_seconds"] = datetime_age_seconds(last_candle)

    tick_rows = db.execute(
        select(
            PriceTick.source,
            Asset.symbol,
            func.count(PriceTick.id),
            func.max(PriceTick.timestamp),
        )
        .join(Asset)
        .group_by(PriceTick.source, Asset.symbol)
    ).all()

    for source, symbol, ticks, last_tick in tick_rows:
        if source not in source_status:
            continue
        symbol_value = symbol.value if hasattr(symbol, "value") else str(symbol)
        asset_status = source_status[source]["assets"].setdefault(symbol_value, empty_asset_status())
        asset_status["ticks"] = int(ticks or 0)
        asset_status["last_tick"] = serialize_datetime(last_tick)
        asset_status["last_tick_age_seconds"] = datetime_age_seconds(last_tick)
        source_status[source]["total_ticks"] += int(ticks or 0)
        if source_status[source]["last_tick"] is None or (
            last_tick is not None and str(last_tick) > str(source_status[source]["last_tick"])
        ):
            source_status[source]["last_tick"] = serialize_datetime(last_tick)
            source_status[source]["last_tick_age_seconds"] = datetime_age_seconds(last_tick)

    for source, status in source_status.items():
        run = db.scalar(
            select(DataSourceRun).where(DataSourceRun.source == source).order_by(DataSourceRun.started_at.desc()).limit(1)
        )
        status["latest_run"] = serialize_run(run)
        if source == "chainlink_candlestick" and run and run.error and "401" in run.error:
            status["blocked_reason"] = "Chainlink Candlestick API authorization failed with HTTP 401."
        if source == "chainlink_streams":
            last_tick_age = status["last_tick_age_seconds"]
            status["is_live"] = isinstance(last_tick_age, int) and last_tick_age <= settings.chainlink_poll_interval_seconds * 6

    return {
        "timeframe": timeframe,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": list(source_status.values()),
    }
