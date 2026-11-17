"""D 지도 API가 합성 자료와 GTFS 선형 출처를 구분하는지 검증."""

import io
import json
import sys
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_dashboard import Store
from busstrike.api.d_geo import (
    DataError,
    demand_geojson,
    load_a_gtfs,
    load_demo,
    map_geojson,
)
from busstrike.api.d_scenario import evaluate_demo

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "d_dashboard" / "demo.json"


@contextmanager
def test_zip(data: bytes):
    path = DEMO.parent / f"test-{uuid4().hex}.zip"
    path.write_bytes(data)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def small_gtfs(
    with_shapes: bool,
    route_type: str = "3",
    route_desc: str = "",
    alternate_trip: bool = False,
) -> bytes:
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,가상 A,37.55,126.97\nB,가상 B,37.56,126.98\n",
        "routes.txt": f"route_id,route_short_name,route_type,agency_id,route_desc\nR,7,{route_type},Demo,{route_desc}\n",
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR,S,T,SH\n",
        "stop_times.txt": "trip_id,stop_id,stop_sequence\nT,A,1\nT,B,2\n",
        "calendar_dates.txt": "service_id,date,exception_type\nS,20261102,1\n",
        "feed_info.txt": "feed_version\ntiny-v1\n",
    }
    if with_shapes:
        files["shapes.txt"] = (
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nSH,37.55,126.97,1\nSH,37.56,126.98,2\n"
        )
    if alternate_trip:
        files["stops.txt"] += "C,가상 C,37.57,126.99\n"
        files["trips.txt"] += "R,S,T2,SH\n"
        files["stop_times.txt"] += "T2,A,1\nT2,C,2\n"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


