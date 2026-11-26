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

    def post_scenario(self, routes: list[dict], normal_ids: list[str] | None = None,
                      strike_ids: list[str] | None = None, date: str | None = None) -> dict:
        payload = {"hour": "07", "routes": routes, "active_normal_route_ids": normal_ids or []}
        if date is not None:
            payload["date"] = date
        if strike_ids is not None:
            payload["strike_route_ids"] = strike_ids
        body = json.dumps(payload).encode("utf-8")
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

    def test_selected_route_geometry_returns_full_path(self) -> None:
        selected = self.get_json(
            "/api/d/route-geometry?mode=demo&district=all&route_id=demo-city-421"
        )
        self.assertEqual(selected["route_id"], "demo-city-421")
        self.assertEqual(selected["geometry"]["type"], "LineString")
        self.assertGreaterEqual(len(selected["geometry"]["coordinates"]), 2)
        self.assertEqual(
            [stop["order"] for stop in selected["ordered_stops"]],
            list(range(1, len(selected["ordered_stops"]) + 1)),
        )

    def test_transport_demand_without_condition_file_is_explicitly_unavailable(self) -> None:
        result = self.get_json(
            "/api/d/simulation/transport-demand?region=guro&fleet=11&plan=ours"
            "&period=exp&date=2026-10-26&disabled_modes=metro"
        )
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["selection"]["disabled_modes"], ["metro"])

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

    def test_normal_transport_changes_spatial_unmet_demand(self) -> None:
        routes = self.get_json("/api/d/routes?mode=demo")["routes"]
        normal_ids = [route["route_id"] for route in routes if route["kind"] in
                      {"city", "village", "gyeonggi", "metro"}]
        baseline = self.post_scenario([], normal_ids)
        for removed_id in normal_ids:
            with self.subTest(removed=removed_id):
                changed = self.post_scenario(
                    [], [route_id for route_id in normal_ids if route_id != removed_id]
                )
                self.assertEqual(changed["total"], baseline["total"])
                self.assertGreater(changed["unmet"], baseline["unmet"])
                self.assertTrue(any(
                    before["properties"]["unmet"] != after["properties"]["unmet"]
                    for before, after in zip(baseline["features"], changed["features"])
                ))

    def test_releasing_strike_route_reduces_affected_and_unmet_demand(self) -> None:
        routes = self.get_json("/api/d/routes?mode=demo")["routes"]
        normal_ids = [r["route_id"] for r in routes if r["kind"] in
                      {"city", "village", "gyeonggi", "metro"}]
        strike_ids = [r["route_id"] for r in routes if r["kind"] == "strike"]
        shuttle = [{"route_id": "demo-shuttle-01", "stop_ids": ["d-a", "d-b", "d-c", "d-d"], "vehicles": 5}]
        both = self.post_scenario(shuttle, normal_ids, strike_ids)
        one = self.post_scenario(shuttle, normal_ids, strike_ids[:1])
        none = self.post_scenario(shuttle, normal_ids, [])
        self.assertEqual(both["total"], one["total"])
        self.assertEqual(one["total"], none["total"])
        self.assertGreater(both["unmet"], one["unmet"])
        self.assertGreater(one["unmet"], none["unmet"])
        self.assertEqual(none["restored_strike_route_ids"], sorted(strike_ids))
        self.assertTrue(any(effect["kind"] == "restored_city" and effect["served"] > 0
                            for effect in none["route_effects"]))
        self.assertTrue(any(
            a["properties"]["unmet"] > b["properties"]["unmet"]
            for a, b in zip(both["features"], one["features"])
        ))

    def test_shuttle_coverage_and_vehicle_count_change_spatial_unmet(self) -> None:
        routes = self.get_json("/api/d/routes?mode=demo")["routes"]
        normal_ids = [r["route_id"] for r in routes if r["kind"] in
                      {"city", "village", "gyeonggi", "metro"}]
        def shuttle(stops: list[str], vehicles: int) -> list[dict]:
            return [{"route_id": "demo-shuttle-01", "stop_ids": stops, "vehicles": vehicles}]
        no_shuttle = self.post_scenario([], normal_ids)
        one = self.post_scenario(shuttle(["d-a", "d-b", "d-c", "d-d"], 1), normal_ids)
        five = self.post_scenario(shuttle(["d-a", "d-b", "d-c", "d-d"], 5), normal_ids)
        poor_path = self.post_scenario(shuttle(["d-h", "d-i"], 5), normal_ids)
        self.assertGreater(no_shuttle["unmet"], one["unmet"])
        self.assertGreater(one["unmet"], five["unmet"])
        self.assertGreater(poor_path["unmet"], five["unmet"])
        self.assertEqual(one["total"], five["total"])
        self.assertTrue(any(
            a["properties"]["unmet"] != b["properties"]["unmet"]
            for a, b in zip(one["features"], five["features"])
        ))

    def test_demo_date_changes_od_and_preserves_allocation_invariants(self) -> None:
        dates = self.get_json("/api/d/dashboard?mode=demo")["demo_dates"]
        self.assertEqual(dates, ["2026-11-13", "2026-11-14", "2026-11-16"])
        totals = []
        for day in dates:
            with self.subTest(date=day):
                result = self.post_scenario([], date=day)
                demand = self.get_json(f"/api/d/demand?mode=demo&hour=07&date={day}")
                self.assertEqual(result["total"], demand["total"])
                self.assertEqual(result["total"], result["served"] + result["unmet"])
                self.assertEqual(result["served"], sum(item["served"] for item in result["route_effects"]))
                self.assertEqual(result["unmet"], sum(item["properties"]["unmet"] for item in result["features"]))
                totals.append(result["total"])
        self.assertEqual(len(set(totals)), len(dates))

    def test_real_mode_without_a_feed_reports_missing_data(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/d/dashboard?mode=real", timeout=2)
        with error.exception as response:
            self.assertEqual(response.code, 503)
            payload = json.load(response)
        self.assertEqual(payload["code"], "DATA_INCOMPLETE")

    def test_map_assets_and_district_api_are_served(self) -> None:
        for path in ("/", "/simulation"):
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                page = response.read().decode("utf-8")
                self.assertIn('class="app lab-active"', page)
                self.assertIn('.lab-active .main-grid>.side,.lab-active .main-grid>.right{display:none!important}', page)
        expected = {
            "/demand-circles.js": "text/javascript",
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
        self.assertEqual(len(districts["districts"]), 25)
        self.assertTrue({"guro", "dongjak", "gangseo", "gangdong"}.issubset(
            {item["id"] for item in districts["districts"]}))
        self.assertIn("OpenStreetMap", districts["source"])
        with urlopen(self.base + "/data/districts_all.geojson", timeout=3) as response:
            boundary_geojson = json.load(response)
        self.assertEqual(len(boundary_geojson["features"]), 25)
        compact = self.get_json("/api/d/network?mode=demo&district=all&compact=1")
        self.assertIsNone(compact["route_stops"])
        self.assertTrue(compact["map"]["features"])

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
