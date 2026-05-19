from decimal import Decimal

from app.db.models import Candle


def calculate_candle_features(candle: Candle) -> dict[str, str]:
    open_price = Decimal(candle.open)
    close_price = Decimal(candle.close)
    high = Decimal(candle.high)
    low = Decimal(candle.low)
    body = abs(close_price - open_price)
    full_range = high - low
    upper_wick = high - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low

    def pct(value: Decimal) -> Decimal:
        return value / open_price * Decimal("100") if open_price else Decimal("0")

    return {
        "upper_wick": str(upper_wick),
        "lower_wick": str(lower_wick),
        "body": str(body),
        "full_range": str(full_range),
        "upper_wick_percent": str(pct(upper_wick)),
        "lower_wick_percent": str(pct(lower_wick)),
        "body_percent": str(pct(body)),
        "body_to_range_ratio": str(body / full_range if full_range else Decimal("0")),
    }
