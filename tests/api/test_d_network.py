"""District crossing, clipping, and dataset safety for the operator map."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import MapSnapshot
from busstrike.api.d_network import clip_line, contains, districts, network_view
from busstrike.api.d_transit_data import TransitData


class NetworkTests(unittest.TestCase):
    def test_all_four_districts_have_real_polygons(self):
        for district in ("guro", "dongjak", "gangseo", "gangdong"):
            self.assertIn(district, districts())
            self.assertIn(
                districts()[district]["geometry"]["type"], {"Polygon", "MultiPolygon"}
            )

    def test_crossing_route_without_inside_endpoint_is_retained(self):
        square = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }
        self.assertFalse(contains([-1, 0.5], square))
        self.assertFalse(contains([2, 0.5], square))
        clipped = clip_line([[-1, 0.5], [2, 0.5]], square)
        self.assertEqual(len(clipped), 1)
        self.assertAlmostEqual(clipped[0][0][0], 0)
        self.assertAlmostEqual(clipped[0][-1][0], 1)

    def test_real_guro_network_filters_and_preserves_stop_order(self):
        polygon = districts()["guro"]["geometry"]
        point = [126.887, 37.495]
        self.assertTrue(contains(point, polygon))
        snapshot = MapSnapshot(
            snapshot_id="test",
            source="a_gtfs",
            service_date="2026-11-02",
            scenario_id=None,
            plan_id=None,
            revision=None,
            routes=(
                {
                    "route_id": "r1",
                    "short_name": "test",
                    "agency": "a",
                    "kind": "village",
                    "coordinates": None,
                    "representative_stop_ids": ["s1", "s2"],
                    "all_stop_ids": ["s1", "s2"],
                    "geometry_status": "unavailable",
                },
            ),
            stops=(
                {"stop_id": "s1", "name": "A", "kind": "bus", "coordinates": point},
                {
                    "stop_id": "s2",
                    "name": "B",
                    "kind": "bus",
                    "coordinates": [126.8, 37.49],
                },
            ),
            demand=(),
            fleet=None,
            warnings=(),
        )
        inside = network_view(snapshot, "guro", "inside")
        full = network_view(snapshot, "guro", "full")
        self.assertEqual(inside["routes"][0]["representative_stop_ids"], ["s1", "s2"])
        self.assertEqual(
            sum(f["geometry"]["type"] == "Point" for f in inside["map"]["features"]), 1
        )
        self.assertEqual(
            sum(f["geometry"]["type"] == "Point" for f in full["map"]["features"]), 2
        )

    def test_missing_dataset_does_not_read_private_tables(self):
        with self.assertRaises(FileNotFoundError):
            TransitData(Path("nonexistent_seoul_transit_data"))


if __name__ == "__main__":
    unittest.main()
