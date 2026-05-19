from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Index, Integer, JSON
from sqlalchemy import Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AssetSymbol(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"


class CandleColor(str, Enum):
    GREEN = "green"
    RED = "red"
    NEUTRAL = "neutral"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[AssetSymbol] = mapped_column(SqlEnum(AssetSymbol), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ticks: Mapped[list["PriceTick"]] = relationship(back_populates="asset")
    candles: Mapped[list["Candle"]] = relationship(back_populates="asset")
    imbalance_events: Mapped[list["ImbalanceEvent"]] = relationship(back_populates="asset")


class PriceTick(Base):
    __tablename__ = "price_ticks"
    __table_args__ = (
        Index("ix_price_ticks_asset_timestamp", "asset_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="ticks")


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "timeframe",
            "timestamp_start",
            "source",
            name="uq_candles_asset_timeframe_start_source",
        ),
        Index("ix_candles_asset_timeframe_start", "asset_id", "timeframe", "timestamp_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="5m")
    timestamp_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timestamp_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    tick_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_tick_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tick_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    color: Mapped[CandleColor | None] = mapped_column(SqlEnum(CandleColor), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    asset: Mapped[Asset] = relationship(back_populates="candles")


class ImbalanceEvent(Base):
    __tablename__ = "imbalance_events"
    __table_args__ = (
        Index("ix_imbalance_events_asset_timeframe_start", "asset_id", "timeframe", "timestamp_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="5m")
    window_size: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timestamp_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    percent_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    z_score: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    severity: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="imbalance_events")


class DataSourceRun(Base):
    __tablename__ = "data_source_runs"
    __table_args__ = (
        Index("ix_data_source_runs_source_status", "source", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RunStatus] = mapped_column(SqlEnum(RunStatus), nullable=False, default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
