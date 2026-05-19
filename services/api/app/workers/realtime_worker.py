import argparse
import asyncio
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.analysis.imbalance_engine import ImbalanceDetectionConfig, detect_imbalances
from app.analysis.imbalance_storage import delete_imbalance_events, insert_imbalance_events
from app.candles.realtime_storage import CHAINLINK_STREAMS_SOURCE, rebuild_stream_candles_from_ticks, upsert_stream_report
from app.candles.storage import build_candle_query, get_asset_by_symbol
from app.core.config import settings
from app.db.models import Asset, AssetSymbol, Candle
from app.db.session import SessionLocal
from app.feeds.chainlink_streams import ChainlinkStreamReport, ChainlinkStreamsClient
from app.feeds.symbols import chainlink_stream_feed_for_asset

FIVE_MINUTES = timedelta(minutes=5)

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryConfig:
    attempts: int = 5
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def normalized_attempts(self) -> int:
        return max(1, self.attempts)

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.initial_delay_seconds * (2 ** max(0, attempt - 1))
        return max(0.0, min(delay, self.max_delay_seconds))


@dataclass(frozen=True)
class RealtimeWorkerConfig:
    interval_seconds: int = 10
    timeframe: str = "5m"
    source: str = CHAINLINK_STREAMS_SOURCE
    retry: RetryConfig = field(default_factory=RetryConfig)
    recalculate_imbalances: bool = True
    imbalance_lookback_candles: int = 120
    imbalance_config: ImbalanceDetectionConfig = field(default_factory=ImbalanceDetectionConfig)


@dataclass(frozen=True)
class CandleGap:
    previous_candle_start: str
    current_candle_start: str
    missing_candle_count: int


@dataclass(frozen=True)
class ImbalanceRefreshSummary:
    source: str
    start: str
    end: str
    candles_loaded: int
    events_detected: int
    events_deleted: int
    events_inserted: int


@dataclass(frozen=True)
class PollAssetResult:
    ok: bool
    asset: str
    attempts: int
    inserted_tick: bool = False
    price: str | None = None
    observations_timestamp: str | None = None
    candle_id: int | None = None
    candle_start: str | None = None
    tick_count: int | None = None
    closed_candle_start: str | None = None
    gap: CandleGap | None = None
    stale_report: bool = False
    imbalance_refresh: ImbalanceRefreshSummary | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PollIterationResult:
    iteration: int
    results: dict[str, PollAssetResult]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "ok": self.ok,
            "results": {asset: result.as_dict() for asset, result in self.results.items()},
        }


def configure_output_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_assets(value: str) -> list[AssetSymbol]:
    if value.lower() == "all":
        return [AssetSymbol.BTC, AssetSymbol.ETH, AssetSymbol.SOL]
    return [AssetSymbol(item.strip().upper()) for item in value.split(",") if item.strip()]


def detect_candle_gap(previous_start: datetime | None, current_start: datetime) -> CandleGap | None:
    if previous_start is None:
        return None

    previous = normalize_timestamp(previous_start)
    current = normalize_timestamp(current_start)
    if current <= previous + FIVE_MINUTES:
        return None

    missing = int((current - previous) / FIVE_MINUTES) - 1
    return CandleGap(
        previous_candle_start=previous.isoformat(),
        current_candle_start=current.isoformat(),
        missing_candle_count=missing,
    )


async def fetch_latest_report_with_retry(
    *,
    client: ChainlinkStreamsClient,
    feed_id: str,
    price_decimals: int,
    retry: RetryConfig,
    logger: logging.Logger,
    sleep: SleepFn = asyncio.sleep,
) -> tuple[ChainlinkStreamReport, int]:
    attempts = retry.normalized_attempts()
    for attempt in range(1, attempts + 1):
        try:
            report = await client.fetch_latest_report(feed_id=feed_id, price_decimals=price_decimals)
            return report, attempt
        except Exception:
            if attempt >= attempts:
                raise
            delay = retry.delay_for_attempt(attempt)
            logger.warning(
                "Chainlink Streams latest report failed, retrying in %.2fs (attempt %s/%s)",
                delay,
                attempt,
                attempts,
                exc_info=True,
            )
            await sleep(delay)

    raise RuntimeError("unreachable retry state")


def load_latest_candle_start(
    db: Session,
    *,
    asset: Asset,
    timeframe: str,
    source: str,
) -> datetime | None:
    candle = db.scalar(
        select(Candle)
        .where(
            Candle.asset_id == asset.id,
            Candle.timeframe == timeframe,
            Candle.source == source,
        )
        .order_by(Candle.timestamp_start.desc())
        .limit(1)
    )
    return normalize_timestamp(candle.timestamp_start) if candle else None


