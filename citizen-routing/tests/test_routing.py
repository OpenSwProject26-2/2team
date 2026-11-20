import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from backend.gtfs_adapter import read_feed, validate, remove_struck_routes, namespace_shuttle, rows
from backend.bus_categories import classify_bus
from backend.route_service import plan, InputError, decode_polyline


class FeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal = read_feed(REPO / "tests/fixtures/routing/gtfs_preview/normal")
        cls.shuttle = read_feed(REPO / "tests/fixtures/routing/gtfs_preview/shuttle")

    def test_strike_removes_trips_from_graph_feed(self):
        self.assertEqual(validate(self.normal)["routes"], 2)
        route_id = rows(self.normal, "routes.txt")[1][0]["route_id"]
        filtered = remove_struck_routes(self.normal, {route_id})
        self.assertEqual(validate(filtered)["routes"], 1)
        remaining = {r["trip_id"] for r in rows(filtered, "trips.txt")[1]}
        self.assertTrue(remaining)
        self.assertTrue(all(c["trip_id"] in remaining for c in rows(filtered, "stop_times.txt")[1]))

    def test_shuttle_ids_are_namespaced(self):
        converted, route_ids = namespace_shuttle(self.shuttle)
        self.assertTrue(all(r.startswith("CR_R_") for r in route_ids))
        self.assertTrue(all(s["stop_id"].startswith("CR_S_") for s in rows(converted, "stops.txt")[1]))
        self.assertEqual(validate(converted)["trips"], validate(self.shuttle)["trips"])


class BusCategoryTests(unittest.TestCase):
    def test_gtfs_description_classifies_only_supported_bus_types(self):
        examples = [
            ({"route_desc": "\uc11c\uc6b8\ub9c8\uc744\ubc84\uc2a4"}, "village"),
            ({"route_desc": "\uc11c\uc6b8\uac04\uc120\ubc84\uc2a4"}, "city"),
            ({"route_desc": "\uacbd\uae30\uc2dc\ub0b4\ubc84\uc2a4"}, "gyeonggi"),
            ({"agency_id": "GYEONGGI_BUS"}, "gyeonggi"),
            ({"route_desc": "\ubbf8\ud655\uc778", "agency_id": "SEOUL_BUS"}, "unclassified"),
        ]
        for route, expected in examples:
            with self.subTest(route=route):
                self.assertEqual(classify_bus(route), expected)

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"version": "test", "source_kind": "DEMO",
                         "shuttle_route_ids": ["CR_R_S-01"], "excluded_route_ids": []}
        self.params = {"origin_lat": "37.55", "origin_lon": "126.99",
                       "destination_lat": "37.54", "destination_lon": "127.01",
                       "departure_at": "2026-11-07T07:00:00+09:00"}

    def test_no_shuttle_uses_otp_search_filter(self):
        with patch("backend.route_service.query", return_value={"edges": []}) as mock:
            result = plan({**self.params, "include_shuttle": "false"}, self.manifest, "http://otp")
        self.assertEqual(result["count"], 0)
        self.assertEqual(mock.call_args.args[1]["preferences"]["transit"]["filters"][0]["exclude"][0]["routes"],
                         ["SHUTTLE:CR_R_S-01"])

    def test_otp_itinerary_is_returned_without_invention(self):
        node = {
            "start": "2026-11-07T07:05:00+09:00", "end": "2026-11-07T07:17:00+09:00",
            "duration": 720, "walkTime": 120, "waitingTime": 60, "numberOfTransfers": 0,
            "legs": [{"mode": "BUS", "duration": 540, "distance": 2300, "transitLeg": True,
                      "start": {"scheduledTime": "2026-11-07T07:07:00+09:00"},
                      "end": {"scheduledTime": "2026-11-07T07:16:00+09:00"},
                      "from": {"name": "A", "lat": 37.55, "lon": 126.99, "stop": {"gtfsId": "SHUTTLE:CR_S_A"}},
                      "to": {"name": "B", "lat": 37.54, "lon": 127.01, "stop": {"gtfsId": "SHUTTLE:CR_S_B"}},
                      "route": {"gtfsId": "SHUTTLE:CR_R_S-01", "shortName": "S-01"},
                      "legGeometry": {"points": ""}}]
        }
        with patch("backend.route_service.query", return_value={"edges": [{"node": node}]}):
            result = plan(self.params, self.manifest, "http://otp")
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["routes"][0]["uses_shuttle"])
        self.assertEqual(result["routes"][0]["legs"][0]["geometry"], [])

    def test_missing_gtfs_shapes_uses_stop_schematic(self):
        node = {
            "start": "2026-11-07T07:00:00+09:00", "end": "2026-11-07T07:10:00+09:00",
            "duration": 600, "legs": [{
                "mode": "BUS", "duration": 600,
                "start": {"scheduledTime": "2026-11-07T07:00:00+09:00"},
                "end": {"scheduledTime": "2026-11-07T07:10:00+09:00"},
                "from": {"name": "A", "lat": 37.55, "lon": 126.99},
                "to": {"name": "B", "lat": 37.54, "lon": 127.01},
                "route": {"gtfsId": "A:BUS1", "shortName": "1"},
                "legGeometry": {"points": "_p~iF~ps|U_ulLnnqC_mqNvxq\\@"},
                "stopCalls": [{"stopLocation": {"lat": 37.55, "lon": 126.99}},
                              {"stopLocation": {"lat": 37.545, "lon": 127.0}},
                              {"stopLocation": {"lat": 37.54, "lon": 127.01}}]
            }]
        }
        manifest = {**self.manifest, "existing_stats": {"shapes": False}}
        with patch("backend.route_service.query", return_value={"edges": [{"node": node}]}):
            result = plan(self.params, manifest, "http://otp", {"A:BUS1": "city"})
        self.assertFalse(result["routes"][0]["has_full_geometry"])
        leg = result["routes"][0]["legs"][0]
        self.assertEqual(leg["geometry_status"], "stop_schematic")
        self.assertEqual(leg["bus_category"], "city")
        self.assertEqual(leg["geometry"], [[126.99, 37.55], [127.0, 37.545], [127.01, 37.54]])

    def test_routes_are_ordered_by_departure_time(self):
        leg = {"mode": "WALK", "duration": 600,
               "from": {"name": "A", "lat": 37.55, "lon": 126.99},
               "to": {"name": "B", "lat": 37.54, "lon": 127.01}}
        departures = ["07:20", "07:05", "07:12"]
        nodes = [{"start": "2026-11-07T" + time + ":00+09:00",
                  "end": "2026-11-07T08:00:00+09:00", "duration": 600,
                  "legs": [leg]} for time in departures]
        with patch("backend.route_service.query", return_value={"edges": [{"node": node} for node in nodes]}):
            result = plan(self.params, self.manifest, "http://otp")
        self.assertEqual([route["start"][11:16] for route in result["routes"]],
                         ["07:05", "07:12", "07:20"])

    def test_invalid_coordinates_fail_before_otp(self):
        with self.assertRaises(InputError):
            plan({**self.params, "origin_lat": "NaN"}, self.manifest, "http://otp")


if __name__ == "__main__":
    unittest.main()
