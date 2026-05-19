from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.imbalance_engine import DetectedImbalance
from app.candles.storage import decimal_to_float
from app.db.models import Asset, ImbalanceEvent


def _decimal_to_db(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal("0.00000001"))


def _metadata_with_source(metadata: dict | None, source: str | None) -> dict:
    normalized = dict(metadata or {})
    if source:
        normalized["source"] = source
    return normalized


def serialize_imbalance_event(event: ImbalanceEvent) -> dict:
    return {
        "id": event.id,
        "asset": event.asset.symbol.value if event.asset else None,
        "timeframe": event.timeframe,
        "source": (event.metadata_json or {}).get("source"),
        "window_size": event.window_size,
        "timestamp_start": event.timestamp_start.isoformat(),
        "timestamp_end": event.timestamp_end.isoformat(),
        "direction": event.direction,
        "percent_change": decimal_to_float(event.percent_change),
        "z_score": decimal_to_float(event.z_score),
        "severity": decimal_to_float(event.severity),
        "event_type": event.event_type,
        "metadata": event.metadata_json,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def delete_imbalance_events(
    db: Session,
    *,
    asset: Asset,
    timeframe: str,
    start: datetime,
    end: datetime,
    source: str | None,
    event_types: Iterable[str] | None = None,
) -> int:
    query = select(ImbalanceEvent.id, ImbalanceEvent.metadata_json).where(
        ImbalanceEvent.asset_id == asset.id,
        ImbalanceEvent.timeframe == timeframe,
        ImbalanceEvent.timestamp_start >= start,
        ImbalanceEvent.timestamp_start < end,
    )
    if event_types:
        query = query.where(ImbalanceEvent.event_type.in_(list(event_types)))

    rows = db.execute(query).all()
    ids = [
        row.id
        for row in rows
        if source is None or (row.metadata_json or {}).get("source") == source
    ]
    if not ids:
        return 0

    db.execute(delete(ImbalanceEvent).where(ImbalanceEvent.id.in_(ids)))
    db.commit()
    return len(ids)


def insert_imbalance_events(
    db: Session,
    *,
    asset: Asset,
    timeframe: str,
    source: str | None,
    events: list[DetectedImbalance],
) -> int:
    inserted = 0
    for event in events:
        if event.timestamp_start is None or event.timestamp_end is None:
            continue
        db.add(
            ImbalanceEvent(
                asset_id=asset.id,
                timeframe=timeframe,
                window_size=event.window_size,
                timestamp_start=event.timestamp_start,
                timestamp_end=event.timestamp_end,
                direction=event.direction,
                percent_change=_decimal_to_db(event.percent_change),
                z_score=_decimal_to_db(event.z_score),
                severity=_decimal_to_db(event.severity),
                event_type=event.event_type,
                metadata_json=_metadata_with_source(event.metadata, source),
            )
        )
        inserted += 1

    db.commit()
    return inserted
