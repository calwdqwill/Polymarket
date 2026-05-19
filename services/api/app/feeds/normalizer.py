from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class NormalizedPriceTick:
    symbol: str
    timestamp: datetime
    price: Decimal
    source: str
    raw_payload: dict[str, Any]


def normalize_chainlink_tick(payload: dict[str, Any], *, symbol: str) -> NormalizedPriceTick:
    raise NotImplementedError("Нормализация Chainlink tick зависит от доступной response schema.")
