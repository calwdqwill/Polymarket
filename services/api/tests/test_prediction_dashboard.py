"""Synthetic read-model checks, independent of external historical datasets."""

import gzip
import hashlib
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from app.api.routes.prediction import get_repository, router
from app.prediction.dashboard_read import LocalDashboardRepository, statistics
from app.prediction.dashboard_series import project_series
from app.prediction.shadow_replay import compact_results
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_prediction_shadow import Fixture


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = LocalDashboardRepository(root / "missing", root / "cache")
        f = Fixture()
        f.books(1)
        # 100ms fills before deterioration; 250ms takes the adverse book.
        f.books(150, prices=(".8", ".8", ".9", ".9"))
        f.advance(300000)
        result = compact_results(f.repo)
        self.repo.windows = {
            f'{w["spec"]["canonical_market_id"]}:{w["scenario_ms"]}': dict(w, id=w["spec"]["canonical_market_id"])
            for w in result["windows"]
        }
        self.repo.attempts = {
            a["attempt_id"]: dict(a, category="success" if a["result"] == "TARGET_CAPTURED" else "failed")
            for a in result["attempts"]
        }
        app = FastAPI()
        app.include_router(router, prefix="/api/prediction")
        app.dependency_overrides[get_repository] = lambda: self.repo
        self.client = TestClient(app)

    def test_stats_all_losses_and_scenario_separation(self):
        stats = self.repo.stats()
        self.assertEqual(stats["100"]["captures"], 1)
        self.assertEqual(stats["250"]["captures"], 0)
        self.assertGreater(D(stats["100"]["simulated_net"]), 0)
        self.assertLess(D(stats["250"]["simulated_net"]), 0)
        for s in stats.values():
            self.assertEqual(s["windows"], 1)
            self.assertEqual(D(s["net_per_hour"]), D(s["simulated_net"]) * 12)
            self.assertEqual(s["cumulative"][-1]["value"], s["simulated_net"])
            self.assertEqual(s["by_window"][0]["value"], s["simulated_net"])

    def test_pagination_filters_and_validation(self):
        base = "/api/prediction/attempts"
        first = self.client.get(base + "?limit=1").json()
        second = self.client.get(base + "?limit=1&offset=1").json()
        self.assertEqual(first["total"], 2)
        self.assertNotEqual(first["items"][0]["attempt_id"], second["items"][0]["attempt_id"])
        self.assertEqual(self.client.get(base + "?scenario=250&result=failed").json()["total"], 1)
        self.assertEqual(self.client.get(base + "?scenario=100&result=failed").json()["total"], 0)
        self.assertEqual(self.client.get(base + "?date=1970-01-01").json()["total"], 2)
        for suffix in ("?limit=101", "?offset=-1", "?scenario=500"):
            self.assertEqual(self.client.get(base + suffix).status_code, 422)

    def test_details_and_read_only_contract(self):
        a = next(iter(self.repo.attempts.values()))
        detail = self.client.get("/api/prediction/attempts/" + a["attempt_id"]).json()
        self.assertEqual(set(detail["scenarios"]), {"100", "250"})
        window = self.client.get("/api/prediction/windows/" + a["window_id"]).json()
        self.assertEqual(len(window["attempts"]), 2)
        self.assertGreater(len(window["timeline"]), 10)
        self.assertEqual(self.client.get("/api/prediction/windows/unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/prediction/attempts").status_code, 405)

    def test_pair_comparison_survives_filter_and_page_boundary(self):
        page = self.client.get("/api/prediction/attempts?scenario=100&limit=1").json()
        self.assertEqual(set(page["items"][0]["peer_nets"]), {"100", "250"})
        self.assertLess(D(page["items"][0]["peer_nets"]["250"]), 0)

    def test_window_pagination_failed_and_empty_filters(self):
        page = self.client.get("/api/prediction/windows?scenario=250&status=failed").json()
        self.assertEqual(page["total"], 1)
        self.assertFalse(page["items"][0]["target_captured"])
        self.assertEqual(self.client.get("/api/prediction/windows?scenario=100&status=failed").json()["total"], 0)
        self.assertEqual(self.client.get("/api/prediction/windows?offset=1").json()["items"], [])

    def test_empty_and_no_signal_windows(self):
        empty = statistics([], [])
        self.assertEqual(empty["100"]["simulated_net"], "0")
        self.assertIsNone(empty["100"]["success_rate"])
        self.assertIsNone(empty["250"]["worst_attempt"])
        f = Fixture()
        f.advance(300000)
        result = compact_results(f.repo)
        s = statistics(result["windows"], [])
        self.assertEqual(s["100"]["windows"], 1)
        self.assertEqual(len(s["100"]["by_window"]), 1)
        self.assertEqual(s["100"]["attempts"], 0)

    def test_corruption_rejected(self):
        with patch("app.prediction.dashboard_read.verify", side_effect=ValueError("bad hash")):
            with self.assertRaises(ValueError):
                LocalDashboardRepository(Path(self.temp.name), Path(self.temp.name))

    def test_series_sampling_preserves_subsample_gap_and_hash(self):
        path = Path(self.temp.name) / "0.json.gz"
        data = dict(
            window=0,
            levels=[[[[".4", "1000"]], []]],
            books=[[0, "VALID", 0, 100_000_000, None], [0, "VALID", 200_000_000, 300_000_000_000, None]],
            rows=[[0, 0, 1, "UNKNOWN", 0, 0, 0, 0], [200_000_000, 200_000, 2, "UNKNOWN", 1, 1, 1, 1]],
        )
        with gzip.open(path, "wt") as f:
            json.dump(data, f)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        s = project_series(path, sha)
        self.assertEqual(len(s["points"]), 601)
        self.assertEqual(s["quality_events"][0]["start_ns"], "100000000")
        self.assertNotEqual(s["points"][0]["segment"], s["points"][1]["segment"])
        self.assertIsNone(s["books"]["Polymarket:YES"]["bid"])
        with self.assertRaises(ValueError):
            project_series(path, "wrong")
