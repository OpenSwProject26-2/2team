import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from backend import strike_route_map as srm  # noqa: E402

A_GTFS = REPO / "tests/fixtures/routing/gtfs_preview/normal"


class StrikeRouteMapCliTests(unittest.TestCase):
    def test_table_and_summary_use_exact_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario = Path(tmp) / "scenario.json"
            scenario.write_text(json.dumps({"scenario_id": "s", "strike_route_ids": ["r1", "합성 정상 2"]}),
                                encoding="utf-8")
            out = Path(tmp) / "map.csv"
            summary = srm.build(scenario, A_GTFS, out)
            self.assertEqual(summary["matched"], 1)
            self.assertEqual(summary["unmatched_ids"], ["합성 정상 2"])
            with out.open(encoding="utf-8") as stream:
                table = list(csv.DictReader(stream))
            self.assertEqual([r["match"] for r in table], ["exact_id", "unmatched"])


if __name__ == "__main__":
    unittest.main()
