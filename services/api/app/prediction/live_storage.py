import gzip
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from app.prediction.storage import Journal, dumps


class LiveJournal(Journal):
    def __init__(self, directory, segment_seconds=60):
        super().__init__(directory)
        self.started_ns = time.perf_counter_ns()
        self.session = uuid4().hex
        self.ordinal = 0
        self.segment_seconds = segment_seconds
        self.segment = 0
        self.closed_segments = []
        self.handles = {}
        self.bins = defaultdict(Counter)
        self.wire_bytes = Counter()
        self.closed_bytes = Counter()
        self.pending_since_ns = None
        self.pending_items = 0
        self.pending_bytes = 0
        self.processing_lag_ms = 0.0
        self.last_durable_ns = self.started_ns

    def append(self, stream, payload, received=None, mono_ns=None, connection=None):
        received = received or datetime.now(timezone.utc)
        mono_ns = mono_ns if mono_ns is not None else time.perf_counter_ns()
        self.rotate(mono_ns)
        self.ordinal += 1
        record = dict(
            received_timestamp=received,
            monotonic_received_ns=mono_ns,
            session=self.session,
            local_ordinal=self.ordinal,
            connection=connection,
            payload=payload,
        )
        encoded = (dumps(record) + "\n").encode("utf-8")
        if stream not in self.handles:
            self.handles[stream] = gzip.open(
                self.directory / f"{stream}.{self.segment:06d}.jsonl.gz", "ab", compresslevel=1
            )
        self.pending_since_ns = self.pending_since_ns or time.perf_counter_ns()
        self.pending_items += 1
        self.pending_bytes += len(encoded)
        self.handles[stream].write(encoded)
        self.processing_lag_ms = max(0, (time.perf_counter_ns() - mono_ns) / 1e6)
        self.counts[stream] = self.counts.get(stream, 0) + 1
        self.bytes[stream] = self.bytes.get(stream, 0) + len(encoded)
        second = int((mono_ns - self.started_ns) / 1e9)
        self.bins[stream][second] += 1
        while self.bins[stream] and next(iter(self.bins[stream])) < second - 299:
            del self.bins[stream][next(iter(self.bins[stream]))]
        if stream.startswith("raw_ws_"):
            self.wire_bytes[stream] += len(payload["frame"].encode("utf-8"))
        return self.ordinal

    def rotate(self, mono_ns):
        segment = max(0, int((mono_ns - self.started_ns) / (self.segment_seconds * 1e9)))
        if segment > self.segment:
            self._close_handles()
            self.closed_segments.append(self.segment)
            self.segment = segment
            atomic_json(self.directory / "segments.json", dict(closed=self.closed_segments, active=self.segment))

    def flush(self):
        self.rotate(time.perf_counter_ns())
        for handle in self.handles.values():
            handle.flush()
            os.fsync(handle.fileobj.fileno())
        self._mark_durable()

    def _close_handles(self):
        for stream, handle in self.handles.items():
            name = handle.name
            handle.close()
            with open(name, "rb+") as durable:
                os.fsync(durable.fileno())
            self.closed_bytes[stream] += os.path.getsize(name)
        self.handles.clear()
        self._mark_durable()

    def _mark_durable(self):
        self.pending_since_ns = None
        self.pending_items = self.pending_bytes = 0
        self.last_durable_ns = time.perf_counter_ns()

    def close(self):
        self._close_handles()
        self.closed_segments.append(self.segment)
        atomic_json(self.directory / "segments.json", dict(closed=self.closed_segments, active=None))

    def metrics(self):
        seconds = max((time.perf_counter_ns() - self.started_ns) / 1e9, 0.001)
        result = {}
        for stream, count in self.counts.items():
            bins = sorted(self.bins[stream].get(i, 0) for i in range(max(0, int(seconds) - 299), int(seconds)))
            result[stream] = dict(
                messages=count,
                bytes=self.bytes[stream],
                stored_bytes=self.closed_bytes[stream]
                + (os.fstat(self.handles[stream].fileobj.fileno()).st_size if stream in self.handles else 0),
                messages_per_second_mean=count / seconds,
                messages_per_second_p95=bins[min(len(bins) - 1, int(len(bins) * 0.95))] if bins else None,
                bytes_per_second=self.bytes[stream] / seconds,
                mb_per_hour=self.bytes[stream] / seconds * 3600 / 1e6,
                payload_bytes=self.wire_bytes.get(stream),
                payload_bytes_per_second=self.wire_bytes[stream] / seconds if stream in self.wire_bytes else None,
            )
        return dict(
            duration_seconds=seconds,
            writer=dict(
                mode="synchronous_raw_before_apply",
                queue_items=0,
                queue_bytes=0,
                oldest_age_ms=0,
                pending_durable_items=self.pending_items,
                pending_durable_bytes=self.pending_bytes,
                durable_lag_ms=(time.perf_counter_ns() - self.pending_since_ns) / 1e6 if self.pending_since_ns else 0,
                processing_lag_ms=self.processing_lag_ms,
                receive_queue_scope="websockets bounded 16 frames; TCP arrival age unavailable",
            ),
            gzip_mb_per_hour=sum(v["stored_bytes"] for v in result.values()) / seconds * 3600 / 1e6,
            active_segment=self.segment,
            streams=result,
            scope="UTF-8 application frames and JSONL; excludes TLS/TCP overhead; rates include idle seconds",
        )


def atomic_json(path, value):
    return atomic_text(path, dumps(value))


def atomic_text(path, value):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
        return True
    except PermissionError:
        # Windows readers may briefly deny replacement; retry on the next UI tick.
        # Append-only market data must not depend on publishing this derived view.
        return False


def decode(frame):
    from decimal import Decimal

    return json.loads(frame, parse_float=Decimal)
