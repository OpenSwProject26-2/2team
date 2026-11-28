"""Bus-only A feeds can show reference station locations without inventing rail trips."""

import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import MapSnapshot
from busstrike.api.d_reference import add_reference_metro_stations


class ReferenceMetroTests(unittest.TestCase):
    def test_adds_station_codes_but_no_rail_route(self):
        snapshot = MapSnapshot(
            snapshot_id="A", source="a_gtfs", service_date="2026-11-02",
            scenario_id=None, plan_id=None, revision=None, routes=(), stops=(),
            demand=(), fleet=None, warnings=(),
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "seoul_routegen_inputs_0610_v4/network/stations.csv",
                "stop_id,stop_name,stop_lat,stop_lon\n0231,시험역,37.495,126.887\n",
            )
        buffer.seek(0)
        with patch.object(Path, "is_file", return_value=True), \
                patch("busstrike.api.d_reference.zipfile.ZipFile", return_value=zipfile.ZipFile(buffer)):
            enriched = add_reference_metro_stations(snapshot, Path("reference-data"))
        self.assertEqual(enriched.routes, ())
        self.assertEqual(enriched.stops[0]["stop_id"], "0231")
        self.assertEqual(enriched.stops[0]["kind"], "metro")
        self.assertEqual(enriched.snapshot_id, "A")


if __name__ == "__main__":
    unittest.main()