def recalculate_recent_imbalances(
    db: Session,
    *,
    asset: Asset,
    timeframe: str,
    source: str,
    end: datetime,
    lookback_candles: int,
    config: ImbalanceDetectionConfig,
) -> ImbalanceRefreshSummary:
    normalized_end = normalize_timestamp(end)
    start = normalized_end - FIVE_MINUTES * max(1, lookback_candles)
    candles = list(
        db.scalars(
            build_candle_query(
                asset_symbol=asset.symbol,
                timeframe=timeframe,
                source=source,
                from_ts=start,
                to_ts=normalized_end,
            ).options(joinedload(Candle.asset))
        ).all()
    )
    events = detect_imbalances(candles, config=config)
    deleted = delete_imbalance_events(
        db,
        asset=asset,
        timeframe=timeframe,
        start=start,
        end=normalized_end,
        source=source,
    )
    inserted = insert_imbalance_events(
        db,
        asset=asset,
        timeframe=timeframe,
        source=source,
        events=events,
    )
    return ImbalanceRefreshSummary(
        source=source,
        start=start.isoformat(),
        end=normalized_end.isoformat(),
        candles_loaded=len(candles),
        events_detected=len(events),
        events_deleted=deleted,
        events_inserted=inserted,
    )


def repair_realtime_candles_from_ticks(
    *,
    assets: list[AssetSymbol],
    timeframe: str,
    source: str,
    logger: logging.Logger,
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    with SessionLocal() as db:
        for asset_symbol in assets:
            asset = get_asset_by_symbol(db, asset_symbol)
            inserted, updated = rebuild_stream_candles_from_ticks(
                db,
                asset=asset,
                timeframe=timeframe,
                source=source,
            )
            summary[asset_symbol.value] = {"inserted": inserted, "updated": updated}

    logger.info("Rebuilt realtime candles from stored ticks: %s", summary)
    return summary


class RealtimeWorker:
    def __init__(
        self,
        *,
        assets: list[AssetSymbol],
        config: RealtimeWorkerConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.assets = assets
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self._active_candle_start: dict[AssetSymbol, datetime] = {}

    async def poll_once(
        self,
        *,
        client: ChainlinkStreamsClient,
        iteration: int,
    ) -> PollIterationResult:
        results: dict[str, PollAssetResult] = {}
        with SessionLocal() as db:
            for asset_symbol in self.assets:
                result = await self._poll_asset(db=db, client=client, asset_symbol=asset_symbol)
                results[asset_symbol.value] = result
        return PollIterationResult(iteration=iteration, results=results)

    async def _poll_asset(
        self,
        *,
        db: Session,
        client: ChainlinkStreamsClient,
        asset_symbol: AssetSymbol,
    ) -> PollAssetResult:
        feed_id = chainlink_stream_feed_for_asset(asset_symbol)
        if not feed_id:
            return PollAssetResult(
                ok=False,
                asset=asset_symbol.value,
                attempts=0,
                error="missing feedID in .env",
            )
        if not client.has_credentials:
            return PollAssetResult(
                ok=False,
                asset=asset_symbol.value,
                attempts=0,
                error="Chainlink Streams credentials are missing: set CHAINLINK_USER_ID and CHAINLINK_API_KEY.",
            )

        try:
            asset = get_asset_by_symbol(db, asset_symbol)
            previous_start = self._active_candle_start.get(asset_symbol)
            if previous_start is None:
                previous_start = load_latest_candle_start(
                    db,
                    asset=asset,
                    timeframe=self.config.timeframe,
                    source=self.config.source,
                )

            report, attempts = await fetch_latest_report_with_retry(
                client=client,
                feed_id=feed_id,
                price_decimals=settings.chainlink_price_decimals,
                retry=self.config.retry,
                logger=self.logger,
            )
            inserted_tick, candle = upsert_stream_report(
                db,
                asset=asset,
                report=report,
                timeframe=self.config.timeframe,
                source=self.config.source,
            )
            current_start = normalize_timestamp(candle.timestamp_start)
            stale_report = previous_start is not None and current_start < normalize_timestamp(previous_start)
            closed_candle_start: datetime | None = None
            imbalance_refresh: ImbalanceRefreshSummary | None = None
            gap = detect_candle_gap(previous_start, current_start)

            if previous_start is None or current_start >= normalize_timestamp(previous_start):
                self._active_candle_start[asset_symbol] = current_start

            if previous_start is not None and current_start > normalize_timestamp(previous_start):
                closed_candle_start = normalize_timestamp(previous_start)
                self.logger.info(
                    "Realtime candle closed: asset=%s source=%s closed_start=%s next_start=%s",
                    asset_symbol.value,
                    self.config.source,
                    closed_candle_start.isoformat(),
                    current_start.isoformat(),
                )
                if gap:
                    self.logger.warning(
                        "Realtime candle gap detected: asset=%s source=%s previous=%s current=%s missing=%s",
                        asset_symbol.value,
                        self.config.source,
                        gap.previous_candle_start,
                        gap.current_candle_start,
                        gap.missing_candle_count,
                    )
                if self.config.recalculate_imbalances:
                    imbalance_refresh = recalculate_recent_imbalances(
                        db,
                        asset=asset,
                        timeframe=self.config.timeframe,
                        source=self.config.source,
                        end=current_start,
                        lookback_candles=self.config.imbalance_lookback_candles,
                        config=self.config.imbalance_config,
                    )

            if stale_report:
                self.logger.warning(
                    "Stale realtime report ignored for active-state advance: asset=%s candle_start=%s active_start=%s",
                    asset_symbol.value,
                    current_start.isoformat(),
                    normalize_timestamp(previous_start).isoformat() if previous_start else None,
                )

            return PollAssetResult(
                ok=True,
                asset=asset_symbol.value,
                attempts=attempts,
                inserted_tick=inserted_tick,
                price=str(report.price),
                observations_timestamp=report.observations_timestamp.isoformat(),
                candle_id=candle.id,
                candle_start=current_start.isoformat(),
                tick_count=candle.tick_count,
                closed_candle_start=closed_candle_start.isoformat() if closed_candle_start else None,
                gap=gap,
                stale_report=stale_report,
                imbalance_refresh=imbalance_refresh,
            )
        except Exception as exc:
            self.logger.exception("Realtime polling failed for asset=%s", asset_symbol.value)
            return PollAssetResult(
                ok=False,
                asset=asset_symbol.value,
                attempts=self.config.retry.normalized_attempts(),
                error=str(exc),
            )


async def run_worker_loop(
    *,
    worker: RealtimeWorker,
    client: ChainlinkStreamsClient,
    once: bool,
    iterations: int | None,
) -> int:
    iteration = 0
    while True:
        iteration += 1
        result = await worker.poll_once(client=client, iteration=iteration)
        print(result.as_dict())

        if once or (iterations is not None and iteration >= iterations):
            return 0 if result.ok else 1

        await asyncio.sleep(worker.config.interval_seconds)


async def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(description="Run hardened Chainlink Streams realtime polling worker.")
    parser.add_argument("--asset", default="all", help="BTC, ETH, SOL, all, or comma-separated symbols.")
    parser.add_argument("--interval", type=int, default=settings.chainlink_poll_interval_seconds)
    parser.add_argument("--once", action="store_true", help="Run one polling iteration and exit.")
    parser.add_argument("--iterations", type=int, default=None, help="Optional max polling iterations.")
    parser.add_argument("--retry-attempts", type=int, default=settings.chainlink_streams_retry_attempts)
    parser.add_argument("--retry-initial-delay", type=float, default=settings.chainlink_streams_retry_initial_seconds)
    parser.add_argument("--retry-max-delay", type=float, default=settings.chainlink_streams_retry_max_seconds)
    parser.add_argument(
        "--imbalance-lookback-candles",
        type=int,
        default=settings.realtime_imbalance_lookback_candles,
        help="Recent closed realtime candles used when refreshing imbalance events.",
    )
    parser.add_argument(
        "--no-recalculate-imbalances",
        action="store_true",
        help="Skip fresh imbalance recalculation when a realtime candle closes.",
    )
    parser.add_argument("--log-level", default=settings.log_level)
    args = parser.parse_args()

    configure_logging(args.log_level)

    worker = RealtimeWorker(
        assets=parse_assets(args.asset),
        config=RealtimeWorkerConfig(
            interval_seconds=args.interval,
            timeframe=settings.backfill_timeframe,
            retry=RetryConfig(
                attempts=args.retry_attempts,
                initial_delay_seconds=args.retry_initial_delay,
                max_delay_seconds=args.retry_max_delay,
            ),
            recalculate_imbalances=not args.no_recalculate_imbalances,
            imbalance_lookback_candles=args.imbalance_lookback_candles,
        ),
    )
    client = ChainlinkStreamsClient()
    repair_realtime_candles_from_ticks(
        assets=worker.assets,
        timeframe=worker.config.timeframe,
        source=worker.config.source,
        logger=logging.getLogger(__name__),
    )

    try:
        return await run_worker_loop(
            worker=worker,
            client=client,
            once=args.once,
            iterations=args.iterations,
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Realtime worker stopped by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
