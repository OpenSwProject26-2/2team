"""The GIS corridor must pass a GTFS coordinate check before display."""

import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from busstrike.api.d_dashboard import Store
from busstrike.api.d_bus_routing import roadnet_route, roadnet_partial_route, roadnet_pattern_route
from busstrike.api.d_geo import MapSnapshot, map_geojson
from busstrike.api.d_geo import DataError
from busstrike.api.d_geometry import bus_geometry_view, rail_geometry_view
from busstrike.api.d_network import network_view


class RailGeometryTests(unittest.TestCase):
    def test_pattern_roadnet_keeps_different_paths(self):
        calls = {"1": [126.88, 37.49], "2": [126.89, 37.49], "3": [126.90, 37.49],
                 "4": [126.90, 37.495]}
        class FakeRoadNet:
            stops = {sid: (point[1], point[0]) for sid, point in calls.items()}
            stop_nodes = {sid: index for index, sid in enumerate(calls)}
            def path_any(self, left, right):
                return [calls[left], [(calls[left][0] + calls[right][0]) / 2,
                                      (calls[left][1] + calls[right][1]) / 2 + .0002], calls[right]]
        route = {"route_id": "R", "short_name": "R", "agency": "A", "kind": "village",
                 "representative_stop_ids": ["1", "2", "3"], "all_stop_ids": list(calls)}
        feature = roadnet_pattern_route(route, {sid: {"coordinates": point} for sid, point in calls.items()},
                                        FakeRoadNet(), "test", (("1", "2", "3"), ("1", "2", "4")))
        self.assertEqual(feature["properties"]["pattern_indices"], [0, 1])
        self.assertEqual(feature["geometry"]["type"], "MultiLineString")

    def test_partial_roadnet_keeps_only_connected_pairs(self):
        calls = {str(i): [127 + i * .001, 37.5] for i in range(1, 5)}
        route = {"route_id": "R", "short_name": "R", "agency": "A", "kind": "village",
                 "representative_stop_ids": ["1", "2", "3", "4"], "coordinates": None}
        class FakeRoadNet:
            stops = {sid: (point[1], point[0]) for sid, point in calls.items()}
            stop_nodes = {"1": 1, "2": 2, "4": 4}
            def path_any(self, left, right):
                return [calls[left], [(calls[left][0] + calls[right][0]) / 2, 37.5002], calls[right]]
        feature = roadnet_partial_route(route, {sid: {"stop_id": sid, "coordinates": point}
                                               for sid, point in calls.items()}, FakeRoadNet(), "test")
        self.assertEqual(feature["properties"]["partial_stop_runs"], [["1", "2"]])
        snapshot = MapSnapshot(snapshot_id="test", source="a_gtfs_approximate_schedule",
                               service_date="2026-11-02", scenario_id=None, plan_id=None, revision=None,
                               routes=(route,), stops=tuple({"stop_id": sid, "name": sid, "coordinates": point}
                                                            for sid, point in calls.items()), demand=(), fleet=None, warnings=())
        matched = bus_geometry_view(snapshot, {"type": "FeatureCollection", "features": [feature]}).routes[0]
        self.assertEqual(matched["geometry_status"], "roadnet_partial_estimate")
        self.assertEqual(matched["geometry_type"], "MultiLineString")
        feature["properties"]["partial_stop_runs"] = [["1", "4"]]
        rejected = bus_geometry_view(snapshot, {"type": "FeatureCollection", "features": [feature]}).routes[0]
        self.assertIsNone(rejected["coordinates"])

    def test_roadnet_path_uses_stop_order_and_rejects_straight_fallback(self):
        calls = {"1": [126.88, 37.49], "2": [126.89, 37.49], "3": [126.90, 37.49]}
        route = {"route_id": "R1", "short_name": "버스", "agency": "A",
                 "representative_stop_ids": ["1", "2", "3"]}
        class FakeRoadNet:
            stops = {sid: (point[1], point[0]) for sid, point in calls.items()}
            stop_nodes = {sid: (index,) for index, sid in enumerate(calls)}
            segments = {
                ("1", "2"): [[126.88, 37.49], [126.885, 37.4902], [126.89, 37.49]],
                ("2", "3"): [[126.89, 37.49], [126.895, 37.4902], [126.90, 37.49]],
            }
            def path_any(self, left, right):
                return self.segments[(left, right)]
        roadnet = FakeRoadNet()
        feature = roadnet_route(route, {sid: {"coordinates": point} for sid, point in calls.items()}, roadnet, "osm-test")
        self.assertEqual(feature["properties"]["geometry_source"], "roadnet_estimate")
        self.assertEqual(feature["geometry"]["coordinates"][2], calls["2"])
        roadnet.segments[("2", "3")] = [calls["2"], calls["3"]]
        with self.assertRaises(DataError):
            roadnet_route(route, {sid: {"coordinates": point} for sid, point in calls.items()}, roadnet, "osm-test")

    def test_verified_rail_line_keeps_stop_order_and_clips_to_district(self):
        stops = tuple(
            {"stop_id": str(index), "name": str(index), "kind": "metro", "coordinates": point}
            for index, point in enumerate(
                ([126.88, 37.49], [126.89, 37.49], [126.90, 37.49]), 1
            )
        )
        snapshot = MapSnapshot(
            snapshot_id="test", source="a_gtfs_approximate_schedule",
            service_date="2026-11-02", scenario_id=None, plan_id=None,
            revision=None,
            routes=({"route_id": "L5", "short_name": "5호선", "kind": "metro",
                     "representative_stop_ids": ["1", "2", "3"],
                     "all_stop_ids": ["1", "2", "3"], "coordinates": None,
                     "geometry_status": "unavailable"},),
            stops=stops, demand=(), fleet=None, warnings=(),
        )
        gis = {"type": "FeatureCollection", "features": [{
            "properties": {"sub_nm": "서울5호선"},
            "geometry": {"type": "MultiLineString", "coordinates": [[
                [126.875, 37.49], [126.89, 37.49], [126.905, 37.49]
            ]]},
        }]}
        matched = rail_geometry_view(snapshot, gis)
        route = matched.routes[0]
        self.assertEqual(route["geometry_status"], "rail_gis_verified_corridor")
        self.assertEqual(route["representative_stop_ids"], ["1", "2", "3"])
        self.assertEqual(route["geometry_validation"]["stops_within_250m"], 3)
        self.assertEqual(map_geojson(matched)["features"][0]["geometry"]["type"], "MultiLineString")
        scoped = network_view(matched, "guro", "inside")
        self.assertEqual(scoped["routes"][0]["geometry_type"], "MultiLineString")
        self.assertTrue(scoped["routes"][0]["coordinates"])

    def test_mismatched_stop_keeps_schematic(self):
        snapshot = MapSnapshot(
            snapshot_id="test", source="a_gtfs_approximate_schedule",
            service_date="2026-11-02", scenario_id=None, plan_id=None,
            revision=None,
            routes=({"route_id": "L5", "short_name": "5호선", "kind": "metro",
                     "representative_stop_ids": ["1", "2", "3"],
                     "coordinates": None, "geometry_status": "unavailable"},),
            stops=tuple({"stop_id": str(i), "coordinates": p} for i, p in enumerate(
                ([126.88, 37.49], [126.89, 37.49], [126.90, 37.51]), 1
            )), demand=(), fleet=None, warnings=(),
        )
        gis = {"type": "FeatureCollection", "features": [{
            "properties": {"sub_nm": "서울5호선"},
            "geometry": {"type": "LineString", "coordinates": [[126.88, 37.49], [126.90, 37.49]]},
        }]}
        route = rail_geometry_view(snapshot, gis).routes[0]
        self.assertIsNone(route["coordinates"])
        self.assertIn("mismatch", route["validation_warnings"][0])

    def test_single_outdated_station_uses_labeled_partial_corridor(self):
        ids = [str(i) for i in range(20)]
        stops = tuple({"stop_id": sid, "coordinates": [126.8 + i * .005, 37.5 + (.01 if i == 10 else 0)]}
                      for i, sid in enumerate(ids))
        snapshot = MapSnapshot(
            snapshot_id="test", source="a_gtfs_approximate_schedule",
            service_date="2026-11-02", scenario_id=None, plan_id=None,
            revision=None,
            routes=({"route_id": "L7", "short_name": "7호선", "kind": "metro",
                     "representative_stop_ids": ids, "coordinates": None,
                     "geometry_status": "unavailable"},),
            stops=stops, demand=(), fleet=None, warnings=(),
        )
        gis = {"type": "FeatureCollection", "features": [{
            "properties": {"sub_nm": "서울7호선"},
            "geometry": {"type": "LineString", "coordinates": [[126.8, 37.5], [126.9, 37.5]]},
        }]}
        route = rail_geometry_view(snapshot, gis).routes[0]
        self.assertEqual(route["geometry_status"], "rail_gis_partial_corridor")
        self.assertEqual(route["geometry_validation"]["unmatched_stop_ids"], ["10"])

    def test_bus_path_requires_id_agency_and_order_crosswalk(self):
        points = [[126.88, 37.49], [126.89, 37.49], [126.90, 37.49]]
        snapshot = MapSnapshot(
            snapshot_id="bus", source="a_gtfs_approximate_schedule",
            service_date="2026-11-02", scenario_id=None, plan_id=None,
            revision=None,
            routes=({"route_id": "A-17", "short_name": "구로01", "agency": "agency-1",
                     "kind": "village", "representative_stop_ids": ["1", "2", "3"],
                     "coordinates": None, "geometry_status": "unavailable"},),
            stops=tuple({"stop_id": str(i), "coordinates": p} for i, p in enumerate(points, 1)),
            demand=(), fleet=None, warnings=(),
        )
        props = {"a_gtfs_route_id": "A-17", "source_route_id": "source-42",
                 "route_short_name": "구로01", "agency_id": "agency-1",
                 "stop_ids": ["1", "2", "3"], "geometry_source": "official",
                 "source_namespace": "Seoul OA-15264", "source_version": "2026-11-02"}
        feature = {"type": "Feature", "properties": props,
                   "geometry": {"type": "LineString", "coordinates": points}}
        data = {"type": "FeatureCollection", "features": [feature]}
        self.assertEqual(bus_geometry_view(snapshot, data).routes[0]["geometry_status"], "official")
        feature["properties"] = {**props, "geometry_source": "roadnet_estimate",
                                 "source_namespace": "RoadNet / OSM"}
        self.assertEqual(bus_geometry_view(snapshot, data).routes[0]["geometry_status"], "roadnet_estimate")
        feature["properties"] = {**props, "stop_ids": ["3", "2", "1"]}
        self.assertIsNone(bus_geometry_view(snapshot, data).routes[0]["coordinates"])

    def test_reference_store_loads_official_bus_path_as_map_line(self):
        points = [[126.88, 37.49], [126.885, 37.4902], [126.89, 37.49]]
        snapshot = MapSnapshot(
            snapshot_id="reference-test", source="reference", service_date="2026-11-02",
            scenario_id=None, plan_id=None, revision=None,
            routes=({"route_id": "42", "short_name": "구로01", "agency": "operator",
                     "kind": "village", "representative_stop_ids": ["1", "2"],
                     "all_stop_ids": ["1", "2"], "coordinates": None},),
            stops=({"stop_id": "1", "kind": "bus", "coordinates": points[0]},
                   {"stop_id": "2", "kind": "bus", "coordinates": points[-1]}),
            demand=(), fleet=None, warnings=(),
        )
        feature = {"type": "Feature", "properties": {
            "a_gtfs_route_id": "42", "source_route_id": "42", "route_short_name": "구로01",
            "agency_id": "operator", "stop_ids": ["1", "2"], "geometry_source": "official",
            "source_namespace": "Seoul bus API", "source_version": "test"},
            "geometry": {"type": "LineString", "coordinates": points}}
        payload = json.dumps({"type": "FeatureCollection", "features": [feature],
                              "metadata": {"a_gtfs_snapshot_id": snapshot.snapshot_id}})
        with patch("busstrike.api.d_dashboard.load_reference", return_value=snapshot), \
             patch("busstrike.api.d_dashboard.TransitData", return_value=SimpleNamespace(root=Path.cwd())), \
             patch.object(Path, "read_text", return_value=payload):
            store = Store(transit_data=Path.cwd(), bus_geometry_file=Path("virtual.geojson"))
            self.assertEqual(store.sources()["bus_geometry"]["official_routes"], 1)
            lines = [item for item in map_geojson(store.real)["features"]
                     if item["geometry"]["type"] == "LineString"]
            self.assertEqual(lines[0]["geometry"]["coordinates"], points)


if __name__ == "__main__":
    unittest.main()
