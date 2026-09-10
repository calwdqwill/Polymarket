import time
from collections import deque

from websockets.asyncio.client import ClientConnection
from websockets.frames import DATA_OPCODES, Frame, Opcode
from websockets.protocol import OPEN


class ReceiveQueue:
    def __init__(self):
        self.frames = deque()
        self.bytes = 0
        self.peak_items = 0
        self.peak_bytes = 0
        self.peak_age_ms = 0.0
        self.last_processing_lag_ms = 0.0
        self.max_processing_lag_ms = 0.0

    def put(self, size, final, now):
        self.frames.append((size, final, now))
        self.bytes += size
        self.peak_items = max(self.peak_items, len(self.frames))
        self.peak_bytes = max(self.peak_bytes, self.bytes)
        return len(self.frames) <= 512 and self.bytes <= 8 * 1024 * 1024

    def consumed(self, now):
        if self.frames:
            self.last_processing_lag_ms = max(0, (now - self.frames[0][2]) / 1e6)
            self.max_processing_lag_ms = max(self.max_processing_lag_ms, self.last_processing_lag_ms)
        while self.frames:
            size, final, _ = self.frames.popleft()
            self.bytes -= size
            if final:
                break

    def snapshot(self):
        age = max(0, (time.perf_counter_ns() - self.frames[0][2]) / 1e6) if self.frames else 0
        self.peak_age_ms = max(self.peak_age_ms, age, self.max_processing_lag_ms)
        return dict(
            queue_items=len(self.frames),
            queue_bytes=self.bytes,
            oldest_age_ms=age,
            peak_items=self.peak_items,
            peak_bytes=self.peak_bytes,
            peak_age_ms=self.peak_age_ms,
            processing_lag_ms=self.last_processing_lag_ms,
            max_processing_lag_ms=self.max_processing_lag_ms,
        )


class MonitoredConnection(ClientConnection):
    def __init__(self, *args, queue, invalidate, overflow, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_metrics = queue
        self.invalidate_books = invalidate
        self.report_overflow = overflow
        self.overflowed = False

    def process_event(self, event):
        if isinstance(event, Frame) and event.opcode is Opcode.CLOSE:
            self.invalidate_books()
        if isinstance(event, Frame) and event.opcode in DATA_OPCODES:
            if self.overflowed:
                return
            if not self.queue_metrics.put(len(event.data), event.fin, time.perf_counter_ns()):
                self.overflowed = True
                self.invalidate_books()
                self.report_overflow(self.queue_metrics.snapshot())
                self.transport.abort()
                return
        super().process_event(event)

    def data_received(self, data):
        super().data_received(data)
        if self.response is not None and self.protocol.state is not OPEN:
            self.invalidate_books()

    def connection_lost(self, exc):
        self.invalidate_books()
        super().connection_lost(exc)

    async def recv(self, decode=None):
        result = await super().recv(decode)
        if self.overflowed or self.protocol.state is not OPEN:
            raise RuntimeError("Receive continuity invalidated; fresh connection required")
        self.queue_metrics.consumed(time.perf_counter_ns())
        return result
