"""GBIS official route shapes stay separate from A GTFS route IDs."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import DataError, MapSnapshot
from busstrike.api.d_gbis import add_gbis_geometry


class GbisGeometryTests(unittest.TestCase):
    def test_adds_validated_route_without_overwriting_a(self):
        snapshot = MapSnapshot("A", "a_gtfs", "2026-11-02", None, None, None,
                               (), (), (), None, ())
        feature = {"type": "Feature", "geometry": {"type": "LineString", "coordinates":
                   [[127.0, 37.5], [127.001, 37.5], [127.002, 37.5]]},
                   "properties": {"gbis_route_id": "200000010", "geometry_source": "official",
                                  "route_short_name": "900", "agency": "operator", "stop_ids": ["1", "2"],
                                  "tpss_order_overlap": 1.0,
                                  "stops": [{"stop_id": "1", "name": "A", "coordinates": [127.0, 37.5]},
                                            {"stop_id": "2", "name": "B", "coordinates": [127.002, 37.5]}]}}
        data = {"type": "FeatureCollection", "features": [feature]}
        with patch.object(Path, "read_bytes", return_value=json.dumps(data).encode()):
            result = add_gbis_geometry(snapshot, Path("gbis.geojson"))
        self.assertEqual(result.routes[0]["route_id"], "gbis:200000010")
        self.assertEqual(result.routes[0]["kind"], "gyeonggi")
        self.assertEqual(result.stops[0]["stop_id"], "gbis:1")
        self.assertTrue(result.snapshot_id.startswith("A-gbis-"))

        feature["properties"]["tpss_order_overlap"] = .4
        with patch.object(Path, "read_bytes", return_value=json.dumps(data).encode()):
            with self.assertRaises(DataError):
                add_gbis_geometry(snapshot, Path("gbis.geojson"))


if __name__ == "__main__":
    unittest.main()
