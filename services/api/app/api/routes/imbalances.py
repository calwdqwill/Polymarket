from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.analysis.imbalance_storage import serialize_imbalance_event
from app.db.models import Asset, AssetSymbol, ImbalanceEvent
from app.db.session import get_db

router = APIRouter()


@router.get("")
def list_imbalances(
    asset: AssetSymbol | None = None,
    timeframe: str = "5m",
    source: str | None = None,
    direction: str | None = None,
    event_type: str | None = None,
    min_severity: Decimal | None = Query(default=None, ge=0),
    window_size: int | None = Query(default=None, ge=1),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(ImbalanceEvent).join(Asset).where(ImbalanceEvent.timeframe == timeframe)
    if asset:
        query = query.where(Asset.symbol == asset)
    if source:
        query = query.where(ImbalanceEvent.metadata_json["source"].as_string() == source)
    if direction:
        query = query.where(ImbalanceEvent.direction == direction)
    if event_type:
        query = query.where(ImbalanceEvent.event_type == event_type)
    if min_severity is not None:
        query = query.where(ImbalanceEvent.severity >= min_severity)
    if window_size is not None:
        query = query.where(ImbalanceEvent.window_size == window_size)
    if from_ts:
        query = query.where(ImbalanceEvent.timestamp_start >= from_ts)
    if to_ts:
        query = query.where(ImbalanceEvent.timestamp_start < to_ts)
    events = db.scalars(
        query.options(joinedload(ImbalanceEvent.asset)).order_by(ImbalanceEvent.timestamp_start.desc()).limit(limit)
    ).all()

    return [serialize_imbalance_event(event) for event in events]
