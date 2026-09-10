import json
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.prediction.models import Book


def json_default(value):
    if isinstance(value, Book):
        result = asdict(value)
        for side in ("bids", "asks"):
            result[side] = [
                {"side": "BUY" if side == "bids" else "SELL", "price": p, "size": s}
                for p, s in sorted(getattr(value, side).items(), reverse=side == "bids")
            ]
        return result
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, default=json_default, ensure_ascii=False, allow_nan=False)


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.started_ns = time.monotonic_ns()
        self.counts: dict[str, int] = {}
        self.bytes: dict[str, int] = {}

    def append(self, stream: str, payload):
        record = {
            "received_timestamp": datetime.now(timezone.utc),
            "received_monotonic_ns": time.monotonic_ns(),
            "payload": payload,
        }
        encoded = (dumps(record) + "\n").encode("utf-8")
        with (self.directory / f"{stream}.jsonl").open("ab") as file:
            file.write(encoded)
        self.counts[stream] = self.counts.get(stream, 0) + 1
        self.bytes[stream] = self.bytes.get(stream, 0) + len(encoded)

    def metrics(self):
        seconds = Decimal(time.monotonic_ns() - self.started_ns) / Decimal(1_000_000_000)
        seconds = max(seconds, Decimal("0.001"))
        return {
            "duration_seconds": seconds,
            "rows": self.counts.copy(),
            "bytes": self.bytes.copy(),
            "rows_per_hour": None,
            "raw_ws_messages_per_second": None,
            "mb_per_hour": None,
            "projected_gb_per_day": None,
            "scope": "metadata_only; excludes summary; one-shot has no steady-state rate",
        }
