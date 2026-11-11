import csv
import sys
from types import ModuleType
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from data.demand_analysis import AnalysisError
from data.demand_bins import BIN_FIELDS, convert
from data.link_demand import run
from data.prepare_arrivals import (A_ROOT, POLICY, bin_values, inspect_b, parameters,
                                   pinned_b, prepare, read_prepared, validate_a_inputs)
from data.link_demand import canonical
from . import test_link_demand as linking_tests


class ArrivalPreparationUnitTests(unittest.TestCase):
    """No B installation, checkout or substitute generation algorithm required."""

    def fixture(self, root):
        fixture = linking_tests.DemandLinkTests()
        fixture.setUp()
        draft, network, _ = fixture.prepare(root)
        links, bins = root / "links", root / "bins"
        run(draft, network, links)
        convert(links, draft, network, bins)
        return bins, links, draft, network

    def test_policy_seed_and_service_date_must_be_explicit_and_valid(self):
        parameters(POLICY, "seed with spaces", "2026-11-02")
        for policy, seed, service in ((None, "s", "2026-11-02"), ("unsupported", "s", "2026-11-02"),
                                      (POLICY, None, "2026-11-02"), (POLICY, " ", "2026-11-02"),
                                      (POLICY, "s", None), (POLICY, "s", "20261102"),
                                      (POLICY, "s", "2026-02-30")):
            with self.subTest(policy=policy, seed=seed, service=service), self.assertRaises(AnalysisError):
                parameters(policy, seed, service)

    def test_six_columns_explicit_integers_and_id_strings(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "bins.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=BIN_FIELDS)
                writer.writeheader()
                writer.writerow(dict(zip(BIN_FIELDS, ("0001", "001", "002", "25200", "3600", "02"))))
            row, = bin_values(path)
            self.assertEqual((row["aggregate_id"], row["origin"], row["destination"]), ("0001", "001", "002"))
            self.assertEqual((row["bin_start_s"], row["bin_width_s"], row["passenger_count"]), (25200, 3600, 2))
            self.assertTrue(all(type(row[f]) is int for f in BIN_FIELDS[3:]))

    def test_invalid_integer_and_missing_or_extra_column_fail_without_values(self):
        for mode in ("-1", "+2", "2.0", " 2", "2 ", "٢", "", "missing", "extra"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                path = Path(folder) / "bins.csv"
                fields = BIN_FIELDS[:-1] if mode == "missing" else (*BIN_FIELDS, "extra") if mode == "extra" else BIN_FIELDS
                row = dict(zip(BIN_FIELDS, ("synthetic-sensitive", "001", "002", "25200", "3600", "2")))
                if mode not in ("missing", "extra"):
                    row["passenger_count"] = mode
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerow(row)
                with self.assertRaises(AnalysisError) as caught:
                    tuple(bin_values(path))
                self.assertNotIn("synthetic-sensitive", str(caught.exception))

    def test_a_linkage_scope_and_included_count_rechecked_before_b(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            bins, links, draft, network = self.fixture(root)
            report, _ = validate_a_inputs(bins, links, draft, network)
            self.assertEqual(report["summary"]["dispositions"]["included"]["passengers"], 2)
            path = bins / "demand_bins_included.csv"
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["passenger_count"] = "3"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=BIN_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            with patch("data.prepare_arrivals.pinned_b", side_effect=AssertionError("must not load B")) as b:
                with self.assertRaises(AnalysisError):
                    prepare(bins, links, draft, network, root / "bad", b_root=root / "B", b_commit="0" * 40,
                            policy=POLICY, seed="s", service_date="2026-11-02")
                b.assert_not_called()
            self.assertFalse((root / "bad").exists())

    def test_existing_output_and_input_directories_protected_before_b(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            bins, links, draft, network = self.fixture(root)
            for target in (bins, links / "nested", draft / "nested", network / "nested"):
                with self.subTest(target=target), self.assertRaises(AnalysisError):
                    prepare(bins, links, draft, network, target, b_root=root / "B", b_commit="0" * 40,
                            policy=POLICY, seed="s", service_date="2026-11-02")

    def test_fixed_checkout_sha_clean_status_and_api_file_hashes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sim").mkdir()
            for name in ("__init__.py", "arrivals.py", "models.py", "run_inputs.py"):
                (root / "sim" / name).write_text("# synthetic inspection fixture\n", encoding="utf-8")
            sha = "1" * 40

            def query(_root, *args):
                if args == ("rev-parse", "--show-toplevel"):
                    return str(root)
                if args == ("rev-parse", "HEAD"):
                    return sha
                return ""

            with patch("data.prepare_arrivals.git", side_effect=query):
                metadata = inspect_b(root, sha)
                self.assertTrue(metadata["working_tree_clean"])
                self.assertEqual(len(metadata["api_files"]), 4)
                with self.assertRaisesRegex(AnalysisError, "commit_mismatch"):
                    inspect_b(root, "2" * 40)
            with patch("data.prepare_arrivals.git", side_effect=lambda r, *a: " M changed" if a[0] == "status" else query(r, *a)):
                with self.assertRaisesRegex(AnalysisError, "working_tree_not_clean"):
                    inspect_b(root, sha)
        with self.assertRaises(AnalysisError):
            inspect_b(A_ROOT, "1" * 40)

    def test_read_rejects_untrusted_report_before_loading_b(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "report.json").write_text('{"all_checks_passed": false}', encoding="utf-8")
            with patch("data.prepare_arrivals.pinned_b", side_effect=AssertionError("must not load B")) as b:
                with self.assertRaises(AnalysisError):
                    read_prepared(root, b_root=root / "B", b_commit="0" * 40)
                b.assert_not_called()

    def test_foreign_sim_import_is_not_replaced(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            foreign = ModuleType("sim")
            foreign.__file__ = str(root.parent / ("foreign-" + root.name) / "sim/__init__.py")
            with patch("data.prepare_arrivals.inspect_b", return_value={"root": str(root)}), \
                    patch("data.prepare_arrivals._B_BINDING", None), patch.dict(sys.modules, {"sim": foreign}):
                with self.assertRaisesRegex(AnalysisError, "conflicting_sim_import"):
                    with pinned_b(root, "1" * 40):
                        self.fail("foreign sim must be rejected")
                self.assertIs(sys.modules["sim"], foreign)

    def test_cached_fingerprint_or_removed_modules_require_new_process(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = {"root": str(root), "actual_commit": "1" * 40, "api_files": []}
            for mode, reason in (("fingerprint", "process_binding_mismatch"),
                                 ("removed", "pinned_modules_removed_or_replaced")):
                binding = {"fingerprint": canonical({} if mode == "fingerprint" else metadata),
                           "modules": {"sim": ModuleType("sim")}, "types": ()}
                with self.subTest(mode=mode), patch("data.prepare_arrivals.inspect_b", return_value=metadata), \
                        patch("data.prepare_arrivals._B_BINDING", binding):
                    with self.assertRaisesRegex(AnalysisError, reason):
                        with pinned_b(root, "1" * 40):
                            self.fail("must not reload a live binding")


if __name__ == "__main__":
    unittest.main()
