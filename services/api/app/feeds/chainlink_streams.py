import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class ChainlinkStreamsResponse:
    status_code: int
    body_preview: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChainlinkStreamReport:
    feed_id: str
    valid_from_timestamp: datetime
    observations_timestamp: datetime
    expires_at: datetime
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    raw_payload: dict[str, Any]


class ChainlinkStreamsClient:
    """HMAC-authenticated Chainlink Data Streams REST API client."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.chainlink_streams_base_url).rstrip("/")
        self.api_key = api_key or settings.chainlink_user_id
        self.api_secret = api_secret or settings.chainlink_streams_secret

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def require_credentials(self) -> None:
        if not self.has_credentials:
            raise RuntimeError("Chainlink Streams credentials are missing: set CHAINLINK_USER_ID and CHAINLINK_API_KEY.")

    def build_full_path(self, path: str, params: dict[str, Any] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if not params:
            return normalized_path
        return f"{normalized_path}?{urlencode(params)}"

    def body_hash(self, body: bytes = b"") -> str:
        return hashlib.sha256(body).hexdigest()

    def make_signature(self, *, method: str, full_path: str, body: bytes, timestamp_ms: int) -> str:
        self.require_credentials()
        body_hash = self.body_hash(body)
        string_to_sign = f"{method.upper()} {full_path} {body_hash} {self.api_key} {timestamp_ms}"
        return hmac.new(self.api_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    def make_headers(self, *, method: str, full_path: str, body: bytes = b"", timestamp_ms: int | None = None) -> dict[str, str]:
        self.require_credentials()
        timestamp = timestamp_ms or int(time.time() * 1000)
        return {
            "Accept": "application/json",
            "Authorization": self.api_key or "",
            "X-Authorization-Timestamp": str(timestamp),
            "X-Authorization-Signature-SHA256": self.make_signature(
                method=method,
                full_path=full_path,
                body=body,
                timestamp_ms=timestamp,
            ),
        }

    def _decode_int256_word(self, word: str) -> int:
        value = int(word, 16)
        if value >= 1 << 255:
            value -= 1 << 256
        return value

    def _decode_uint_word(self, word: str) -> int:
        return int(word, 16)

    def decode_v3_report_data(self, *, full_report: str, feed_id: str) -> dict[str, Any]:
        normalized_report = full_report[2:] if full_report.startswith("0x") else full_report
        normalized_feed_id = feed_id[2:] if feed_id.startswith("0x") else feed_id
        feed_index = normalized_report.lower().find(normalized_feed_id.lower())
        if feed_index < 0:
            raise ValueError("Feed ID was not found in Chainlink fullReport.")
        if feed_index % 64 != 0:
            feed_index -= feed_index % 64

        words = [
            normalized_report[index : index + 64]
            for index in range(feed_index, min(len(normalized_report), feed_index + 64 * 9), 64)
        ]
        if len(words) < 9:
            raise ValueError("Chainlink fullReport does not contain enough v3 report words.")

        return {
            "feed_id": f"0x{words[0]}",
            "valid_from_timestamp": self._decode_uint_word(words[1]),
            "observations_timestamp": self._decode_uint_word(words[2]),
            "native_fee": self._decode_uint_word(words[3]),
            "link_fee": self._decode_uint_word(words[4]),
            "expires_at": self._decode_uint_word(words[5]),
            "price": self._decode_int256_word(words[6]),
            "bid": self._decode_int256_word(words[7]),
            "ask": self._decode_int256_word(words[8]),
        }

    def parse_latest_report_payload(self, payload: dict[str, Any], *, feed_id: str, price_decimals: int = 18) -> ChainlinkStreamReport:
        report = payload.get("report")
        if not isinstance(report, dict):
            raise ValueError("Chainlink latest report response does not contain a report object.")

        full_report = report.get("fullReport")
        if not isinstance(full_report, str):
            raise ValueError("Chainlink latest report response does not contain fullReport.")

        data = self.decode_v3_report_data(full_report=full_report, feed_id=feed_id)
        scale = Decimal(10) ** price_decimals
        observed_at = int(report.get("observationsTimestamp") or data["observations_timestamp"])
        valid_from = int(report.get("validFromTimestamp") or data["valid_from_timestamp"])

        return ChainlinkStreamReport(
            feed_id=str(report.get("feedID") or data["feed_id"]),
            valid_from_timestamp=datetime.fromtimestamp(valid_from, tz=timezone.utc),
            observations_timestamp=datetime.fromtimestamp(observed_at, tz=timezone.utc),
            expires_at=datetime.fromtimestamp(data["expires_at"], tz=timezone.utc),
            price=Decimal(data["price"]) / scale,
            bid=Decimal(data["bid"]) / scale if data.get("bid") is not None else None,
            ask=Decimal(data["ask"]) / scale if data.get("ask") is not None else None,
            raw_payload={
                "feedID": report.get("feedID") or data["feed_id"],
                "validFromTimestamp": valid_from,
                "observationsTimestamp": observed_at,
                "expiresAt": data["expires_at"],
                "price": str(data["price"]),
                "bid": str(data["bid"]),
                "ask": str(data["ask"]),
            },
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> ChainlinkStreamsResponse:
        full_path = self.build_full_path(path, params)
        headers = self.make_headers(method="GET", full_path=full_path)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}{full_path}", headers=headers)
        payload = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return ChainlinkStreamsResponse(
            status_code=response.status_code,
            body_preview=response.text[:500],
            payload=payload,
        )

    async def fetch_latest_report(self, *, feed_id: str, price_decimals: int = 18) -> ChainlinkStreamReport:
        response = await self.get("/api/v1/reports/latest", {"feedID": feed_id})
        if response.status_code != 200:
            raise RuntimeError(f"Chainlink Streams latest report failed with HTTP {response.status_code}: {response.body_preview}")
        if response.payload is None:
            raise RuntimeError("Chainlink Streams latest report returned non-JSON response.")
        return self.parse_latest_report_payload(response.payload, feed_id=feed_id, price_decimals=price_decimals)
