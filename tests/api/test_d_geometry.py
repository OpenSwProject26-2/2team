"""The GIS corridor must pass a GTFS coordinate check before display."""

import unittest

from busstrike.api.d_geo import MapSnapshot, map_geojson
from busstrike.api.d_geometry import bus_geometry_view, rail_geometry_view
from busstrike.api.d_network import network_view


class RailGeometryTests(unittest.TestCase):
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
        feature["properties"] = {**props, "stop_ids": ["3", "2", "1"]}
        self.assertIsNone(bus_geometry_view(snapshot, data).routes[0]["coordinates"])


if __name__ == "__main__":
    unittest.main()
