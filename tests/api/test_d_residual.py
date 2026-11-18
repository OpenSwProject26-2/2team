"""Regional input demand is spatially mapped without exposing small time cells."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from busstrike.api.d_transit_data import TransitData


class ResidualDemandTests(unittest.TestCase):
    def test_variant_changes_residual_and_small_hour_cells_stay_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "transit.zip"
            nested = io.BytesIO()
            prefix = "guro_0610_v4/"
            with zipfile.ZipFile(nested, "w") as region:
                region.writestr(
                    prefix + "network/stops.csv",
                    "stop_id,stop_name,stop_lat,stop_lon,gu\n"
                    "s1,첫 정류장,37.5,126.9,구로구\n",
                )
                for variant, count in (("affected", 12), ("affected_maeul", 8)):
                    region.writestr(
                        prefix + f"bundles/{variant}_20261026/demand_bins.csv",
                        "aggregate_id,origin,destination,bin_start_s,bin_width_s,passenger_count\n"
                        f"a,s1,d1,21600,3600,{count}\n"
                        "b,s1,d2,25200,3600,3\n",
                    )
            with zipfile.ZipFile(root, "w") as outer:
                outer.writestr("manifest.json", "{}")
                outer.writestr("bundles_v4/region_bundles_0610_v4.zip", nested.getvalue())
            data = TransitData(root)
            try:
                a = data.residual("guro", "affected", "exp", "2026-10-26")
                b = data.residual("guro", "affected_maeul", "exp", "2026-10-26")
                self.assertEqual((a["total"], b["total"]), (15, 11))
                self.assertEqual((a["features"][0]["properties"]["passenger_count"],
                                  b["features"][0]["properties"]["passenger_count"]), (12, 8))
                self.assertEqual(a["features"][0]["properties"]["hours"], {"06": 12})
                self.assertEqual(a["suppressed_hour_cells"], 1)
                self.assertNotIn("aggregate_id", str(a))
            finally:
                data._region_bundle.close()
                data._zip.close()


if __name__ == "__main__":
    unittest.main()
