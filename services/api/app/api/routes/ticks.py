from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.candles.storage import serialize_tick
from app.db.models import Asset, AssetSymbol, PriceTick
from app.db.session import get_db

router = APIRouter()


@router.get("")
def list_ticks(
    asset: AssetSymbol | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(PriceTick).join(Asset).options(joinedload(PriceTick.asset))
    if asset:
        query = query.where(Asset.symbol == asset)
    if from_ts:
        query = query.where(PriceTick.timestamp >= from_ts)
    if to_ts:
        query = query.where(PriceTick.timestamp < to_ts)
    ticks = db.scalars(query.order_by(PriceTick.timestamp).limit(limit)).all()
    return [serialize_tick(tick) for tick in ticks]
