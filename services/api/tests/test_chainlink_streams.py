import hashlib
import hmac
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.feeds.chainlink_streams import ChainlinkStreamsClient


class ChainlinkStreamsClientTest(unittest.TestCase):
    def test_make_headers_uses_hmac_auth_scheme(self) -> None:
        client = ChainlinkStreamsClient(
            base_url="https://example.test",
            api_key="user-id",
            api_secret="secret",
        )
        timestamp_ms = 1_716_211_845_123
        full_path = "/api/v1/reports/latest?feedID=0xabc"

        headers = client.make_headers(method="GET", full_path=full_path, timestamp_ms=timestamp_ms)

        body_hash = hashlib.sha256(b"").hexdigest()
        string_to_sign = f"GET {full_path} {body_hash} user-id {timestamp_ms}"
        expected_signature = hmac.new(b"secret", string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(headers["Authorization"], "user-id")
        self.assertEqual(headers["X-Authorization-Timestamp"], str(timestamp_ms))
        self.assertEqual(headers["X-Authorization-Signature-SHA256"], expected_signature)

    def test_build_full_path_includes_query_parameters(self) -> None:
        client = ChainlinkStreamsClient(base_url="https://example.test", api_key="user-id", api_secret="secret")

        self.assertEqual(
            client.build_full_path("/api/v1/reports/latest", {"feedID": "0xabc"}),
            "/api/v1/reports/latest?feedID=0xabc",
        )

    def test_parse_latest_report_payload_decodes_price_words(self) -> None:
        feed_id = "0x" + "aa" * 32
        words = [
            "aa" * 32,
            f"{1_700_000_000:064x}",
            f"{1_700_000_010:064x}",
            f"{0:064x}",
            f"{0:064x}",
            f"{1_700_000_300:064x}",
            f"{83562965382026515000:064x}",
            f"{83552751500000000000:064x}",
            f"{83569504467836665000:064x}",
        ]
        payload = {
            "report": {
                "feedID": feed_id,
                "validFromTimestamp": 1_700_000_000,
                "observationsTimestamp": 1_700_000_010,
                "fullReport": "0x" + ("00" * 64) + "".join(words),
            }
        }
        client = ChainlinkStreamsClient(base_url="https://example.test", api_key="user-id", api_secret="secret")

        report = client.parse_latest_report_payload(payload, feed_id=feed_id)

        self.assertEqual(report.price, Decimal("83.562965382026515"))
        self.assertEqual(report.bid, Decimal("83.5527515"))
        self.assertEqual(report.ask, Decimal("83.569504467836665"))
        self.assertEqual(report.observations_timestamp, datetime.fromtimestamp(1_700_000_010, tz=timezone.utc))


if __name__ == "__main__":
    unittest.main()
