from decimal import Decimal

from app.db.models import CandleColor


def get_candle_color(
    open_price: Decimal,
    close_price: Decimal,
    threshold_percent: Decimal = Decimal("0"),
) -> CandleColor:
    if open_price == 0:
        return CandleColor.NEUTRAL

    change_percent = abs((close_price - open_price) / open_price * Decimal("100"))
    if change_percent <= threshold_percent:
        return CandleColor.NEUTRAL
    if close_price > open_price:
        return CandleColor.GREEN
    return CandleColor.RED
