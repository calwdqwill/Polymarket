from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.analysis.window_metrics import calculate_window_metrics
from app.db.models import Candle, CandleColor

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ImbalanceDetectionConfig:
    window_size: int = 12
    baseline_window: int = 36
    min_baseline_points: int = 10
    percent_move_threshold: Decimal = Decimal("2.5")
    streak_length: int = 5
    body_anomaly_multiplier: Decimal = Decimal("4")
    wick_anomaly_multiplier: Decimal = Decimal("5")
    body_range_ratio_threshold: Decimal = Decimal("0.95")
    mean_deviation_threshold_percent: Decimal = Decimal("3")
    median_deviation_threshold_percent: Decimal = Decimal("3")
    z_score_threshold: Decimal = Decimal("3.5")
    percentile_threshold: Decimal = Decimal("0.95")
    percentile_min_move_percent: Decimal = Decimal("1")


@dataclass(frozen=True)
class DetectedImbalance:
    event_type: str
    direction: str
    severity: Decimal
    percent_change: Decimal | None = None
    z_score: Decimal | None = None
    metadata: dict | None = None
    window_size: int = 1
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None


@dataclass(frozen=True)
class CandleFeatures:
    candle: Candle
    body: Decimal
    upper_wick: Decimal
    lower_wick: Decimal
    full_range: Decimal
    body_percent: Decimal
    upper_wick_percent: Decimal
    lower_wick_percent: Decimal
    range_percent: Decimal
    body_to_range_ratio: Decimal
    candle_return_percent: Decimal
    close_return_percent: Decimal | None


def _percent_change(start: Decimal, end: Decimal) -> Decimal:
    return ((end - start) / start * ONE_HUNDRED) if start else ZERO


def _abs_percent(value: Decimal, base: Decimal) -> Decimal:
    return abs(value) / base * ONE_HUNDRED if base else ZERO


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values)) if values else ZERO


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return ZERO
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def _standard_deviation(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        return ZERO
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values))
    return variance.sqrt() if variance > 0 else ZERO


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        return ZERO
    ordered = sorted(values)
    clamped = min(max(percentile, ZERO), Decimal("1"))
    index = int((Decimal(len(ordered) - 1) * clamped).to_integral_value(rounding="ROUND_CEILING"))
    return ordered[index]


def _direction_from_change(change: Decimal) -> str:
    if change > 0:
        return "bullish"
    if change < 0:
        return "bearish"
    return "neutral"


def _direction_from_candle(candle: Candle) -> str:
    if Decimal(candle.close) > Decimal(candle.open):
        return "bullish"
    if Decimal(candle.close) < Decimal(candle.open):
        return "bearish"
    return "neutral"


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _metadata(**values: object) -> dict:
    metadata: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, Decimal):
            metadata[key] = str(value)
        elif value is not None:
            metadata[key] = value
    return metadata


def _build_features(candles: list[Candle]) -> list[CandleFeatures]:
    features: list[CandleFeatures] = []
    previous_close: Decimal | None = None
    for candle in candles:
        open_price = Decimal(candle.open)
        close_price = Decimal(candle.close)
        high = Decimal(candle.high)
        low = Decimal(candle.low)
        body = abs(close_price - open_price)
        full_range = high - low
        upper_wick = high - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low
        close_return_percent = _percent_change(previous_close, close_price) if previous_close is not None else None
        features.append(
            CandleFeatures(
                candle=candle,
                body=body,
                upper_wick=upper_wick,
                lower_wick=lower_wick,
                full_range=full_range,
                body_percent=_abs_percent(body, open_price),
                upper_wick_percent=_abs_percent(upper_wick, open_price),
                lower_wick_percent=_abs_percent(lower_wick, open_price),
                range_percent=_abs_percent(full_range, open_price),
                body_to_range_ratio=body / full_range if full_range else ZERO,
                candle_return_percent=_percent_change(open_price, close_price),
                close_return_percent=close_return_percent,
            )
        )
        previous_close = close_price
    return features


def _event(
    *,
    event_type: str,
    direction: str,
    severity: Decimal,
    percent_change: Decimal | None,
    z_score: Decimal | None = None,
    metadata: dict | None = None,
    window_size: int,
    timestamp_start: datetime,
    timestamp_end: datetime,
) -> DetectedImbalance:
    return DetectedImbalance(
        event_type=event_type,
        direction=direction,
        severity=abs(severity),
        percent_change=percent_change,
        z_score=z_score,
        metadata=metadata or {},
        window_size=window_size,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
    )


def detect_percent_move(candles: list[Candle], threshold_percent: Decimal) -> DetectedImbalance | None:
    metrics = calculate_window_metrics(candles)
    if abs(metrics.percentage_change) < threshold_percent:
        return None

    return _event(
        event_type="window_percent_move",
        direction=_direction_from_change(metrics.percentage_change),
        severity=abs(metrics.percentage_change),
        percent_change=metrics.percentage_change,
        metadata=_metadata(window_size=len(candles), threshold_percent=threshold_percent),
        window_size=len(candles),
        timestamp_start=candles[0].timestamp_start,
        timestamp_end=candles[-1].timestamp_end,
    )


