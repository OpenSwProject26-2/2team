"""Exercise the local D API through its HTTP boundary."""

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_dashboard import Store, make_handler


class DashboardHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store()))
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.worker.join(timeout=2)
        cls.server.server_close()

    def get_json(self, path: str) -> dict:
        with urlopen(self.base + path, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            return json.load(response)

    def post_scenario(self, routes: list[dict]) -> dict:
        body = json.dumps(
            {"hour": "07", "routes": routes, "active_normal_route_ids": []}
        ).encode("utf-8")
        request = Request(
            self.base + "/api/d/demo/scenario/evaluate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            return json.load(response)

    def test_dashboard_routes_map_demand_and_sources(self) -> None:
        dashboard = self.get_json("/api/d/dashboard?mode=demo")
        routes = self.get_json("/api/d/routes?mode=demo")
        selected = self.get_json("/api/d/routes?mode=demo&kind=strike")
        map_data = self.get_json("/api/d/map?mode=demo")
        demand = self.get_json("/api/d/demand?mode=demo&hour=07")
        sources = self.get_json("/api/d/sources")

        self.assertEqual(dashboard["snapshot_id"], routes["snapshot_id"])
        self.assertEqual(dashboard["snapshot_id"], map_data["snapshot_id"])
        self.assertEqual(len(routes["routes"]), 7)
        self.assertTrue(selected["routes"])
        self.assertTrue(all(r["kind"] == "strike" for r in selected["routes"]))
        self.assertEqual(map_data["type"], "FeatureCollection")
        self.assertEqual(len(map_data["features"]), 17)
        self.assertEqual(demand["type"], "FeatureCollection")
        self.assertEqual(len(demand["features"]), 80)
        self.assertEqual(sources["demo"]["status"], "ready")
        self.assertEqual(sources["B"]["status"], "unavailable")

    def test_scenario_route_addition_and_removal_changes_unmet(self) -> None:
        empty = self.post_scenario([])
        shuttle = self.post_scenario(
            [
                {
                    "route_id": "demo-shuttle-01",
                    "stop_ids": ["d-a", "d-b", "d-c", "d-d"],
                    "vehicles": 5,
                }
            ]
        )
        removed = self.post_scenario([])

        self.assertEqual(empty, removed)
        self.assertEqual(empty["total"], shuttle["total"])
        self.assertGreater(shuttle["served"], empty["served"])
        self.assertLess(shuttle["unmet"], empty["unmet"])

    def test_real_mode_without_a_feed_reports_missing_data(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/d/dashboard?mode=real", timeout=2)
        with error.exception as response:
            self.assertEqual(response.code, 503)
            payload = json.load(response)
        self.assertEqual(payload["code"], "DATA_INCOMPLETE")

    def test_map_assets_and_district_api_are_served(self) -> None:
        expected = {
            "/vendor/maplibre-gl.mjs": "text/javascript",
            "/vendor/maplibre-gl-worker.mjs": "text/javascript",
            "/vendor/maplibre-gl.css": "text/css",
            "/vendor/openfreemap-liberty.json": "application/json",
            "/data/districts_all.geojson": "application/geo+json",
        }
        for path, content_type in expected.items():
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), content_type)
                self.assertGreater(int(response.headers["Content-Length"]), 100)

        districts = self.get_json("/api/d/districts")
        self.assertEqual([item["id"] for item in districts["districts"]],
                         ["guro", "dongjak", "gangseo", "gangdong"])
        self.assertIn("OpenStreetMap", districts["source"])
        with urlopen(self.base + "/data/districts_all.geojson", timeout=3) as response:
            boundary_geojson = json.load(response)
        self.assertEqual(len(boundary_geojson["features"]), 25)

    def test_simulation_report_api_and_missing_artifacts(self) -> None:
        options = self.get_json("/api/d/simulation/options")
        self.assertEqual(len(options["regions"]), 4)
        result = self.get_json(
            "/api/d/simulation/result?region=guro&fleet=11&variant=affected&plan=ours&period=exp&date=mean"
        )
        self.assertEqual(result["metrics"]["rate_percent"], 12.58)
        self.assertEqual(result["metrics"]["source"], "report_v4_5day_mean")
        self.assertEqual(result["routes"], [])
        self.assertEqual(result["outcome"]["status"], "report_only")
        self.assertIsNone(result["run_id"])
        runs = self.get_json("/api/d/simulation/runs")
        self.assertEqual(runs["source"], "unavailable")
        self.assertEqual(runs["runs"], [])
        self.assertIsNone(result["verification"]["pass"])
        self.assertEqual(
            len(self.get_json("/api/d/simulation/comparison")["regions"]), 4
        )
        self.assertEqual(self.get_json("/api/d/simulation/map")["map"]["features"], [])
        self.assertEqual(
            self.get_json("/api/d/simulation/candidates")["candidates"], []
        )
        with urlopen(self.base + "/simulation", timeout=2) as response:
            self.assertIn(b"simModeButton", response.read())
        table = self.get_json("/api/d/simulation/table")
        self.assertTrue(table["comparable"])
        self.assertEqual(len(table["rows"]), 2)
        summary = self.get_json("/api/d/simulation/seoul")
        self.assertEqual(len(summary["districts"]), 25)
        self.assertEqual(summary["aggregate"]["region_count"], 4)
        request = Request(
            self.base + "/api/d/simulation/selection",
            data=json.dumps(
                {
                    "region": "guro",
                    "fleet": 11,
                    "variant": "affected",
                    "plan": "ours",
                    "period": "exp",
                    "date": "mean",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 503)
        self.assertIsNone(self.get_json("/api/d/simulation/selection")["selected"])


if __name__ == "__main__":
    unittest.main()
