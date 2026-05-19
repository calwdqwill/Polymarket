from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Sequence

from app.db.models import Candle


@dataclass(frozen=True)
class CandleValidationIssue:
    code: str
    message: str
    candle_id: int | None = None
    severity: str = "error"


TIMEFRAME_STEPS = {
    "5m": timedelta(minutes=5),
}


def expected_step_for_timeframe(timeframe: str) -> timedelta:
    try:
        return TIMEFRAME_STEPS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported candle validation timeframe: {timeframe}") from exc


def validate_ohlc(candle: Candle) -> list[CandleValidationIssue]:
    issues: list[CandleValidationIssue] = []
    high = Decimal(candle.high)
    low = Decimal(candle.low)
    open_price = Decimal(candle.open)
    close_price = Decimal(candle.close)

    if low > high:
        issues.append(CandleValidationIssue("ohlc_low_gt_high", "low больше high", candle.id))
    if not low <= open_price <= high:
        issues.append(CandleValidationIssue("ohlc_open_out_of_range", "open вне диапазона low/high", candle.id))
    if not low <= close_price <= high:
        issues.append(CandleValidationIssue("ohlc_close_out_of_range", "close вне диапазона low/high", candle.id))
    if min(low, high, open_price, close_price) <= 0:
        issues.append(CandleValidationIssue("ohlc_non_positive_price", "OHLC содержит нулевую или отрицательную цену", candle.id))

    return issues


def validate_candle_timestamp(candle: Candle, *, timeframe: str = "5m") -> list[CandleValidationIssue]:
    issues: list[CandleValidationIssue] = []
    expected_step = expected_step_for_timeframe(timeframe)
    expected_end = candle.timestamp_start + expected_step

    if candle.timestamp_end != expected_end:
        issues.append(
            CandleValidationIssue(
                "timestamp_end_mismatch",
                (
                    f"timestamp_end должен быть {expected_end.isoformat()}, "
                    f"получено {candle.timestamp_end.isoformat()}"
                ),
                candle.id,
            )
        )

    if (
        candle.timestamp_start.minute % 5 != 0
        or candle.timestamp_start.second != 0
        or candle.timestamp_start.microsecond != 0
    ):
        issues.append(
            CandleValidationIssue(
                "timestamp_start_not_5m_aligned",
                f"timestamp_start не выровнен на 5m: {candle.timestamp_start.isoformat()}",
                candle.id,
            )
        )

    return issues


def validate_candle_source(candle: Candle, *, expected_source: str | None = None) -> list[CandleValidationIssue]:
    if not candle.source:
        return [CandleValidationIssue("missing_source", "source пустой", candle.id)]
    if expected_source and candle.source != expected_source:
        return [
            CandleValidationIssue(
                "unexpected_source",
                f"ожидался source={expected_source}, получено source={candle.source}",
                candle.id,
            )
        ]
    return []


def validate_candle(candle: Candle, *, timeframe: str = "5m", expected_source: str | None = None) -> list[CandleValidationIssue]:
    issues: list[CandleValidationIssue] = []
    issues.extend(validate_ohlc(candle))
    issues.extend(validate_candle_timestamp(candle, timeframe=timeframe))
    issues.extend(validate_candle_source(candle, expected_source=expected_source))
    return issues


def validate_gap(previous: Candle, current: Candle, *, timeframe: str = "5m") -> CandleValidationIssue | None:
    expected_start = previous.timestamp_start + expected_step_for_timeframe(timeframe)
    if current.timestamp_start != expected_start:
        return CandleValidationIssue(
            "candle_gap",
            f"ожидалась следующая свеча в {expected_start.isoformat()}, получено {current.timestamp_start.isoformat()}",
            current.id,
        )
    return None


def validate_5m_gap(previous: Candle, current: Candle) -> CandleValidationIssue | None:
    return validate_gap(previous, current, timeframe="5m")


def validate_candle_sequence(candles: Sequence[Candle], *, timeframe: str = "5m") -> list[CandleValidationIssue]:
    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp_start)
    return [
        issue
        for previous, current in zip(ordered_candles, ordered_candles[1:])
        if (issue := validate_gap(previous, current, timeframe=timeframe)) is not None
    ]
