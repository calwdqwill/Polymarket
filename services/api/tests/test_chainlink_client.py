import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.feeds.chainlink import ChainlinkClient


class ChainlinkClientRowParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ChainlinkClient(base_url="https://example.test")
        self.client.price_scale = Decimal(10) ** 18

    def test_parse_row_normalizes_prices_and_zero_volume(self) -> None:
        candle = self.client._parse_row(
            "BTCUSD",
            [
                1_700_000_000,
                "65000000000000000000000",
                "66000000000000000000000",
                "64000000000000000000000",
                "65500000000000000000000",
                "0",
            ],
        )

        self.assertEqual(candle.symbol, "BTCUSD")
        self.assertEqual(candle.timestamp_start, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc))
        self.assertEqual(candle.open, Decimal("65000"))
        self.assertEqual(candle.high, Decimal("66000"))
        self.assertEqual(candle.low, Decimal("64000"))
        self.assertEqual(candle.close, Decimal("65500"))
        self.assertIsNone(candle.volume)

    def test_parse_row_accepts_millisecond_timestamps(self) -> None:
        candle = self.client._parse_row(
            "BTCUSD",
            [
                1_700_000_000_000,
                "65000000000000000000000",
                "66000000000000000000000",
                "64000000000000000000000",
                "65500000000000000000000",
            ],
        )

        self.assertEqual(candle.timestamp_start, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc))

    def test_parse_row_rejects_short_rows(self) -> None:
        with self.assertRaises(ValueError):
            self.client._parse_row("BTCUSD", [1_700_000_000, "65000"])

    def test_extract_access_token_accepts_nested_chainlink_payload(self) -> None:
        self.assertEqual(self.client._extract_access_token({"d": {"access_token": "token-value"}}), "token-value")

    def test_extract_rows_accepts_candles_payload(self) -> None:
        rows = [[1_700_000_000, "1", "2", "1", "2"]]

        self.assertEqual(self.client._extract_rows({"candles": rows}), rows)
        self.assertEqual(self.client._extract_rows({"d": {"candles": rows}}), rows)


if __name__ == "__main__":
    unittest.main()
