"""Read-only local research endpoints, isolated from collector and primary database."""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.prediction.dashboard_read import LocalDashboardRepository, paginate
from app.prediction.dashboard_series import project_series

router = APIRouter()
ROOT = Path(__file__).resolve().parents[5]


@lru_cache(maxsize=1)
def get_repository():
    try:
        return LocalDashboardRepository(
            Path(os.environ.get("PREDICTION_DASHBOARD_JOURNAL_DIR", ROOT / "shadow-engine-v1-validated")),
            Path(os.environ.get("PREDICTION_DASHBOARD_CACHE_DIR", ROOT / "target-profit-research-v1/cache")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).exception("Local prediction dataset rejected")
        raise HTTPException(503, "Local dataset verification failed; inspect server logs") from None


@router.get("/health")
def health(repo=Depends(get_repository)):
    return repo.health


@router.get("/summary")
def summary(repo=Depends(get_repository)):
    return dict(
        health=repo.health,
        config=repo.config,
        dataset=repo.root.name,
        settlement="UNKNOWN",
        fees="FEE_UNKNOWN",
        scenarios=repo.stats(),
    )


@router.get("/stats")
def stats(repo=Depends(get_repository)):
    return repo.stats()


@router.get("/windows")
def windows(
    scenario: Literal["100", "250"] = "100",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    status: str = "",
    date: str = "",
    repo=Depends(get_repository),
):
    rows = [w for w in repo.windows.values() if w["scenario_ms"] == int(scenario)]
    if status:
        rows = [
            w
            for w in rows
            if (
                (status == "capture" and w["target_captured"])
                or (status == "no-signal" and not w["attempts"])
                or (status == "failed" and w["attempts"] and not w["target_captured"])
                or (status == "one-leg" and any(repo.attempts[aid]["category"] == "one-leg" for aid in w["attempts"]))
                or (status == "invalid" and w["valid_coverage_ns"] < 300_000_000_000)
            )
        ]
    if date:
        from datetime import datetime, timezone

        rows = [
            w
            for w in rows
            if datetime.fromisoformat(w["spec"]["start"]).astimezone(timezone.utc).date().isoformat() == date
        ]
    return paginate(sorted(rows, key=lambda w: w["spec"]["start"], reverse=True), offset, limit)


@router.get("/windows/{wid}")
def window_detail(wid: str, repo=Depends(get_repository)):
    result = repo.window(wid)
    if result is None:
        raise HTTPException(404, "Window not found")
    return result


@router.get("/windows/{wid}/series")
def window_series(wid: str, repo=Depends(get_repository)):
    if wid not in repo.sources:
        raise HTTPException(404, "Window not found")
    epoch, hashes = repo.sources[wid]
    path = repo.cache / f"{epoch}.json.gz"
    if not path.exists():
        return dict(status="NO_CACHE", points=[], quality_events=[], books={})
    try:
        return project_series(path, hashes["source_sha256"])
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "Historical chart cache unavailable or invalid") from None


@router.get("/attempts")
def attempts(
    scenario: Literal["100", "250"] | None = None,
    direction: Literal["A", "B"] | None = None,
    result: str = "",
    date: str = "",
    strategy_version: str = "",
    sort: Literal["newest", "pnl", "edge", "capture-time"] = "newest",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    repo=Depends(get_repository),
):
    from datetime import datetime, timezone
    from decimal import Decimal

    rows = list(repo.attempts.values())
    rows = [
        a
        for a in rows
        if (scenario is None or a["scenario_ms"] == int(scenario))
        and (not direction or a["direction"] == direction)
        and (not result or a["category"] == result or result == "failed" and a["state"] == "FAILED")
        and (not strategy_version or a["strategy_version"] == strategy_version)
        and (not date or datetime.fromisoformat(a["signal_time"]).astimezone(timezone.utc).date().isoformat() == date)
    ]

    def sort_key(a):
        if sort == "pnl":
            return Decimal(a["simulated_net"])
        if sort == "edge":
            return Decimal(a["signal_edge"])
        if sort == "capture-time":
            w = repo.windows[f'{a["window_id"]}:{a["scenario_ms"]}']
            return Decimal(w["capture_seconds"]) if a["result"] == "TARGET_CAPTURED" else Decimal("Infinity")
        return a["signal_ns"]

    page = paginate(sorted(rows, key=sort_key, reverse=sort != "capture-time"), offset, limit)
    page["items"] = [
        dict(
            a,
            peer_nets={
                str(p["scenario_ms"]): p["simulated_net"]
                for p in repo.attempts.values()
                if p["signal_id"] == a["signal_id"]
                and p["window_id"] == a["window_id"]
                and p["direction"] == a["direction"]
            },
        )
        for a in page["items"]
    ]
    return page


@router.get("/attempts/{aid}")
def attempt_detail(aid: str, repo=Depends(get_repository)):
    if aid not in repo.attempts:
        raise HTTPException(404, "Attempt not found")
    a = repo.attempts[aid]
    peers = [
        p
        for p in repo.attempts.values()
        if p["signal_id"] == a["signal_id"] and p["window_id"] == a["window_id"] and p["direction"] == a["direction"]
    ]
    return dict(selected=a, scenarios={str(p["scenario_ms"]): p for p in peers})
