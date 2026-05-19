import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.candles.aggregator import candle_end, floor_to_5m
from app.db.base import Base
from app.db.models import Asset, AssetSymbol, Candle, CandleColor, ImbalanceEvent, PriceTick
from app.db.session import get_db
from app.main import create_app


class ApiRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        Base.metadata.create_all(bind=self.engine)
        self._seed_database()

        self.app = create_app()

        def override_get_db():  # noqa: ANN202
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        self.app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _seed_database(self) -> None:
        current_tick_time = datetime.now(timezone.utc).replace(tzinfo=None)
        now = floor_to_5m(current_tick_time)
        historical_start = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)

        with self.SessionLocal() as db:
            assets = {
                symbol: Asset(symbol=symbol, name=f"{symbol.value} test", source_config={})
                for symbol in AssetSymbol
            }
            db.add_all(assets.values())
            db.commit()

            btc = assets[AssetSymbol.BTC]
            historical_candles = [
                Candle(
                    asset_id=btc.id,
                    timeframe="5m",
                    timestamp_start=historical_start + timedelta(minutes=5 * index),
                    timestamp_end=candle_end(historical_start + timedelta(minutes=5 * index)),
                    open=Decimal("100") + index,
                    high=Decimal("102") + index,
                    low=Decimal("99") + index,
                    close=Decimal("101") + index,
                    volume=Decimal("10"),
                    tick_count=0,
                    color=CandleColor.GREEN,
                    source="binance_klines",
                    raw_payload={"index": index},
                )
                for index in range(3)
            ]
            db.add_all(historical_candles)

            stream_candle = Candle(
                asset_id=btc.id,
                timeframe="5m",
                timestamp_start=now,
                timestamp_end=candle_end(now),
                open=Decimal("110"),
                high=Decimal("112"),
                low=Decimal("109"),
                close=Decimal("111"),
                volume=None,
                tick_count=1,
                first_tick_time=now,
                last_tick_time=now,
                color=CandleColor.GREEN,
                source="chainlink_streams",
                raw_payload={"source": "test"},
            )
            db.add(stream_candle)
            db.flush()
            db.add(
                PriceTick(
                    asset_id=btc.id,
                    timestamp=current_tick_time,
                    price=Decimal("111"),
                    source="chainlink_streams",
                    raw_payload={"source": "test"},
                )
            )
            db.add(
                ImbalanceEvent(
                    asset_id=btc.id,
                    timeframe="5m",
                    window_size=2,
                    timestamp_start=historical_start,
                    timestamp_end=historical_start + timedelta(minutes=10),
                    direction="bullish",
                    percent_change=Decimal("1.25"),
                    z_score=Decimal("2.50"),
                    severity=Decimal("2.50"),
                    event_type="window_percent_move",
                    metadata_json={"source": "binance_klines"},
                )
            )
            db.commit()

            self.stream_candle_id = stream_candle.id

    def test_status_and_assets_endpoints(self) -> None:
        status = self.client.get("/api/status")
        assets = self.client.get("/api/assets")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "ok")
        self.assertEqual(assets.status_code, 200)
        self.assertEqual([asset["symbol"] for asset in assets.json()], ["BTC", "ETH", "SOL"])

    def test_candles_endpoint_filters_by_asset_and_source(self) -> None:
        response = self.client.get("/api/candles", params={"asset": "BTC", "source": "binance_klines"})

        self.assertEqual(response.status_code, 200)
        candles = response.json()
        self.assertEqual(len(candles), 3)
        self.assertTrue(all(candle["asset"] == "BTC" for candle in candles))
        self.assertTrue(all(candle["source"] == "binance_klines" for candle in candles))

    def test_window_analysis_returns_sliding_windows(self) -> None:
        response = self.client.get(
            "/api/analysis/window",
            params={"asset": "BTC", "source": "binance_klines", "window": 2},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["candles_loaded"], 3)
        self.assertEqual(len(payload["windows"]), 2)
        self.assertEqual(payload["window"], 2)

    def test_candle_drilldown_returns_realtime_ticks(self) -> None:
        response = self.client.get(f"/api/candles/{self.stream_candle_id}/drilldown")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tick_count"], 1)
        self.assertTrue(payload["has_realtime_ticks"])
        self.assertEqual(payload["ticks"][0]["source"], "chainlink_streams")

    def test_imbalances_endpoint_filters_by_source(self) -> None:
        response = self.client.get("/api/imbalances", params={"asset": "BTC", "source": "binance_klines"})

        self.assertEqual(response.status_code, 200)
        events = response.json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "binance_klines")
        self.assertEqual(events[0]["event_type"], "window_percent_move")

    def test_source_health_reports_chainlink_streams(self) -> None:
        response = self.client.get("/api/status/sources")

        self.assertEqual(response.status_code, 200)
        sources = {source["source"]: source for source in response.json()["sources"]}
        chainlink_streams = sources["chainlink_streams"]
        self.assertEqual(chainlink_streams["assets"]["BTC"]["candles"], 1)
        self.assertEqual(chainlink_streams["assets"]["BTC"]["ticks"], 1)
        self.assertTrue(chainlink_streams["is_live"])


if __name__ == "__main__":
    unittest.main()
