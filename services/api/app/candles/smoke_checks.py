from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Sequence

from app.candles.validator import validate_ohlc
from app.db.models import AssetSymbol, Candle


@dataclass(frozen=True)
class SmokeCheckIssue:
    code: str
    message: str
    candle_id: int | None = None


@dataclass(frozen=True)
class PriceRange:
    min_low: Decimal
    max_high: Decimal


@dataclass(frozen=True)
class PriceBounds:
    min_price: Decimal
    max_price: Decimal


PRICE_BOUNDS_BY_ASSET = {
    AssetSymbol.BTC: PriceBounds(Decimal("10000"), Decimal("1000000")),
    AssetSymbol.ETH: PriceBounds(Decimal("100"), Decimal("100000")),
    AssetSymbol.SOL: PriceBounds(Decimal("1"), Decimal("10000")),
}

TIMEFRAME_STEPS = {
    "5m": timedelta(minutes=5),
}


def expected_step_for_timeframe(timeframe: str) -> timedelta:
    try:
        return TIMEFRAME_STEPS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported smoke-check timeframe: {timeframe}") from exc


def summarize_price_range(candles: Sequence[Candle]) -> PriceRange | None:
    if not candles:
        return None
    return PriceRange(
        min_low=min(Decimal(candle.low) for candle in candles),
        max_high=max(Decimal(candle.high) for candle in candles),
    )


def validate_smoke_candles(
    candles: Sequence[Candle],
    *,
    asset_symbol: AssetSymbol,
    timeframe: str = "5m",
) -> list[SmokeCheckIssue]:
    issues: list[SmokeCheckIssue] = []
    if not candles:
        return [SmokeCheckIssue("no_candles", "No candles were loaded for the smoke period.")]

    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp_start)
    for candle in ordered_candles:
        for issue in validate_ohlc(candle):
            issues.append(SmokeCheckIssue(issue.code, issue.message, issue.candle_id))

    expected_step = expected_step_for_timeframe(timeframe)
    for previous, current in zip(ordered_candles, ordered_candles[1:]):
        actual_step = current.timestamp_start - previous.timestamp_start
        if actual_step != expected_step:
            issues.append(
                SmokeCheckIssue(
                    "unexpected_timestamp_step",
                    (
                        f"Expected {expected_step} between candles, got {actual_step} "
                        f"at {current.timestamp_start.isoformat()}."
                    ),
                    current.id,
                )
            )

    price_range = summarize_price_range(ordered_candles)
    if price_range:
        bounds = PRICE_BOUNDS_BY_ASSET[asset_symbol]
        if price_range.min_low <= 0:
            issues.append(SmokeCheckIssue("non_positive_price", "Candle price is zero or negative."))
        if price_range.min_low < bounds.min_price or price_range.max_high > bounds.max_price:
            issues.append(
                SmokeCheckIssue(
                    "suspicious_price_scale",
                    (
                        f"Price range {price_range.min_low}..{price_range.max_high} is outside "
                        f"expected {asset_symbol.value} bounds {bounds.min_price}..{bounds.max_price}."
                    ),
                )
            )

    return issues
