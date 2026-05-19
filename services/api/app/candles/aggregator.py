from datetime import datetime, timedelta, timezone
from decimal import Decimal


def floor_to_5m(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    minute = timestamp.minute - (timestamp.minute % 5)
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def candle_end(timestamp_start: datetime) -> datetime:
    return timestamp_start + timedelta(minutes=5)


def update_ohlc(
    *,
    current_open: Decimal,
    current_high: Decimal,
    current_low: Decimal,
    price: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    return current_open, max(current_high, price), min(current_low, price)
