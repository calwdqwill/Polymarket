import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class ChainlinkCandle:
    symbol: str
    timestamp_start: datetime
    timestamp_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    raw_payload: dict[str, Any]


class ChainlinkClient:
    """Граница интеграции с Chainlink Data Streams/DataEngine."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.chainlink_base_url).rstrip("/")
        self.login = settings.chainlink_login
        self.api_key = settings.chainlink_api_key
        self.price_scale = Decimal(10) ** settings.chainlink_price_decimals
        self._access_token: str | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.login and self.api_key)

    def require_credentials(self) -> None:
        if not self.has_credentials:
            raise RuntimeError("Chainlink credentials are missing: set CHAINLINK_USER_ID and CHAINLINK_API_KEY in .env.")

    async def _authorize(self, client: httpx.AsyncClient) -> str:
        self.require_credentials()

        response = await client.post(
            f"{self.base_url}/api/v1/authorize",
            data={"login": self.login, "password": self.api_key},
            headers={"Accept": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise RuntimeError(f"Chainlink authorize failed with HTTP {status_code}. Check CHAINLINK_USER_ID and CHAINLINK_API_KEY.") from exc
        payload = response.json()
        token = self._extract_access_token(payload)
        if not token:
            raise RuntimeError("Chainlink authorize не вернул access_token.")
        self._access_token = token
        return self._access_token

    async def _headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        token = self._access_token or await self._authorize(client)
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def _decode_json(self, response: httpx.Response) -> Any:
        return json.loads(response.text, parse_float=Decimal, parse_int=Decimal)

    def _extract_access_token(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("d")
        token = payload.get("access_token") or payload.get("token")
        if not token and isinstance(data, dict):
            token = data.get("access_token") or data.get("token")
        return str(token) if token else None

    def _extract_rows(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("candles")
            if rows is None and isinstance(payload.get("d"), dict):
                rows = payload["d"].get("rows") or payload["d"].get("candles")
            return rows
        return payload

    def _normalize_price(self, value: Any) -> Decimal:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        return decimal_value / self.price_scale

    def _parse_timestamp(self, value: Any) -> datetime:
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    def _parse_row(self, symbol: str, row: list[Any]) -> ChainlinkCandle:
        if len(row) < 5:
            raise ValueError(f"Некорректная строка Chainlink candle: {row}")

        timestamp_start = self._parse_timestamp(row[0])
        volume = None
        if len(row) > 5:
            raw_volume = row[5] if isinstance(row[5], Decimal) else Decimal(str(row[5]))
            volume = raw_volume if raw_volume != 0 else None

        return ChainlinkCandle(
            symbol=symbol,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_start + timedelta(minutes=5),
            open=self._normalize_price(row[1]),
            high=self._normalize_price(row[2]),
            low=self._normalize_price(row[3]),
            close=self._normalize_price(row[4]),
            volume=volume,
            raw_payload={"row": [str(item) for item in row]},
        )

    async def fetch_candles(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        resolution: str = "5m",
        feed_id: str | None = None,
    ) -> list[ChainlinkCandle]:
        del feed_id

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/history/rows",
                params={
                    "symbol": symbol,
                    "from": int(start.timestamp()),
                    "to": int(end.timestamp()),
                    "resolution": resolution,
                },
                headers=await self._headers(client),
            )
            if response.status_code == 401:
                self._access_token = None
                response = await client.get(
                    f"{self.base_url}/api/v1/history/rows",
                    params={
                        "symbol": symbol,
                        "from": int(start.timestamp()),
                        "to": int(end.timestamp()),
                        "resolution": resolution,
                    },
                    headers=await self._headers(client),
                )
            response.raise_for_status()
            payload = self._decode_json(response)

        rows = self._extract_rows(payload)
        if not isinstance(rows, list):
            raise ValueError("Chainlink history/rows вернул неожиданный формат ответа.")
        return [self._parse_row(symbol, row) for row in rows]

    async def healthcheck(self) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/v1/health", headers={"Accept": "application/json"})
            return response.status_code < 500
