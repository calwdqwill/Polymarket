from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./poly_crypto.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    environment: Literal["local", "dev", "prod"] = "local"

    chainlink_base_url: str = "https://priceapi.dataengine.chain.link"
    chainlink_streams_base_url: str = "https://api.dataengine.chain.link"
    chainlink_user_id: str | None = None
    chainlink_client_id: str | None = None
    chainlink_client_secret: str | None = None
    chainlink_api_key: str | None = None
    chainlink_price_decimals: int = 18
    chainlink_symbol_btc_usd: str = "BTCUSD"
    chainlink_symbol_eth_usd: str = "ETHUSD"
    chainlink_symbol_sol_usd: str = "SOLUSD"
    chainlink_feed_btc_usdt: str | None = None
    chainlink_feed_eth_usdt: str | None = None
    chainlink_feed_sol_usd: str | None = None
    chainlink_poll_interval_seconds: int = 10
    chainlink_streams_retry_attempts: int = 5
    chainlink_streams_retry_initial_seconds: float = 1.0
    chainlink_streams_retry_max_seconds: float = 30.0
    realtime_imbalance_lookback_candles: int = 120

    binance_base_url: str = "https://api.binance.com"
    binance_symbol_btc_usdt: str = "BTCUSDT"
    binance_symbol_eth_usdt: str = "ETHUSDT"
    binance_symbol_sol_usdt: str = "SOLUSDT"
    binance_klines_limit: int = 1000
    binance_request_sleep_seconds: float = 0.1
    binance_retry_attempts: int = 5
    binance_retry_sleep_seconds: float = 2.0
    binance_timeout_seconds: float = 60.0

    backfill_lookback_days: int = 90
    backfill_timeframe: str = "5m"
    backfill_chunk_days: int = 5

    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def chainlink_login(self) -> str | None:
        return self.chainlink_user_id or self.chainlink_client_id

    @property
    def chainlink_streams_secret(self) -> str | None:
        return self.chainlink_client_secret or self.chainlink_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
