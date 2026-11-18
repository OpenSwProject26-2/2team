"""Validation gates for external bus road geometry."""

import copy
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from busstrike.api.d_bus_official import _items, _ordered_subsequence, fetch_official_route
from busstrike.api.d_bus_routing import validate_osrm_result
from busstrike.api.d_geo import DataError


class BusRoutingTests(unittest.TestCase):
    def setUp(self):
        self.calls = [[126.88, 37.49], [126.89, 37.49], [126.90, 37.49]]
        self.route = {"route_id": "116900013"}
        self.data = {"code": "Ok", "waypoints": [{"distance": 10}] * 3,
                     "routes": [{"geometry": {"type": "LineString", "coordinates": [
                         self.calls[0], [126.885, 37.4902], self.calls[1],
                         [126.895, 37.4902], self.calls[2]]},
                         "distance": 1900, "legs": [{"distance": 950}, {"distance": 950}]}]}

    def test_osrm_accepts_ordered_road_bend(self):
        result = validate_osrm_result(self.data, self.route, self.calls)
        self.assertEqual(len(result["points"]), 5)

    def test_osrm_rejects_far_snap_and_detour(self):
        far = copy.deepcopy(self.data)
        far["waypoints"][1]["distance"] = 150
        with self.assertRaises(DataError):
            validate_osrm_result(far, self.route, self.calls)
        detour = copy.deepcopy(self.data)
        detour["routes"][0]["legs"][1]["distance"] = 9000
        with self.assertRaises(DataError):
            validate_osrm_result(detour, self.route, self.calls)

    def test_official_xml_items_and_missing_key(self):
        root = ET.fromstring("<ServiceResult><msgBody><itemList><no>1</no><gpsX>126.9</gpsX></itemList></msgBody></ServiceResult>")
        self.assertEqual(_items(root), [{"no": "1", "gpsX": "126.9"}])
        self.assertTrue(_ordered_subsequence(["1", "2", "3"], ["1", "3"]))
        with patch.dict(os.environ, {"SEOUL_BUS_API_KEY": ""}):
            with self.assertRaisesRegex(DataError, "SEOUL_BUS_API_KEY"):
                fetch_official_route({"route_id": "116900013", "representative_stop_ids": ["1", "2"]},
                                     {"1": {"coordinates": self.calls[0]}, "2": {"coordinates": self.calls[1]}})


if __name__ == "__main__":
    unittest.main()
