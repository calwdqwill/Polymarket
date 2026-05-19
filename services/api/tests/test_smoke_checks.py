import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.candles.smoke_checks import validate_smoke_candles
from app.db.models import AssetSymbol, Candle


def make_candle(candle_id: int, start: datetime, low: str = "64000", high: str = "66000") -> Candle:
    return Candle(
        id=candle_id,
        asset_id=1,
        timeframe="5m",
        timestamp_start=start,
        timestamp_end=start + timedelta(minutes=5),
        open=Decimal("65000"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("65500"),
        source="chainlink_candlestick",
    )


class SmokeChecksTest(unittest.TestCase):
    def test_accepts_contiguous_realistic_btc_candles(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=timezone.utc)
        candles = [make_candle(index + 1, start + timedelta(minutes=5 * index)) for index in range(3)]

        issues = validate_smoke_candles(candles, asset_symbol=AssetSymbol.BTC)

        self.assertEqual(issues, [])

    def test_flags_gaps_and_suspicious_price_scale(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=timezone.utc)
        candles = [
            make_candle(1, start, low="0.000064", high="0.000066"),
            make_candle(2, start + timedelta(minutes=10), low="0.000064", high="0.000066"),
        ]

        issues = validate_smoke_candles(candles, asset_symbol=AssetSymbol.BTC)
        codes = {issue.code for issue in issues}

        self.assertIn("unexpected_timestamp_step", codes)
        self.assertIn("suspicious_price_scale", codes)

    def test_flags_missing_candles(self) -> None:
        issues = validate_smoke_candles([], asset_symbol=AssetSymbol.BTC)

        self.assertEqual(issues[0].code, "no_candles")


if __name__ == "__main__":
    unittest.main()