def _detect_streak(features: list[CandleFeatures], index: int, config: ImbalanceDetectionConfig) -> DetectedImbalance | None:
    color = features[index].candle.color
    if color not in {CandleColor.GREEN, CandleColor.RED}:
        return None

    count = 0
    cursor = index
    while cursor >= 0 and features[cursor].candle.color == color:
        count += 1
        cursor -= 1

    if count != config.streak_length:
        return None

    streak_features = features[index - count + 1 : index + 1]
    start_price = Decimal(streak_features[0].candle.open)
    end_price = Decimal(streak_features[-1].candle.close)
    percent_change = _percent_change(start_price, end_price)
    event_type = "green_streak" if color == CandleColor.GREEN else "red_streak"
    direction = "bullish" if color == CandleColor.GREEN else "bearish"
    return _event(
        event_type=event_type,
        direction=direction,
        severity=abs(percent_change),
        percent_change=percent_change,
        metadata=_metadata(
            streak_length=count,
            candle_ids=[feature.candle.id for feature in streak_features if feature.candle.id is not None],
        ),
        window_size=count,
        timestamp_start=streak_features[0].candle.timestamp_start,
        timestamp_end=streak_features[-1].candle.timestamp_end,
    )


def _baseline(features: list[CandleFeatures], index: int, config: ImbalanceDetectionConfig) -> list[CandleFeatures]:
    start = max(0, index - config.baseline_window)
    baseline = features[start:index]
    return baseline if len(baseline) >= config.min_baseline_points else []


def _detect_body_anomaly(
    current: CandleFeatures,
    baseline: list[CandleFeatures],
    config: ImbalanceDetectionConfig,
) -> DetectedImbalance | None:
    baseline_body = _mean([feature.body_percent for feature in baseline])
    if baseline_body <= 0 or current.body_percent < baseline_body * config.body_anomaly_multiplier:
        return None

    severity = current.body_percent / baseline_body
    return _event(
        event_type="body_anomaly",
        direction=_direction_from_candle(current.candle),
        severity=severity,
        percent_change=current.candle_return_percent,
        metadata=_metadata(
            body_percent=current.body_percent,
            baseline_mean_body_percent=baseline_body,
            multiplier=config.body_anomaly_multiplier,
            candle_id=current.candle.id,
        ),
        window_size=1,
        timestamp_start=current.candle.timestamp_start,
        timestamp_end=current.candle.timestamp_end,
    )


def _detect_wick_anomalies(
    current: CandleFeatures,
    baseline: list[CandleFeatures],
    config: ImbalanceDetectionConfig,
) -> list[DetectedImbalance]:
    events: list[DetectedImbalance] = []
    checks = [
        ("upper", current.upper_wick_percent, _mean([feature.upper_wick_percent for feature in baseline]), "bearish"),
        ("lower", current.lower_wick_percent, _mean([feature.lower_wick_percent for feature in baseline]), "bullish"),
    ]
    for side, current_percent, baseline_percent, direction in checks:
        if baseline_percent <= 0 or current_percent < baseline_percent * config.wick_anomaly_multiplier:
            continue
        severity = current_percent / baseline_percent
        events.append(
            _event(
                event_type="wick_anomaly",
                direction=direction,
                severity=severity,
                percent_change=current.candle_return_percent,
                metadata=_metadata(
                    wick_side=side,
                    wick_percent=current_percent,
                    baseline_mean_wick_percent=baseline_percent,
                    multiplier=config.wick_anomaly_multiplier,
                    candle_id=current.candle.id,
                ),
                window_size=1,
                timestamp_start=current.candle.timestamp_start,
                timestamp_end=current.candle.timestamp_end,
            )
        )
    return events


def _detect_body_range_imbalance(
    current: CandleFeatures,
    config: ImbalanceDetectionConfig,
) -> DetectedImbalance | None:
    if current.full_range <= 0 or current.body_to_range_ratio < config.body_range_ratio_threshold:
        return None

    return _event(
        event_type="body_range_imbalance",
        direction=_direction_from_candle(current.candle),
        severity=current.body_to_range_ratio,
        percent_change=current.candle_return_percent,
        metadata=_metadata(
            body_to_range_ratio=current.body_to_range_ratio,
            threshold=config.body_range_ratio_threshold,
            candle_id=current.candle.id,
        ),
        window_size=1,
        timestamp_start=current.candle.timestamp_start,
        timestamp_end=current.candle.timestamp_end,
    )


