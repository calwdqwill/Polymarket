from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset
from app.db.session import get_db

router = APIRouter()


@router.get("")
def list_assets(db: Session = Depends(get_db)) -> list[dict]:
    assets = db.scalars(select(Asset).order_by(Asset.symbol)).all()
    return [
        {
            "id": asset.id,
            "symbol": asset.symbol.value,
            "name": asset.name,
            "source_config": asset.source_config,
        }
        for asset in assets
    ]
