import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.analysis.imbalance_engine import ImbalanceDetectionConfig, detect_imbalances
from app.candles.builder import get_candle_color
from app.db.models import Candle


def make_candle(
    candle_id: int,
    start: datetime,
    open_price: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    open_decimal = Decimal(open_price)
    close_decimal = Decimal(close)
    return Candle(
        id=candle_id,
        asset_id=1,
        timeframe="5m",
        timestamp_start=start,
        timestamp_end=start + timedelta(minutes=5),
        open=open_decimal,
        high=Decimal(high),
        low=Decimal(low),
        close=close_decimal,
        source="binance_klines",
        color=get_candle_color(open_decimal, close_decimal),
    )


class ImbalanceEngineTest(unittest.TestCase):
    def test_detects_percent_move_and_green_streak(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=timezone.utc)
        candles = [
            make_candle(1, start, "100", "101.2", "99.8", "101"),
            make_candle(2, start + timedelta(minutes=5), "101", "102.2", "100.8", "102"),
            make_candle(3, start + timedelta(minutes=10), "102", "103.2", "101.8", "103"),
            make_candle(4, start + timedelta(minutes=15), "103", "104.2", "102.8", "104"),
        ]

        events = detect_imbalances(
            candles,
            ImbalanceDetectionConfig(
                window_size=3,
                percent_move_threshold=Decimal("2"),
                streak_length=4,
            ),
        )
        event_types = {event.event_type for event in events}

        self.assertIn("window_percent_move", event_types)
        self.assertIn("green_streak", event_types)

    def test_detects_body_wick_and_body_range_anomalies(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=timezone.utc)
        candles = [
            make_candle(index + 1, start + timedelta(minutes=5 * index), "100", "100.2", "99.9", "100.05")
            for index in range(5)
        ]
        candles.append(make_candle(6, start + timedelta(minutes=25), "100", "105", "99.9", "104.8"))
        candles.append(make_candle(7, start + timedelta(minutes=30), "104.8", "110", "104.7", "104.9"))

        events = detect_imbalances(
            candles,
            ImbalanceDetectionConfig(
                baseline_window=5,
                min_baseline_points=5,
                body_anomaly_multiplier=Decimal("3"),
                wick_anomaly_multiplier=Decimal("3"),
                body_range_ratio_threshold=Decimal("0.8"),
            ),
        )
        event_types = {event.event_type for event in events}

        self.assertIn("body_anomaly", event_types)
        self.assertIn("wick_anomaly", event_types)
        self.assertIn("body_range_imbalance", event_types)

    def test_detects_rolling_z_score_and_percentile_moves(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=timezone.utc)
        closes = ["100.10", "99.95", "100.20", "100.05", "100.30", "100.15", "100.40", "100.25"]
        candles: list[Candle] = []
        open_price = "100.00"
        for index, close in enumerate(closes):
            high = str(max(Decimal(open_price), Decimal(close)) + Decimal("0.05"))
            low = str(min(Decimal(open_price), Decimal(close)) - Decimal("0.05"))
            candles.append(
                make_candle(
                    index + 1,
                    start + timedelta(minutes=5 * index),
                    open_price,
                    high,
                    low,
                    close,
                )
            )
            open_price = close
        candles.append(make_candle(9, start + timedelta(minutes=40), open_price, "104", open_price, "103.50"))

        events = detect_imbalances(
            candles,
            ImbalanceDetectionConfig(
                baseline_window=8,
                min_baseline_points=5,
                mean_deviation_threshold_percent=Decimal("2"),
                median_deviation_threshold_percent=Decimal("2"),
                z_score_threshold=Decimal("2"),
                percentile_threshold=Decimal("0.8"),
                percentile_min_move_percent=Decimal("1"),
            ),
        )
        event_types = {event.event_type for event in events}

        self.assertIn("rolling_mean_deviation", event_types)
        self.assertIn("rolling_median_deviation", event_types)
        self.assertIn("z_score_move", event_types)
        self.assertIn("percentile_move", event_types)


if __name__ == "__main__":
    unittest.main()
