from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data.demand_analysis import AnalysisError
from data.demand_bins import BIN_FIELDS, convert, make_bin, validate_bins
from data.link_demand import run
from . import test_link_demand as linking_tests


class DemandBinTests(unittest.TestCase):
    def setUp(self):
        self.fixture = linking_tests.DemandLinkTests()
        self.fixture.setUp()

    def prepare(self, root):
        draft, network, _ = self.fixture.prepare(root)
        links = root / "links"
        report = run(draft, network, links)
        self.assertTrue(report["all_checks_passed"])
        return draft, network, links

    def test_exact_bins_integer_weights_zero_and_original_id_preserved(self):
        for hour, start in (("07", 25200), ("08", 28800)):
            row = self.fixture.od(hour=hour, count="02")
            record = self.fixture.record(row)
            original = deepcopy(record)
            row = make_bin(record)
            self.assertEqual(tuple(row), BIN_FIELDS)
            self.assertEqual((row["bin_start_s"], row["bin_width_s"], row["passenger_count"]), (start, 3600, 2))
            self.assertIs(type(row["passenger_count"]), int)
            self.assertEqual(row["aggregate_id"], record["aggregate_id"])
            self.assertEqual(record, original)
            self.assertNotIn("arrival_s", row)
        record = self.fixture.record(self.fixture.od(count="0"))
        self.assertEqual(make_bin(record)["passenger_count"], 0)
        record["source"]["RIDE_BSST_ID"] = "0001"
        self.assertEqual(make_bin(record)["origin"], "0001")
        record["source"]["RIDE_DTM_H"] = "09"
        with self.assertRaises(AnalysisError):
            make_bin(record)

    def test_conversion_counts_identity_and_no_engine_or_arrivals(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            draft, network, links = self.prepare(root)
            self.assertFalse((links / "demand_bins_all.csv").exists())
            before = {f.name: f.read_bytes() for f in links.iterdir()}
            output = root / "bins"
            report = convert(links, draft, network, output)
            self.assertTrue(report["all_checks_passed"])
            self.assertEqual(report["saved_validation"]["counts"],
                             {"demand_bins_all.csv": {"rows": 4, "passengers": 14},
                              "demand_bins_included.csv": {"rows": 1, "passengers": 2}})
            self.assertFalse(report["arrival_s_generated"])
            self.assertFalse(report["engine_executed"])
            self.assertFalse(report["b_check_rows_executed_by_this_tool"])
            self.assertEqual(before, {f.name: f.read_bytes() for f in links.iterdir()})
            for target in (output, links / "nested", draft / "nested", network / "nested"):
                with self.assertRaises(AnalysisError):
                    convert(links, draft, network, target)

    def test_missing_duplicate_wrong_inclusion_wrong_count_and_identity_fail(self):
        for mode in ("missing", "duplicate", "wrong_inclusion", "count", "id", "all_file_count"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network, links = self.prepare(root)
                output = root / "bins"
                report = convert(links, draft, network, output)
                filename = "demand_bins_all.csv" if mode == "all_file_count" else "demand_bins_included.csv"
                path = output / filename
                with path.open(encoding="utf-8", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                if mode == "missing":
                    rows.pop()
                elif mode == "duplicate":
                    rows.append(deepcopy(rows[0]))
                elif mode == "wrong_inclusion":
                    rows[0]["origin"] = "stop-C"
                elif mode == "id":
                    rows[0]["aggregate_id"] = "wrong"
                else:
                    rows[0]["passenger_count"] = "3"
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=BIN_FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
                self.assertFalse(validate_bins(links, output, report["summary"])["all_checks_passed"])

    def test_changed_links_or_input_and_failed_core_report_are_rejected(self):
        for mode in ("links", "input", "failed_report"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network, links = self.prepare(root)
                if mode == "links":
                    path = links / "od_links.jsonl"
                    rows = path.read_text(encoding="utf-8").splitlines()
                    path.write_text("\n".join(rows[:-1]), encoding="utf-8")
                elif mode == "input":
                    with (draft / "morning_od.csv").open("a", encoding="utf-8") as stream:
                        stream.write("\n")  # Even harmless byte changes invalidate declared inputs.
                else:
                    path = links / "report.json"
                    report = json.loads(path.read_text(encoding="utf-8"))
                    report["all_checks_passed"] = False
                    path.write_text(json.dumps(report), encoding="utf-8")
                output = root / "bad"
                with self.assertRaises(AnalysisError):
                    convert(links, draft, network, output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
