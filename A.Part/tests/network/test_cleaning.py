from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data.clean_network import (build_network, clean, load_visits, segment_distance,
                                settings, validate_network)
from data.demand_analysis import AnalysisError
from data.extract_guro_draft import digest


PART = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures/synthetic"


class NetworkCleaningTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_visits(FIXTURE / "route_visits.csv")
        self.stops = json.loads((FIXTURE / "stops.json").read_text(encoding="utf-8"))
        self.config = settings(json.loads((PART / "data/examples/network_cleaning.synthetic.json").read_text(encoding="utf-8")))
        self.checksum = digest(FIXTURE / "route_visits.csv")["sha256"]

    def build(self):
        return build_network(self.rows, self.stops, self.config, self.checksum)

    def validate(self, network):
        return validate_network(network, self.rows, self.config, self.checksum, self.stops)

    def append(self, row):
        new = {**deepcopy(row), "input_csv_line": max(r["input_csv_line"] for r in self.rows) + 1}
        new["source_csv_line"] = str(new["input_csv_line"])
        self.rows.append(new)
        return new

    def test_separate_stop_visit_and_revisit_with_full_source_links(self):
        network = self.build()
        self.assertEqual(len(network["stops"]), 3)
        self.assertEqual(len(network["visits"]), 4)
        self.assertEqual(len(network["segments"]), 3)
        a_visits = [v for v in network["visits"] if v["stop_id"] == "stop-A"]
        self.assertEqual(len({v["visit_id"] for v in a_visits}), 2)
        self.assertEqual(network["groups"][0]["revisited_stop_ids"], ["stop-A"])
        self.assertEqual([s["distance_m"] for s in network["segments"]], [100, 150, 150])
        self.assertTrue(self.validate(network)["all_checks_passed"])

    def test_identical_variant_duplicates_and_company_variants_are_preserved(self):
        self.append({**self.rows[0], "SERIAL_NUM": "new-processing-serial"})
        self.append({**self.rows[0], "TRCM_ID": "synthetic-operator-B", "TRCM_NM": "Other synthetic operator"})
        network = self.build()
        visit = next(v for v in network["visits"] if v["source_sequence"] == 1)
        self.assertEqual(visit["source_row_count"], 3)
        self.assertEqual(visit["source_variant_count"], 2)
        self.assertEqual(network["groups"][0]["same_visit_excess_rows"], 2)
        self.assertEqual(network["groups"][0]["identical_variant_excess_rows"], 1)
        self.assertEqual(len(network["segments"][0]["origin_source_row_ids"]), 3)
        self.assertEqual(len(network["visits"]), 4)
        self.assertTrue(self.validate(network)["all_checks_passed"])

    def test_conflicting_sequence_is_not_an_identical_visit_or_bridge(self):
        self.append({**self.rows[1], "BSST_ID": "stop-C"})
        network = self.build()
        self.assertEqual(network["groups"][0]["conflicting_sequence_positions"], 1)
        self.assertEqual(network["groups"][0]["same_visit_excess_rows"], 0)
        self.assertEqual([(s["origin_source_sequence"], s["destination_source_sequence"]) for s in network["segments"]], [(3, 4)])
        self.assertEqual(len(network["blocked_boundaries"]), 2)
        self.assertTrue(self.validate(network)["all_checks_passed"])

    def test_sequence_gap_is_not_skipped(self):
        self.rows = [r for r in self.rows if r["BSST_ORDER"] != "2"]
        network = self.build()
        self.assertEqual([(s["origin_source_sequence"], s["destination_source_sequence"]) for s in network["segments"]], [(3, 4)])
        self.assertEqual(network["blocked_boundaries"][0]["reasons"], ["sequence_gap"])

    def test_name_coordinate_ambiguity_has_no_arbitrary_representative(self):
        self.stops["stops"][0]["stop_name_candidates"].append("Alternative synthetic A")
        self.stops["stops"][0]["coordinate_candidates"].append([37.1, 127.1])
        self.stops["stops"][0]["coordinate_candidates"].append([999, 127])
        network = self.build()
        stop = next(s for s in network["stops"] if s["stop_id"] == "stop-A")
        self.assertIsNone(stop["name"])
        self.assertIsNone(stop["lat"])
        self.assertIsNone(stop["lon"])
        self.assertEqual(len(stop["coordinate_candidates"]), 2)
        self.assertEqual(stop["invalid_coordinate_candidates"], 1)
        self.assertIsNone(stop["crs"])
        self.assertTrue(self.validate(network)["all_checks_passed"])

    def test_bad_cumulative_values_are_not_replaced_by_zero(self):
        for value, expected in (("", "missing_cumulative"), ("bad", "invalid_cumulative"),
                                ("-1", "invalid_cumulative"), ("0", "non_increasing_cumulative")):
            with self.subTest(value=value):
                self.rows[1]["BSST_SUM_DISTANCE"] = value
                segment = self.build()["segments"][0]
                self.assertIsNone(segment["distance_m"])
                self.assertEqual(segment["distance_status"], expected)
                self.assertEqual(segment["destination_cumulative_raw"], [value])

    def test_decreasing_cumulative_distance_keeps_negative_delta_as_evidence(self):
        self.rows[0]["BSST_SUM_DISTANCE"] = "200"
        segment = self.build()["segments"][0]
        self.assertIsNone(segment["distance_m"])
        self.assertEqual(segment["source_delta_raw_units"], "-100")
        self.assertEqual(segment["distance_status"], "non_increasing_cumulative")

    def test_ambiguous_cumulative_source_variants_do_not_choose_one_value(self):
        self.append({**self.rows[1], "BSST_SUM_DISTANCE": "101"})
        network = self.build()
        first = network["segments"][0]
        self.assertEqual(first["destination_cumulative_raw"], ["100", "101"])
        self.assertIsNone(first["distance_m"])
        self.assertEqual(first["distance_status"], "ambiguous_cumulative")

    def test_units_without_evidence_are_explicitly_unknown(self):
        for unit, evidence in ((None, None), ("m", None), ("km", self.config["distance"]["unit_evidence"])):
            with self.subTest(unit=unit):
                self.config["distance"] = {"cumulative_unit": unit, "unit_evidence": evidence}
                segment = self.build()["segments"][0]
                self.assertIsNone(segment["distance_m"])
                self.assertEqual(segment["distance_status"], "unit_unverified")
                self.assertEqual(segment["source_delta_raw_units"], "100")

    def test_date_mismatch_and_cross_group_distance_are_not_accepted(self):
        self.rows[1]["STDR_DE"] = "20260102"
        network = self.build()
        self.assertEqual(network["segments"][0]["distance_status"], "source_date_ambiguous_or_mismatch")
        a, b = deepcopy(network["visits"][:2])
        b["source_group_id"] = "other-source-group"
        self.assertEqual(segment_distance(a, b, self.config["distance"])["distance_status"], "source_group_mismatch")

    def test_other_source_group_is_preserved_without_cross_group_edges(self):
        self.append({**self.rows[0], "ROUTE_NM": "Other source group"})
        self.append({**self.rows[1], "ROUTE_NM": "Other source group"})
        network = self.build()
        self.assertEqual(len(network["groups"]), 2)
        self.assertEqual(sum(v["group_role"] == "other_source_group_preserved" for v in network["visits"]), 2)
        by_id = {v["visit_id"]: v for v in network["visits"]}
        self.assertTrue(all(by_id[s["origin_visit_id"]]["source_group_id"] == by_id[s["destination_visit_id"]]["source_group_id"] for s in network["segments"]))
        self.assertTrue(all(g["reverse_direction_status"] == "not_extracted" for g in network["groups"]))

    def test_determinism_and_input_immutability(self):
        before = deepcopy((self.rows, self.stops, self.config))
        first = self.build()
        self.assertEqual((self.rows, self.stops, self.config), before)
        self.rows.reverse()
        self.stops["stops"].reverse()
        self.assertEqual(first, self.build())

    def test_saved_validator_detects_missing_source_and_tampered_visit(self):
        network = self.build()
        network["visits"][0]["source_variants"][0]["source_rows"].clear()
        result = self.validate(network)
        self.assertFalse(result["all_checks_passed"])
        self.assertEqual(result["missing_source_rows"], 1)
        network = self.build()
        network["visits"][0]["stop_id"] = "stop-B"
        self.assertFalse(self.validate(network)["checks"]["source_counts_consistent"])

    def test_saved_validator_detects_false_edge_and_invented_time(self):
        network = self.build()
        network["segments"][0]["destination_visit_id"] = "missing"
        self.assertFalse(self.validate(network)["all_checks_passed"])
        network = self.build()
        network["segments"][0]["travel_time_s"] = 0
        self.assertFalse(self.validate(network)["checks"]["unknown_conditions_not_fabricated"])
        network = self.build()
        network["stops"][0]["lat"] = 0
        self.assertFalse(self.validate(network)["checks"]["stop_set_exact_and_candidates_preserved"])

    def test_rejects_invalid_config_and_sequence_without_field_values(self):
        for field, value in (("route_ids", []), ("selected_groups", []), ("source_namespace", "")):
            with self.subTest(field=field), self.assertRaises(AnalysisError):
                settings({**self.config, field: value})
        with TemporaryDirectory() as directory:
            path = Path(directory) / "route_visits.csv"
            original = self.rows[0]
            row = {k: v for k, v in original.items() if k != "input_csv_line"}
            row["BSST_ORDER"] = "SYNTHETIC_SECRET"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, list(row))
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaises(AnalysisError) as caught:
                load_visits(path)
            self.assertEqual(str(caught.exception), "route_visits.csv:2:BSST_ORDER")

    def test_clean_persists_four_files_reloads_and_protects_existing_outputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config = {**self.config, "input_dir": str(FIXTURE), "output_dir": str(root / "output")}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            before = {p: p.read_bytes() for p in FIXTURE.iterdir()}
            report = clean(config_path)
            self.assertTrue(report["saved_file_validation"]["all_checks_passed"])
            self.assertEqual({p.name for p in (root / "output").iterdir()}, {"stops.json", "visits.json", "segments.json", "quality_report.json"})
            self.assertEqual(before, {p: p.read_bytes() for p in FIXTURE.iterdir()})
            with self.assertRaises(AnalysisError):
                clean(config_path)
            config["output_dir"] = str(root / "second-output")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            clean(config_path)
            for filename in ("stops.json", "visits.json", "segments.json"):
                self.assertEqual((root / "output" / filename).read_bytes(), (root / "second-output" / filename).read_bytes())
            config["output_dir"] = str(FIXTURE)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(AnalysisError):
                clean(config_path)


if __name__ == "__main__":
    unittest.main()
