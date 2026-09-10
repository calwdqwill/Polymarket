"""Local receive-order cache adapter; never hands a timeline lookup to the engine."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal as D

from app.prediction.shadow_engine import ShadowEngine
from app.prediction.shadow_models import KEYS, EventKind, ShadowBook, ShadowEvent, WindowSpec, utc_from_us
from app.prediction.shadow_storage import InMemoryShadowJournal


def cache_events(data: dict, spec: WindowSpec, session: str) -> Iterator[ShadowEvent]:
    """The cache is a historical projection with combined health/freshness deadlines.

    cache-ref versions and unavailable connection IDs are explicitly labelled;
    they must not be misrepresented as original exchange versions/connection IDs.
    """
    if not data["rows"]:
        raise ValueError("Empty historical timeline")
    first = data["rows"][0]
    yield ShadowEvent(f"{session}:{data['window']}:start", EventKind.WINDOW_START, spec.canonical_market_id,
                      utc_from_us(first[1]), first[0], first[2] - 1, session, first[3], window=spec)
    levels = [tuple((D(p), D(q)) for p, q in ladder[0]) for ladder in data["levels"]]
    previous_refs = {}
    last_ns, last_ordinal = -1, -1
    for row in data["rows"]:
        ns, us, ordinal, quality = row[:4]
        if ns < last_ns or ordinal <= last_ordinal:
            raise ValueError("Historical receive order regressed")
        last_ns, last_ordinal = ns, ordinal
        changed = []
        for key, ref in zip(KEYS, row[4:]):
            if previous_refs.get(key) == ref:
                continue
            previous_refs[key] = ref
            if ref < 0:
                changed.append(ShadowBook(key, (), "MISSING", ns, ns, ns, "cache:missing", session,
                                          "cache:connection-unavailable"))
            else:
                ladder, status, book_ns, deadline, minimum = data["books"][ref]
                changed.append(ShadowBook(key, levels[ladder], status, book_ns, deadline, deadline,
                                          f"cache-ref:{ref}", session, "cache:connection-unavailable",
                                          D(minimum) if minimum is not None else None))
        # Empty batches still advance the receive watermark and timers.
        yield ShadowEvent(f"{session}:{ordinal}", EventKind.BOOK_VALID, spec.canonical_market_id,
                          utc_from_us(us), ns, ordinal, session, quality, books=tuple(changed))


def replay_window(data, spec, session, config, repository=None):
    repo = repository if repository is not None else InMemoryShadowJournal()
    engine = ShadowEngine(config, repo)
    for event in cache_events(data, spec, session):
        engine.on_event(event)
    if engine.spec:
        engine.advance(engine.end_ns)
    return repo


def compact_results(repo):
    windows = {r["key"]: r["payload"] for r in repo.records("window")}
    attempts = {r["key"]: r["payload"] for r in repo.records("attempt")}
    return dict(windows=list(windows.values()), attempts=list(attempts.values()))


def spec_for(window: int, market_ids, quality="UNKNOWN"):
    start = utc_from_us(window * 1_000_000)
    return WindowSpec(f"BTC-5M-{start:%Y%m%dT%H%M%SZ}", start, start + timedelta(seconds=300),
                      tuple(sorted(market_ids.items())), quality)
