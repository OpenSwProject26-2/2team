"""Explicit real-B synthetic checks; deliberately outside ordinary A discovery."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

PART = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART))

from data.demand_analysis import AnalysisError
from data.demand_bins import BIN_FIELDS, convert
from data.extract_guro_draft import digest
from data.link_demand import run
from data.prepare_arrivals import (POLICY, bin_values, inspect_b, pinned_b, prepare,
                                   read_prepared)
from tests.demand import test_link_demand as linking_tests


class RealBArrivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only synthetic fixture files. No guro CSV or real local data is read.
        cls.output_root.mkdir(parents=True, exist_ok=False)
        fixture = linking_tests.DemandLinkTests()
        fixture.setUp()
        fixture_root = cls.output_root / "synthetic_inputs"
        fixture_root.mkdir()
        cls.draft, cls.network, rows = fixture.prepare(fixture_root, two_routes=True)
        # Two unique included rows, one per synthetic route, in different hours.
        rows[4]["RIDE_DTM_H"] = "08"
        fixture.write_od(cls.draft, rows)
        cls.links, cls.bins = cls.output_root / "links", cls.output_root / "bins"
        run(cls.draft, cls.network, cls.links)
        convert(cls.links, cls.draft, cls.network, cls.bins)
        cls.before_inputs = {str(p): digest(p)["sha256"] for d in (fixture_root, cls.links, cls.bins)
                             for p in d.rglob("*") if p.is_file()}
        cls.before_b = inspect_b(cls.b_root, cls.b_commit)
        cls.baseline = cls.output_root / "arrivals"
        cls.report = cls.execute(cls.baseline)

    @classmethod
    def execute(cls, output, **overrides):
        args = {"b_root": cls.b_root, "b_commit": cls.b_commit, "policy": POLICY,
                "seed": "synthetic-arrivals-v1", "service_date": "2026-01-01"}
        args.update(overrides)
        return prepare(cls.bins, cls.links, cls.draft, cls.network, output, **args)

    def test_real_api_count_od_each_aggregate_and_half_open_bounds(self):
        realization, report = read_prepared(self.baseline, b_root=self.b_root, b_commit=self.b_commit)
        self.assertEqual(realization.total, 4)
        self.assertEqual(sum(r.passenger_count for r in realization.rows), realization.total)
        self.assertEqual({r.bin_start_s for r in realization.rows}, {25200, 28800})
        lookup = {r.aggregate_id: r for r in realization.rows}
        counts = Counter()
        for passenger in realization.passengers:
            aggregate_id, _ = json.loads(passenger.passenger_id)
            row = lookup[aggregate_id]
            self.assertEqual((passenger.origin, passenger.destination), (row.origin, row.destination))
            self.assertTrue(row.bin_start_s <= passenger.time_s < row.bin_start_s + row.bin_width_s)
            counts[aggregate_id] += 1
        self.assertEqual(counts, {r.aggregate_id: r.passenger_count for r in realization.rows})
        self.assertTrue(report["all_checks_passed"])
        self.assertFalse(report["b_changes_detected"])

    def test_saved_read_uses_all_b_validation_without_generating_again(self):
        with pinned_b(self.b_root, self.b_commit) as (api, _):
            with patch.object(api, "generate", side_effect=AssertionError("read must not regenerate")):
                loaded, _ = read_prepared(self.baseline, b_root=self.b_root, b_commit=self.b_commit,
                                          expected_report_sha256=digest(self.baseline / "report.json")["sha256"])
                self.assertEqual(loaded.checksum, self.report["realization_checksum"])

    def test_fresh_process_read_then_b_validation_and_c_demands_without_generate(self):
        command = [sys.executable, "-B", str(Path(__file__).with_name("arrival_reuse_check.py")),
                   "--prepared-dir", str(self.baseline.resolve()), "--b-root", str(self.b_root.resolve()),
                   "--b-commit", self.b_commit]
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertTrue(outcome["fresh_process"])
        self.assertTrue(outcome["b_validation_passed"])
        self.assertEqual(outcome["c_demands_passengers"], self.report["generated_passengers"])
        self.assertEqual((outcome["generate_calls"], outcome["simulation_calls"]), (0, 0))

    def test_same_input_policy_seed_reproducible(self):
        repeated = self.output_root / "arrivals_repeat"
        report = self.execute(repeated)
        self.assertEqual(report["realization_checksum"], self.report["realization_checksum"])
        self.assertEqual((repeated / "realization.jsonl").read_bytes(),
                         (self.baseline / "realization.jsonl").read_bytes())

    def test_real_b_rejects_duplicate_id_same_stop_and_invalid_integer(self):
        with pinned_b(self.b_root, self.b_commit) as (api, _):
            source = tuple(bin_values(self.bins / "demand_bins_included.csv"))
            for mode in ("duplicate", "same_stop", "negative", "not_integer"):
                values = [dict(v) for v in source]
                if mode == "duplicate":
                    values.append(dict(values[0]))
                elif mode == "same_stop":
                    values[0]["destination"] = values[0]["origin"]
                else:
                    values[0]["passenger_count"] = -1 if mode == "negative" else "2"
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    api.check_rows(tuple(api.BinRow(**v) for v in values))

    def test_bin_connection_mismatch_rejected_before_generation(self):
        with TemporaryDirectory() as folder:
            bins = Path(folder) / "bins"
            shutil.copytree(self.bins, bins)
            path = bins / "demand_bins_included.csv"
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["origin"] = "synthetic-wrong-origin"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=BIN_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            output = Path(folder) / "bad"
            with self.assertRaises(AnalysisError):
                prepare(bins, self.links, self.draft, self.network, output, b_root=self.b_root,
                        b_commit=self.b_commit, policy=POLICY, seed="s", service_date="2026-01-01")
            self.assertFalse(output.exists())

    def test_stored_payload_and_route_mapping_tampering_rejected(self):
        for name in ("realization.jsonl", "aggregate_routes.jsonl"):
            with self.subTest(name=name), TemporaryDirectory() as folder:
                target = Path(folder) / "changed"
                shutil.copytree(self.baseline, target)
                path = target / name
                path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                with self.assertRaises(AnalysisError):
                    read_prepared(target, b_root=self.b_root, b_commit=self.b_commit)

    def test_original_route_and_source_rows_preserved(self):
        with (self.baseline / "aggregate_routes.jsonl").open(encoding="utf-8") as stream:
            mappings = [json.loads(line) for line in stream]
        self.assertEqual({m["original_route_id"] for m in mappings}, {"route-A", "route-B"})
        self.assertTrue(all(m["original_route_id"] == m["source"]["ROUTE_ID"] for m in mappings))
        self.assertTrue(all(m["source"]["source_csv_line"] and m["source_candidates"][0]["source_row_ids"]
                            for m in mappings))

    def test_wrong_commit_policy_or_service_date_refused(self):
        for args in ({"b_commit": "0" * 40}, {"policy": "unsupported"}, {"service_date": "2026-02-30"}):
            with self.subTest(args=args), TemporaryDirectory() as folder, self.assertRaises(AnalysisError):
                self.execute(Path(folder) / "bad", **args)

    def test_inputs_b_checkout_and_existing_output_preserved(self):
        self.assertEqual(self.before_b, inspect_b(self.b_root, self.b_commit))
        self.assertTrue(all(digest(Path(p))["sha256"] == sha for p, sha in self.before_inputs.items()))
        before = (self.baseline / "realization.jsonl").read_bytes()
        for target in (self.baseline, self.bins / "nested", self.links / "nested"):
            with self.subTest(target=target), self.assertRaises(AnalysisError):
                self.execute(target)
        self.assertEqual(before, (self.baseline / "realization.jsonl").read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--b-root", type=Path, required=True)
    parser.add_argument("--b-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    RealBArrivalTests.b_root, RealBArrivalTests.b_commit = args.b_root, args.b_commit
    RealBArrivalTests.output_root = args.output_dir
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealBArrivalTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.output_dir.is_dir():
        (args.output_dir / "integration_result.json").write_text(json.dumps({
            "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "success": result.wasSuccessful(), "b_commit": args.b_commit,
            "synthetic_only": True, "real_guro_generation": False}, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
