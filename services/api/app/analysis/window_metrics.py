from dataclasses import dataclass
from decimal import Decimal

from app.db.models import Candle, CandleColor


@dataclass(frozen=True)
class WindowMetrics:
    percentage_change: Decimal
    absolute_change: Decimal
    max_high: Decimal
    min_low: Decimal
    range_percent: Decimal
    green_count: int
    red_count: int
    neutral_count: int
    average_body: Decimal
    average_upper_wick: Decimal
    average_lower_wick: Decimal
    average_body_to_range_ratio: Decimal


def calculate_window_metrics(candles: list[Candle]) -> WindowMetrics:
    if not candles:
        raise ValueError("Список candles не может быть пустым")

    first_open = Decimal(candles[0].open)
    last_close = Decimal(candles[-1].close)
    max_high = max(Decimal(candle.high) for candle in candles)
    min_low = min(Decimal(candle.low) for candle in candles)

    bodies: list[Decimal] = []
    upper_wicks: list[Decimal] = []
    lower_wicks: list[Decimal] = []
    body_to_range_ratios: list[Decimal] = []

    for candle in candles:
        open_price = Decimal(candle.open)
        close_price = Decimal(candle.close)
        high = Decimal(candle.high)
        low = Decimal(candle.low)
        body = abs(close_price - open_price)
        full_range = high - low
        bodies.append(body)
        upper_wicks.append(high - max(open_price, close_price))
        lower_wicks.append(min(open_price, close_price) - low)
        body_to_range_ratios.append(body / full_range if full_range else Decimal("0"))

    return WindowMetrics(
        percentage_change=((last_close - first_open) / first_open * Decimal("100")) if first_open else Decimal("0"),
        absolute_change=last_close - first_open,
        max_high=max_high,
        min_low=min_low,
        range_percent=((max_high - min_low) / first_open * Decimal("100")) if first_open else Decimal("0"),
        green_count=sum(1 for candle in candles if candle.color == CandleColor.GREEN),
        red_count=sum(1 for candle in candles if candle.color == CandleColor.RED),
        neutral_count=sum(1 for candle in candles if candle.color == CandleColor.NEUTRAL),
        average_body=sum(bodies) / len(bodies),
        average_upper_wick=sum(upper_wicks) / len(upper_wicks),
        average_lower_wick=sum(lower_wicks) / len(lower_wicks),
        average_body_to_range_ratio=sum(body_to_range_ratios) / len(body_to_range_ratios),
    )