class DashboardDataTests(unittest.TestCase):
    def test_demo_capacity_allocation_changes_with_route_and_preserves_people(self):
        snapshot = load_demo(DEMO)
        route = {
            "route_id": "demo-shuttle-01",
            "stop_ids": ["d-a", "d-b", "d-c", "d-d"],
            "vehicles": 5,
        }
        empty = evaluate_demo(snapshot, {"hour": "07", "routes": []})
        active = evaluate_demo(snapshot, {"hour": "07", "routes": [route]})
        repeated = evaluate_demo(snapshot, {"hour": "07", "routes": [route]})
        self.assertEqual(empty["served"], 0)
        self.assertGreater(active["served"], 0)
        self.assertEqual(active, repeated)
        self.assertEqual(active["total"], active["served"] + active["unmet"])
        self.assertEqual(
            sum(f["properties"]["served"] for f in active["features"]), active["served"]
        )
        self.assertTrue(
            all(
                f["properties"]["total"]
                == f["properties"]["served"] + f["properties"]["unmet"]
                for f in active["features"]
            )
        )

    def test_demo_rejects_overassigned_fleet_and_duplicate_stops(self):
        snapshot = load_demo(DEMO)
        with self.assertRaises(DataError) as fleet:
            evaluate_demo(
                snapshot,
                {
                    "hour": "07",
                    "routes": [
                        {
                            "route_id": "demo-shuttle-01",
                            "stop_ids": ["d-a", "d-b"],
                            "vehicles": 5,
                        },
                        {
                            "route_id": "demo-shuttle-02",
                            "stop_ids": ["d-a", "d-c"],
                            "vehicles": 4,
                        },
                    ],
                },
            )
        self.assertEqual(fleet.exception.code, "PLAN_INFEASIBLE")
        with self.assertRaises(DataError) as stops:
            evaluate_demo(
                snapshot,
                {
                    "hour": "07",
                    "routes": [
                        {
                            "route_id": "demo-shuttle-01",
                            "stop_ids": ["d-a", "d-a"],
                            "vehicles": 1,
                        },
                    ],
                },
            )
        self.assertEqual(stops.exception.code, "INPUT_INVALID")

    def test_demo_normal_route_toggle_changes_unmet_not_total(self):
        snapshot = load_demo(DEMO)
        shuttle = {
            "route_id": "demo-shuttle-01",
            "stop_ids": ["d-a", "d-b", "d-c", "d-d"],
            "vehicles": 5,
        }
        all_normal = [
            route["route_id"]
            for route in snapshot.routes
            if route["kind"] in {"city", "gyeonggi", "village", "metro"}
        ]
        baseline = evaluate_demo(
            snapshot,
            {"hour": "07", "routes": [shuttle], "active_normal_route_ids": all_normal},
        )
        self.assertEqual(len(baseline["active_normal_route_ids"]), 4)
        for removed in ("demo-city-421", "demo-gyeonggi-5500"):
            reduced = evaluate_demo(
                snapshot,
                {
                    "hour": "07",
                    "routes": [shuttle],
                    "active_normal_route_ids": [
                        route_id for route_id in all_normal if route_id != removed
                    ],
                },
            )
            self.assertEqual(reduced["total"], baseline["total"])
            self.assertLess(reduced["served"], baseline["served"])
            self.assertGreater(reduced["unmet"], baseline["unmet"])
            self.assertNotEqual(
                [f["properties"]["unmet"] for f in reduced["features"]],
                [f["properties"]["unmet"] for f in baseline["features"]],
            )
            self.assertNotEqual(
                reduced["scenario_fingerprint"], baseline["scenario_fingerprint"]
            )

    def test_demo_rejects_unknown_normal_route(self):
        with self.assertRaises(DataError) as invalid:
            evaluate_demo(
                load_demo(DEMO),
                {
                    "hour": "07",
                    "routes": [],
                    "active_normal_route_ids": ["demo-strike-700"],
                },
            )
        self.assertEqual(invalid.exception.code, "INPUT_INVALID")

    def test_demo_has_actual_geojson_types_and_no_invented_evaluation(self):
        snapshot = load_demo(DEMO)
        features = map_geojson(snapshot)["features"]
        self.assertEqual(snapshot.source, "synthetic")
        self.assertEqual(
            len([f for f in features if f["geometry"]["type"] == "LineString"]), 7
        )
        self.assertEqual(
            len([f for f in features if f["geometry"]["type"] == "Point"]), 10
        )
        self.assertEqual(
            snapshot.summary()["evaluation_status"], "not_independently_verified"
        )
        demand = demand_geojson(snapshot, "07", "origin")
        self.assertEqual(len(demand["features"]), 80)
        self.assertEqual(
            demand["total"],
            sum(f["properties"]["passenger_count"] for f in demand["features"]),
        )
        self.assertEqual(
            demand_geojson(snapshot, "07", "unmet")["status"], "not_computed"
        )

    def test_demo_road_geometry_is_labeled_as_driving_estimate(self):
        snapshot = load_demo(DEMO)
        shuttle = next(route for route in snapshot.routes if route["kind"] == "shuttle")
        self.assertEqual(shuttle["geometry_status"], "osm_osrm_driving_estimate")
        self.assertGreater(len(shuttle["coordinates"]), len(shuttle["stop_ids"]))
        self.assertGreater(shuttle["road_distance_m"], 0)
        self.assertLessEqual(shuttle["max_snap_distance_m"], 120)
        self.assertIn("OpenStreetMap", shuttle["geometry_source"])
        self.assertEqual(
            next(
                feature
                for feature in map_geojson(snapshot)["features"]
                if feature["properties"].get("route_id") == shuttle["route_id"]
            )["properties"]["geometry_status"],
            "osm_osrm_driving_estimate",
        )

    def test_demo_rejects_road_geometry_for_changed_waypoints(self):
        road_file = DEMO.with_name("road_shapes.json")
        road = json.loads(road_file.read_text(encoding="utf-8"))
        road["routes"][0]["source_waypoints"][0][0] += 0.001
        original_read_bytes = Path.read_bytes

        def read_bytes(path):
            if path == road_file:
                return json.dumps(road).encode("utf-8")
            return original_read_bytes(path)

        with (
            patch.object(Path, "read_bytes", read_bytes),
            self.assertRaises(DataError) as mismatch,
        ):
            load_demo(DEMO)
        self.assertEqual(mismatch.exception.code, "DATA_VERSION_MISMATCH")

    def test_gtfs_without_shapes_never_draws_stop_to_stop_line(self):
        with test_zip(small_gtfs(False)) as path:
            snapshot = load_a_gtfs(path, "2026-11-02")
        self.assertEqual(len(snapshot.routes), 1)
        self.assertIsNone(snapshot.routes[0]["coordinates"])
        self.assertEqual(snapshot.routes[0]["representative_stop_ids"], ["A", "B"])
        self.assertTrue(
            all(
                f["geometry"]["type"] == "Point"
                for f in map_geojson(snapshot)["features"]
            )
        )
        self.assertIsNone(snapshot.fleet)
        self.assertIsNone(snapshot.plan_id)

    def test_gtfs_shapes_keep_lon_lat_order(self):
        with test_zip(small_gtfs(True)) as path:
            snapshot = load_a_gtfs(path, "2026-11-02")
        line = next(
            f
            for f in map_geojson(snapshot)["features"]
            if f["geometry"]["type"] == "LineString"
        )
        self.assertEqual(line["geometry"]["coordinates"][0], [126.97, 37.55])
        self.assertEqual(line["properties"]["geometry_status"], "gtfs_shape")

    def test_rail_trip_stops_are_marked_as_stations(self):
        with test_zip(small_gtfs(False, route_type="1")) as path:
            snapshot = load_a_gtfs(path, "2026-11-02")
        self.assertEqual([stop["kind"] for stop in snapshot.stops], ["metro", "metro"])

    def test_rail_schematic_includes_adjacent_stations_from_alternate_trips(self):
        with test_zip(small_gtfs(False, route_type="1", alternate_trip=True)) as path:
            snapshot = load_a_gtfs(path, "2026-11-02")
        self.assertEqual(
            snapshot.routes[0]["schematic_stop_links"], [["A", "B"], ["A", "C"]]
        )
        self.assertIsNone(snapshot.routes[0]["coordinates"])
        self.assertTrue(
            all(
                feature["geometry"]["type"] == "Point"
                for feature in map_geojson(snapshot)["features"]
            )
        )

    def test_a_route_desc_classifies_village_and_preserves_stop_order(self):
        with test_zip(
            small_gtfs(False, route_desc="서울마을버스", alternate_trip=True)
        ) as path:
            snapshot = load_a_gtfs(path, "2026-11-02")
        self.assertEqual(snapshot.routes[0]["kind"], "village")
        self.assertEqual(snapshot.routes[0]["route_desc"], "서울마을버스")
        self.assertEqual(snapshot.routes[0]["representative_stop_ids"], ["A", "B"])
        self.assertEqual(snapshot.routes[0]["all_stop_ids"], ["A", "B", "C"])
        self.assertEqual(
            [stop["name"] for stop in snapshot.stops], ["가상 A", "가상 B", "가상 C"]
        )

    def test_unsupported_day_is_out_of_scope(self):
        with (
            test_zip(small_gtfs(False)) as path,
            self.assertRaises(DataError) as context,
        ):
            load_a_gtfs(path, "2026-11-07")
        self.assertEqual(context.exception.code, "OUT_OF_SCOPE")

    def test_nested_release_is_read_without_editing_source(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("gtfs_bus_rail_combined.zip", small_gtfs(True))
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr(
                "data/10_data_walk-links-bus-rail_test.zip", inner.getvalue()
            )
        with test_zip(outer.getvalue()) as path:
            original = path.read_bytes()
            snapshot = load_a_gtfs(path, "2026-11-02")
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(snapshot.routes), 1)

    def test_failed_refresh_preserves_previous_snapshot(self):
        with test_zip(small_gtfs(True)) as path:
            store = Store(path, "2026-11-02")
            original = store.get("real").snapshot_id
            path.write_bytes(b"invalid ZIP")
            with self.assertRaises(DataError):
                store.refresh()
            self.assertEqual(store.get("real").snapshot_id, original)
            self.assertEqual(
                store.sources()["A"]["last_error"]["code"], "DATA_INCOMPLETE"
            )


if __name__ == "__main__":
    unittest.main()