def _detect_rolling_deviation(
    current: CandleFeatures,
    baseline: list[CandleFeatures],
    config: ImbalanceDetectionConfig,
) -> list[DetectedImbalance]:
    close = Decimal(current.candle.close)
    baseline_closes = [Decimal(feature.candle.close) for feature in baseline]
    mean_close = _mean(baseline_closes)
    median_close = _median(baseline_closes)
    checks = [
        ("rolling_mean_deviation", mean_close, config.mean_deviation_threshold_percent),
        ("rolling_median_deviation", median_close, config.median_deviation_threshold_percent),
    ]
    events: list[DetectedImbalance] = []
    for event_type, baseline_value, threshold in checks:
        deviation = _percent_change(baseline_value, close)
        if abs(deviation) < threshold:
            continue
        events.append(
            _event(
                event_type=event_type,
                direction=_direction_from_change(deviation),
                severity=abs(deviation),
                percent_change=deviation,
                metadata=_metadata(
                    close=close,
                    baseline_value=baseline_value,
                    threshold_percent=threshold,
                    baseline_points=len(baseline),
                    candle_id=current.candle.id,
                ),
                window_size=len(baseline) + 1,
                timestamp_start=baseline[0].candle.timestamp_start,
                timestamp_end=current.candle.timestamp_end,
            )
        )
    return events


def _detect_z_score(
    current: CandleFeatures,
    baseline: list[CandleFeatures],
    config: ImbalanceDetectionConfig,
) -> DetectedImbalance | None:
    if current.close_return_percent is None:
        return None
    baseline_returns = [feature.close_return_percent for feature in baseline if feature.close_return_percent is not None]
    if len(baseline_returns) < config.min_baseline_points:
        return None
    mean_return = _mean(baseline_returns)
    stdev_return = _standard_deviation(baseline_returns)
    if stdev_return <= 0:
        return None
    z_score = (current.close_return_percent - mean_return) / stdev_return
    if abs(z_score) < config.z_score_threshold:
        return None

    return _event(
        event_type="z_score_move",
        direction=_direction_from_change(current.close_return_percent),
        severity=abs(z_score),
        percent_change=current.close_return_percent,
        z_score=z_score,
        metadata=_metadata(
            baseline_mean_return_percent=mean_return,
            baseline_stdev_return_percent=stdev_return,
            threshold=config.z_score_threshold,
            baseline_points=len(baseline_returns),
            candle_id=current.candle.id,
        ),
        window_size=len(baseline) + 1,
        timestamp_start=baseline[0].candle.timestamp_start,
        timestamp_end=current.candle.timestamp_end,
    )


def _detect_percentile_move(
    current: CandleFeatures,
    baseline: list[CandleFeatures],
    config: ImbalanceDetectionConfig,
) -> DetectedImbalance | None:
    if current.close_return_percent is None:
        return None
    baseline_returns = [abs(feature.close_return_percent) for feature in baseline if feature.close_return_percent is not None]
    if len(baseline_returns) < config.min_baseline_points:
        return None
    threshold = max(
        _percentile(baseline_returns, config.percentile_threshold),
        config.percentile_min_move_percent,
    )
    if abs(current.close_return_percent) < threshold:
        return None

    return _event(
        event_type="percentile_move",
        direction=_direction_from_change(current.close_return_percent),
        severity=abs(current.close_return_percent),
        percent_change=current.close_return_percent,
        metadata=_metadata(
            percentile=str(config.percentile_threshold),
            percentile_threshold_percent=threshold,
            baseline_points=len(baseline_returns),
            candle_id=current.candle.id,
        ),
        window_size=len(baseline) + 1,
        timestamp_start=baseline[0].candle.timestamp_start,
        timestamp_end=current.candle.timestamp_end,
    )


def detect_imbalances(
    candles: list[Candle],
    config: ImbalanceDetectionConfig | None = None,
) -> list[DetectedImbalance]:
    if not candles:
        return []

    config = config or ImbalanceDetectionConfig()
    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp_start)
    features = _build_features(ordered_candles)
    events: list[DetectedImbalance] = []

    for index, current in enumerate(features):
        if index >= config.window_size - 1:
            window = ordered_candles[index - config.window_size + 1 : index + 1]
            percent_event = detect_percent_move(window, config.percent_move_threshold)
            if percent_event:
                events.append(percent_event)

        streak_event = _detect_streak(features, index, config)
        if streak_event:
            events.append(streak_event)

        baseline = _baseline(features, index, config)
        if not baseline:
            body_range_event = _detect_body_range_imbalance(current, config)
            if body_range_event:
                events.append(body_range_event)
            continue

        body_event = _detect_body_anomaly(current, baseline, config)
        if body_event:
            events.append(body_event)
        events.extend(_detect_wick_anomalies(current, baseline, config))

        body_range_event = _detect_body_range_imbalance(current, config)
        if body_range_event:
            events.append(body_range_event)
        events.extend(_detect_rolling_deviation(current, baseline, config))

        z_score_event = _detect_z_score(current, baseline, config)
        if z_score_event:
            events.append(z_score_event)

        percentile_event = _detect_percentile_move(current, baseline, config)
        if percentile_event:
            events.append(percentile_event)

    return events
