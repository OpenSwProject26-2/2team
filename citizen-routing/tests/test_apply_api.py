import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import mode_service, server  # noqa: E402


def manifest(version, **extra):
    return {"version": version, "excluded_route_ids": ["r1"], "shuttle_route_ids": [],
            "existing_stats": {"shapes": False}, **extra}


def route(duration, kinds, transfers=0, walk=60):
    legs = [{"kind": k, "route_id": ("SHUTTLE:CR_R_r01" if k == "shuttle" else "A:r2" if k == "bus" else None),
             "route_name": "셔틀 r01" if k == "shuttle" else None, "duration_seconds": duration // len(kinds),
             "distance_meters": 100, "from": {"stop_id": None}, "to": {"stop_id": None},
             "start": None, "end": None, "geometry": [], "geometry_status": "unavailable"} for k in kinds]
    return {"id": f"r{duration}", "start": None, "end": None, "duration_seconds": duration,
            "walk_seconds": walk, "wait_seconds": 0, "transit_seconds": duration - walk,
            "transfers": transfers, "uses_shuttle": "shuttle" in kinds, "legs": legs}


class ServerFixture:
    def __init__(self, tmp: Path, apply_config: Path | None = None):
        self.normal = tmp / "normal"
        self.strike = tmp / "strike-default"
        self.runtime = tmp / "runtime"
        for d in (self.normal, self.strike, self.runtime):
            d.mkdir(parents=True)
        (self.normal / "manifest.json").write_text(json.dumps(manifest("n1", excluded_route_ids=[])))
        (self.strike / "manifest.json").write_text(json.dumps(manifest("s-default", baseline_version="n1")))
        handler = server.handler(self.normal, "http://127.0.0.1:1/otp/gtfs/v1", self.strike,
                                 "http://127.0.0.1:2/otp/gtfs/v1", runtime=self.runtime,
                                 apply_config=apply_config)
        handler.log_message = lambda *args: None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        request = Request(self.base + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)


class ApplyApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = ServerFixture(Path(self.tmp.name))

    def tearDown(self):
        self.fx.close()
        self.tmp.cleanup()

    def write_active(self, **extra):
        graph = self.fx.runtime / "strike-graphs" / "g-s2-abc"
        graph.mkdir(parents=True)
        (graph / "manifest.json").write_text(json.dumps(manifest(
            "s2", baseline_version="n1", shuttle_route_ids=["CR_R_r01"],
            shuttle_source={"plan_id": "plan-test", "revision": 1, "source_kind": "C_TEAM_CANDIDATE",
                            "confirm": {"confirm_id": "c1"}})))
        active = {"build_id": "g-s2-abc", "dir": str(graph), "url": "http://127.0.0.1:3/otp/gtfs/v1",
                  "confirm_id": "c1", "plan_id": "plan-test", "revision": 1, "algo_name": "합성 순환",
                  "confirmed_at": "2026-11-27T12:00:00+09:00", "graph_version": "s2",
                  "shuttle_version": "plan-test@1", "m07": {"pass": True, "fully_verified": False},
                  "service_date": {"simulated": "2026-10-28", "gtfs": "2026-11-02", "rule": "first_available"},
                  **extra}
        (self.fx.runtime / "strike-active.json").write_text(json.dumps(active, ensure_ascii=False))
        return active

    def test_apply_without_config_is_unavailable(self):
        status, body = self.fx.call("/api/citizen-routing/apply", "POST", {})
        self.assertEqual(status, 503)
        self.assertEqual(body["code"], "APPLY_NOT_CONFIGURED")

    def test_apply_status_reports_idle_and_active_confirm(self):
        self.write_active()
        status, body = self.fx.call("/api/citizen-routing/apply/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "idle")
        self.assertEqual(body["active"]["confirm_id"], "c1")
        self.assertEqual(body["active"]["shuttle_version"], "plan-test@1")

    def test_apply_status_tells_whether_a_given_confirm_is_applied(self):
        self.write_active(plan_sha256="a" * 64, plan_key="plan-test@1#aaaaaaaa")
        _, body = self.fx.call("/api/citizen-routing/apply/status?confirm_id=c1")
        self.assertIs(body["applied"], True)
        _, body = self.fx.call("/api/citizen-routing/apply/status?confirm_id=c9")
        self.assertIs(body["applied"], False)
        self.assertEqual(body["active"]["plan_key"], "plan-test@1#aaaaaaaa")

    def test_status_shows_applied_confirm_and_uses_active_strike_graph(self):
        self.write_active()
        with patch("backend.mode_service.healthy", return_value=True):
            (self.fx.strike / "graph.obj").write_bytes(b"x")
            _, body = self.fx.call("/api/citizen-routing/status")
        self.assertEqual(body["confirm"]["confirm_id"], "c1")
        self.assertEqual(body["confirm"]["algo_name"], "합성 순환")
        self.assertEqual(body["modes"]["strike"]["version"], "s2")

    def test_apply_busy_is_conflict(self):
        cfg = Path(self.tmp.name) / "apply-config.json"
        cfg.write_text("{}")
        self.fx.close()
        self.fx = ServerFixture(Path(self.tmp.name) / "second", apply_config=cfg)
        status_dir = self.fx.runtime / "apply"
        status_dir.mkdir(parents=True)
        (status_dir / "status.json").write_text(json.dumps({"state": "running", "pid": os.getpid(),
                                                            "step": "build", "steps": []}))
        status, body = self.fx.call("/api/citizen-routing/apply", "POST", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "APPLY_BUSY")

    def test_presets_empty_without_active_build(self):
        status, body = self.fx.call("/api/citizen-routing/presets")
        self.assertEqual((status, body["presets"]), (200, []))


class ThreeResultTests(unittest.TestCase):
    def test_three_results_have_same_shape_and_deltas(self):
        normal = manifest("n1", excluded_route_ids=[])
        strike = manifest("s2", baseline_version="n1", shuttle_route_ids=["CR_R_r01"],
                          shuttle_source={"plan_id": "plan-test", "revision": 1})
        calls = []

        def fake_plan(request, man, endpoint, categories):
            calls.append((man["version"], request.get("include_shuttle")))
            if man["version"] == "n1":
                routes = [route(1200, ["walk", "bus", "walk"])]
            elif request.get("include_shuttle") == "false":
                routes = [route(3000, ["walk"])]
            else:
                routes = [route(1800, ["walk", "shuttle", "walk"])]
            return {"version": man["version"], "routes": routes, "count": len(routes),
                    "excluded_route_ids": man.get("excluded_route_ids", [])}

        def fake_require(mode, directory, endpoint, normal_version=None):
            return normal if mode == "normal" else strike

        with patch("backend.mode_service.require_mode", side_effect=fake_require), \
                patch("backend.mode_service.plan", side_effect=fake_plan), \
                patch("backend.mode_service.load_categories", return_value={}):
            result = mode_service.compare_three({"departure_at": "x"}, Path("n"), "u1", Path("s"), "u2")
        self.assertEqual(set(result["results"]), {"normal", "strike_no_shuttle", "strike_shuttle"})
        for key, item in result["results"].items():
            self.assertIn("routes", item)
            self.assertIn(key, result["best"])
        self.assertEqual(result["best"]["strike_shuttle"]["duration_seconds"], 1800)
        self.assertTrue(result["best"]["strike_shuttle"]["uses_shuttle"])
        self.assertEqual(result["delta_seconds"], {"strike_no_shuttle": 1800, "strike_shuttle": 600})
        self.assertEqual(result["shuttle_gain_seconds"], 1200)
        self.assertEqual(sorted(calls), [("n1", "false"), ("s2", "false"), ("s2", "true")])

    def test_strike_without_shuttle_reports_missing_shuttle(self):
        normal = manifest("n1", excluded_route_ids=[])
        strike = manifest("s1", baseline_version="n1")

        def fake_plan(request, man, endpoint, categories):
            return {"version": man["version"], "routes": [route(900, ["walk"])], "count": 1}

        with patch("backend.mode_service.require_mode",
                   side_effect=lambda mode, *a, **k: normal if mode == "normal" else strike), \
                patch("backend.mode_service.plan", side_effect=fake_plan), \
                patch("backend.mode_service.load_categories", return_value={}):
            result = mode_service.compare_three({}, Path("n"), "u1", Path("s"), "u2")
        self.assertEqual(result["results"]["strike_shuttle"]["code"], "SHUTTLE_DATA_MISSING")
        self.assertIsNone(result["best"]["strike_shuttle"])


if __name__ == "__main__":
    unittest.main()
