"""начальная схема

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-18
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.Enum("BTC", "ETH", "SOL", name="assetsymbol"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_assets_symbol"), "assets", ["symbol"], unique=True)

    op.create_table(
        "data_source_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "COMPLETED", "FAILED", name="runstatus"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_data_source_runs_source_status", "data_source_runs", ["source", "status"], unique=False)

    op.create_table(
        "price_ticks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(24, 10), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_ticks_asset_timestamp", "price_ticks", ["asset_id", "timestamp"], unique=False)

    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("timestamp_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(24, 10), nullable=False),
        sa.Column("high", sa.Numeric(24, 10), nullable=False),
        sa.Column("low", sa.Numeric(24, 10), nullable=False),
        sa.Column("close", sa.Numeric(24, 10), nullable=False),
        sa.Column("volume", sa.Numeric(24, 10), nullable=True),
        sa.Column("tick_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_tick_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tick_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("color", sa.Enum("GREEN", "RED", "NEUTRAL", name="candlecolor"), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "asset_id",
            "timeframe",
            "timestamp_start",
            "source",
            name="uq_candles_asset_timeframe_start_source",
        ),
    )
    op.create_index("ix_candles_asset_timeframe_start", "candles", ["asset_id", "timeframe", "timestamp_start"], unique=False)

    op.create_table(
        "imbalance_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("window_size", sa.Integer(), nullable=False),
        sa.Column("timestamp_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("percent_change", sa.Numeric(18, 8), nullable=True),
        sa.Column("z_score", sa.Numeric(18, 8), nullable=True),
        sa.Column("severity", sa.Numeric(18, 8), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_imbalance_events_asset_timeframe_start",
        "imbalance_events",
        ["asset_id", "timeframe", "timestamp_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_imbalance_events_asset_timeframe_start", table_name="imbalance_events")
    op.drop_table("imbalance_events")
    op.drop_index("ix_candles_asset_timeframe_start", table_name="candles")
    op.drop_table("candles")
    op.drop_index("ix_price_ticks_asset_timestamp", table_name="price_ticks")
    op.drop_table("price_ticks")
    op.drop_index("ix_data_source_runs_source_status", table_name="data_source_runs")
    op.drop_table("data_source_runs")
    op.drop_index(op.f("ix_assets_symbol"), table_name="assets")
    op.drop_table("assets")
