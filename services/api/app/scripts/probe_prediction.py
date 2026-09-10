import argparse
import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.prediction.discovery import Discovery
from app.prediction.models import match_markets
from app.prediction.observer import Observer
from app.prediction.screen import render_screen
from app.prediction.storage import Journal, dumps


async def probe(output: Path) -> dict:
    journal = Journal(output)
    started = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        discovery = Discovery(client, journal)
        markets = await discovery.collect(started)
    now, mono_ns = datetime.now(timezone.utc), time.monotonic_ns()
    matches, rows = [], []
    for a in markets:
        for b in markets:
            if a.venue != "Polymarket" or b.venue != "Limitless" or a.start != b.start:
                continue
            result = match_markets(a, b)
            match = dict(
                asdict(result), canonical_market_id=a.canonical_market_id, market_a=a.market_id, market_b=b.market_id
            )
            matches.append(match)
            journal.append("matches", match)
            pair_rows, events = Observer().evaluate(a, b, {}, now, mono_ns)
            rows.extend(pair_rows)
            for event in events:
                journal.append("cross_venue_opportunity", event)
    status = "RESEARCH_COLLECTION_ALLOWED" if matches else "NO_MATCHING_WINDOW"
    if any(match["quality"] == "EXACT" for match in matches):
        # This metadata probe never opens market-data sockets. Streaming is a separate stage.
        status = "EXACT_REQUIRES_STREAMING_STAGE"
    state = {
        "timestamp": now,
        "started": started,
        "status": status,
        "markets": markets,
        "matches": matches,
        "observer_rows": rows,
        "errors": discovery.errors,
        "l2_collected": False,
        "opportunities_observed": None,
        "metrics": journal.metrics(),
    }
    journal.append("probe_summary", state)
    (output / "summary.json").write_text(dumps(state), encoding="utf-8")
    render_screen(state, output / "research.html")
    return state


def main():
    parser = argparse.ArgumentParser(description="Read-only BTC 5m settlement probe; no credentials or orders")
    parser.add_argument("--output", type=Path, help="New directory; existing runs are never overwritten")
    args = parser.parse_args()
    directory = args.output or Path("data/prediction") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    state = asyncio.run(probe(directory))
    print(
        dumps(
            {
                "status": state["status"],
                "markets": len(state["markets"]),
                "matches": state["matches"],
                "errors": state["errors"],
                "output": str(directory.resolve()),
            }
        )
    )


if __name__ == "__main__":
    main()
