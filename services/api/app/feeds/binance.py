from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import asyncio

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class BinanceKline:
    symbol: str
    timestamp_start: datetime
    timestamp_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    raw_payload: dict[str, Any]


class BinanceRequestError(RuntimeError):
    pass


class BinanceClient:
    """Public Binance Spot kline client for historical OHLCV backfill."""

    def __init__(
        self,
        base_url: str | None = None,
        limit: int | None = None,
        retry_attempts: int | None = None,
        retry_sleep: float | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.binance_base_url).rstrip("/")
        self.limit = min(limit or settings.binance_klines_limit, 1000)
        self.retry_attempts = retry_attempts or settings.binance_retry_attempts
        self.retry_sleep = retry_sleep if retry_sleep is not None else settings.binance_retry_sleep_seconds
        self.timeout = timeout or settings.binance_timeout_seconds

    def _parse_timestamp_ms(self, value: Any) -> datetime:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)

    def _parse_decimal(self, value: Any) -> Decimal:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    def _parse_kline(self, symbol: str, row: list[Any]) -> BinanceKline:
        if len(row) < 6:
            raise ValueError(f"Некорректная строка Binance kline: {row}")

        timestamp_start = self._parse_timestamp_ms(row[0])
        # Binance returns close time as the last millisecond of the interval.
        timestamp_end = self._parse_timestamp_ms(row[6]) + timedelta(milliseconds=1) if len(row) > 6 else timestamp_start + timedelta(minutes=5)
        volume = self._parse_decimal(row[5])

        return BinanceKline(
            symbol=symbol,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            open=self._parse_decimal(row[1]),
            high=self._parse_decimal(row[2]),
            low=self._parse_decimal(row[3]),
            close=self._parse_decimal(row[4]),
            volume=volume if volume != 0 else None,
            raw_payload={
                "symbol": symbol,
                "row": [str(item) for item in row],
            },
        )

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    async def fetch_klines(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "5m",
        limit: int | None = None,
    ) -> list[BinanceKline]:
        request_limit = min(limit or self.limit, 1000)
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": request_limit,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(f"{self.base_url}/api/v3/klines", params=params)
                if self._is_retryable_status(response.status_code):
                    raise BinanceRequestError(f"Binance klines failed with HTTP {response.status_code}.")
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.RequestError, BinanceRequestError) as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    raise
                await asyncio.sleep(self.retry_sleep * attempt)
        else:
            raise BinanceRequestError(f"Binance klines request failed: {last_error}")

        if not isinstance(payload, list):
            raise ValueError("Binance klines вернул неожиданный формат ответа.")
        return [self._parse_kline(symbol, row) for row in payload]
