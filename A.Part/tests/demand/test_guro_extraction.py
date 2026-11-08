import csv
from contextlib import redirect_stderr
from copy import deepcopy
import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from data.extract_guro_draft import (ADMIN_FIELDS, VISIT_FIELDS, endpoint_class,
                                    evidence_matches, extract, internal_order,
                                    validate_saved_od)
from data.extract_guro_draft import main
from data.demand_analysis import AnalysisError


def source_visit(route, name, sequence, stop):
    row = {field: "synthetic" for field in VISIT_FIELDS}
    row.update(ROUTE_ID=route, ROUTE_NM=name, BSST_ORDER=str(sequence), BSST_ID=stop,
               BSST_SUM_DISTANCE=str(sequence * 100), STDR_DE="20261102", SERIAL_NUM=str(sequence))
    row.update(BSST_NM="Synthetic " + stop, lat="37.5", lon="127.0")
    row.update({field: "synthetic-area" for field in ADMIN_FIELDS})
    return row


class GuroExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.survey, self.output = (self.root / name for name in ("input", "survey", "output"))
        self.source.mkdir()
        self.survey.mkdir()
        self.visits = [source_visit("r1", "selected-one", i, stop)
                       for i, stop in enumerate(("A", "B", "C", "X", "Y", "A"), 1)]
        self.visits.append({**self.visits[1], "TRCM_ID": "other-synthetic-company"})
        self.visits.extend([source_visit("r1", "other-group", 1, "C"), source_visit("r1", "other-group", 2, "A")])
        self.visits.extend([source_visit("r2", "selected-two", i, stop)
                            for i, stop in enumerate(("A", "B", "C", "X", "Y"), 10)])
        self.od = [dict(ROUTE_ID=rid, RIDE_BSST_ID=origin, ALGH_BSST_ID=destination, RIDE_DTM_H=hour, PSNG_NUM=str(n))
                   for rid, origin, destination, hour, n in (
                       ("r1", "A", "B", "07", 3), ("r2", "B", "A", "08", 2),
                       ("r1", "A", "A", "07", 1), ("r1", "A", "X", "08", 4),
                       ("r2", "X", "C", "07", 5), ("r2", "X", "Y", "08", 6))]
        self.definition = {"recommendation": "southwest", "observation_date": "20261102",
            "validity_assumption": "inclusive boundaries; synthetic example only",
            "candidates": [{"candidate": "southwest", "route_ids": ["r1", "r2"], "bundle_morning_passengers": 21,
                "all_routes_shared_source_run_through_hub": {"stop_ids": ["A", "B", "C"], "evidence": [
                    {"route_id": "r1", "matches": [{"route_name": "selected-one", "sequences": [1, 2, 3]}]},
                    {"route_id": "r2", "matches": [{"route_name": "selected-two", "sequences": [10, 11, 12]}]}]}}]}

    def write_sources(self):
        for filename, rows in (("route_stops.csv", self.visits), ("bus_od_0709.csv", self.od)):
            with (self.source / filename).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        (self.survey / "survey.json").write_text(json.dumps(self.definition), encoding="utf-8")
        (self.survey / "report.md").write_text("Synthetic selection evidence", encoding="utf-8")

    def run_extract(self):
        self.write_sources()
        return extract(self.source, self.survey, self.output)

    def read_csv(self, name):
        with (self.output / name).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def write_output_csv(self, name, rows):
        with (self.output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            from data.extract_guro_draft import OD_FIELDS
            writer = csv.DictWriter(stream, OD_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def validate_files(self, report):
        corridor = json.loads((self.output / "corridor.json").read_text(encoding="utf-8"))
        references = {}
        for row in self.read_csv("route_visits.csv"):
            references.setdefault(row["ROUTE_ID"], set()).add(row["BSST_ID"])
        return validate_saved_od(self.output, report, corridor, references)

    def test_endpoint_boundaries_are_exclusive_and_ids_not_merged(self):
        self.assertEqual(endpoint_class("A", "B", ["A", "B"]), "both_inside")
        self.assertEqual(endpoint_class("A", "X", ["A", "B"]), "boarding_only_inside")
        self.assertEqual(endpoint_class("X", "B", ["A", "B"]), "alighting_only_inside")
        self.assertEqual(endpoint_class("A ", "X", ["A", "B"]), "both_outside")

    def test_source_order_forward_reverse_same_and_unknown(self):
        self.assertEqual(internal_order("A", "C", ["A", "B", "C"]), "forward")
        self.assertEqual(internal_order("C", "A", ["A", "B", "C"]), "reverse")
        self.assertEqual(internal_order("A", "A", ["A", "B", "A"]), "same_stop")
        self.assertEqual(internal_order("A", "B", ["A", "B", "A"]), "undetermined")
        self.assertEqual(internal_order("X", "B", ["A", "B"]), "undetermined")

    def test_evidence_does_not_fill_sequence_gap_or_conflicting_stop(self):
        rows = [source_visit("r", "n", 1, "A"), source_visit("r", "n", 3, "B")]
        self.assertFalse(evidence_matches(rows, ["A", "B"], [1, 3]))
        rows.append(source_visit("r", "n", 1, "X"))
        self.assertFalse(evidence_matches(rows, ["A", "B"], [1, 2]))

    def test_weighted_conservation_forward_subset_and_hour_preservation(self):
        result = self.run_extract()
        self.assertEqual(result["total_morning"], {"rows": 6, "passengers": 21})
        self.assertEqual({k: v["passengers"] for k, v in result["endpoint_classes"].items()},
                         {"both_inside": 6, "boarding_only_inside": 4, "alighting_only_inside": 5, "both_outside": 6})
        self.assertEqual({k: v["passengers"] for k, v in result["internal_source_order_classes"].items()},
                         {"forward": 3, "reverse": 2, "same_stop": 1, "undetermined": 0})
        self.assertEqual(result["included_forward_internal"], {"rows": 1, "passengers": 3})
        self.assertEqual(result["separated_from_forward_internal"]["passengers"], 18)
        self.assertTrue(result["all_checks_passed"])
        full = self.read_csv("morning_od.csv")
        self.assertEqual([(r["ROUTE_ID"], r["RIDE_DTM_H"], r["PSNG_NUM"]) for r in full],
                         [(r["ROUTE_ID"], r["RIDE_DTM_H"], r["PSNG_NUM"]) for r in self.od])
        self.assertNotIn("arrival_s", full[0])
        forward = self.read_csv("internal_forward_od.csv")
        self.assertEqual(sum(int(r["PSNG_NUM"]) for r in forward), 3)

    def test_duplicate_other_group_and_repeated_visits_preserved(self):
        result = self.run_extract()
        saved = self.read_csv("route_visits.csv")
        self.assertEqual(len(saved), len(self.visits))
        self.assertEqual([r["BSST_ORDER"] for r in saved], [r["BSST_ORDER"] for r in self.visits])
        self.assertEqual(result["other_source_groups_preserved"], 1)
        self.assertEqual(sum(r["group_role"] == "other_source_group_preserved" for r in saved), 2)
        reports = json.loads((self.output / "route_groups.json").read_text(encoding="utf-8"))
        group = next(r for r in reports if r["source_route_name"] == "selected-one")
        self.assertEqual(group["duplicate_sequence_groups"], 1)
        self.assertEqual(group["repeated_stop_ids_at_different_sequences"], 1)

    def test_stop_candidates_are_one_per_id_and_preserve_ambiguity(self):
        self.visits.append({**self.visits[1], "BSST_NM": "Alternative B", "lat": "37.6", "SIDO_NM": "other-area"})
        result = self.run_extract()
        stops = json.loads((self.output / "stops.json").read_text(encoding="utf-8"))["stops"]
        self.assertEqual(len(stops), len({r["stop_id"] for r in stops}))
        b = next(r for r in stops if r["stop_id"] == "B")
        self.assertEqual(b["stop_name_status"], "ambiguous")
        self.assertEqual(b["administrative_status"], "ambiguous")
        self.assertIsNone(b["lat"])
        self.assertEqual(result["total_morning"]["passengers"], 21)

    def test_missing_source_reference_is_reported_and_evidence_not_faked(self):
        self.visits[1]["BSST_ID"] = "Z"
        # The duplicate B at sequence 2 now conflicts with Z; no inferred edge.
        result = self.run_extract()
        self.assertFalse(result["all_checks_passed"])
        self.assertFalse(result["checks"]["source_corridor_evidence_verified"])
        self.assertEqual(result["internal_source_order_classes"]["undetermined"]["passengers"], 3)
        self.assertEqual(result["internal_source_order_classes"]["same_stop"]["passengers"], 1)
        self.assertEqual(self.read_csv("internal_forward_od.csv"), [])
        corridor = json.loads((self.output / "corridor.json").read_text(encoding="utf-8"))
        for edge in corridor["adjacent_pairs"]:
            self.assertIsNone(edge["travel_time_s"])
            self.assertIsNone(edge["distance_m"])
            self.assertIsNone(edge["return_feasibility"])

    def test_unknown_endpoint_reference_is_not_discarded(self):
        self.od[-1]["RIDE_BSST_ID"] = "MISSING_SYNTHETIC_STOP"
        result = self.run_extract()
        self.assertEqual(result["total_morning"]["passengers"], 21)
        self.assertEqual(result["reference_failures"]["od_route_stop_failure_passengers"], 6)
        self.assertIn("MISSING_SYNTHETIC_STOP", result["reference_failures"]["stop_metadata_missing_ids"])
        self.assertFalse(result["all_checks_passed"])

    def test_unselected_route_is_not_added_and_sources_unchanged(self):
        self.od.append({**self.od[0], "ROUTE_ID": "unselected", "PSNG_NUM": "100"})
        self.write_sources()
        before = {p: p.read_bytes() for directory in (self.source, self.survey) for p in directory.iterdir()}
        result = extract(self.source, self.survey, self.output)
        self.assertEqual(result["total_morning"]["passengers"], 21)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))
        with self.assertRaises(ValueError):
            extract(self.source, self.survey, self.output)

    def test_saved_validation_accepts_exact_subset_in_different_row_order(self):
        self.od.append({**self.od[0], "ROUTE_ID": "r2", "ALGH_BSST_ID": "C"})
        self.definition["candidates"][0]["bundle_morning_passengers"] += 3
        report = self.run_extract()
        self.write_output_csv("internal_forward_od.csv", list(reversed(self.read_csv("internal_forward_od.csv"))))
        result = self.validate_files(report)
        self.assertTrue(result["all_checks_passed"])
        self.assertEqual(result["measured_totals"]["forward"], {"rows": 2, "passengers": 6})
        self.assertTrue(report["checks"]["included_separated_preserved"])

    def test_saved_validation_detects_missing_forward_row(self):
        report = self.run_extract()
        self.write_output_csv("internal_forward_od.csv", [])
        result = self.validate_files(report)
        self.assertFalse(result["all_checks_passed"])
        self.assertEqual(result["missing_forward_rows"], 1)
        self.assertFalse(result["checks"]["forward_totals_match_report"])

    def test_saved_validation_detects_duplicate_forward_row(self):
        report = self.run_extract()
        rows = self.read_csv("internal_forward_od.csv")
        self.write_output_csv("internal_forward_od.csv", rows + rows)
        result = self.validate_files(report)
        self.assertFalse(result["all_checks_passed"])
        self.assertEqual(result["unexpected_forward_rows"], 1)
        self.assertEqual(result["duplicate_source_rows"]["forward"], 1)

    def test_saved_validation_detects_omission_and_duplicate_with_equal_totals(self):
        self.od.append({**self.od[0], "ROUTE_ID": "r2", "ALGH_BSST_ID": "C"})
        self.definition["candidates"][0]["bundle_morning_passengers"] += 3
        report = self.run_extract()
        first = self.read_csv("internal_forward_od.csv")[0]
        self.write_output_csv("internal_forward_od.csv", [first, first])
        result = self.validate_files(report)
        self.assertTrue(result["checks"]["forward_totals_match_report"])
        self.assertFalse(result["checks"]["forward_multiset_matches_full_subset"])
        self.assertEqual((result["missing_forward_rows"], result["unexpected_forward_rows"]), (1, 1))

    def test_saved_validation_detects_wrong_inclusion_even_with_same_weight(self):
        self.od[1]["PSNG_NUM"] = "3"
        self.definition["candidates"][0]["bundle_morning_passengers"] += 1
        report = self.run_extract()
        reverse = next(r for r in self.read_csv("morning_od.csv") if r["internal_source_order"] == "reverse")
        reverse["internal_source_order"] = "forward"
        self.write_output_csv("internal_forward_od.csv", [reverse])
        result = self.validate_files(report)
        self.assertTrue(result["checks"]["forward_totals_match_report"])
        self.assertFalse(result["checks"]["forward_rows_satisfy_conditions"])
        self.assertEqual((result["missing_forward_rows"], result["unexpected_forward_rows"]), (1, 1))

    def test_saved_validation_recomputes_condition_instead_of_trusting_both_files(self):
        report = self.run_extract()
        rows = self.read_csv("morning_od.csv")
        forward = next(r for r in rows if r["internal_source_order"] == "forward")
        forward["RIDE_BSST_ID"] = "X"
        self.write_output_csv("morning_od.csv", rows)
        self.write_output_csv("internal_forward_od.csv", [forward])
        result = self.validate_files(report)
        self.assertTrue(result["checks"]["morning_totals_match_report"])
        self.assertTrue(result["checks"]["forward_totals_match_report"])
        self.assertFalse(result["checks"]["morning_classifications_valid"])
        self.assertFalse(result["checks"]["forward_rows_satisfy_conditions"])
        self.assertFalse(result["all_checks_passed"])

    def test_saved_validation_detects_missing_zero_weight_full_row(self):
        self.od.append({**self.od[-1], "PSNG_NUM": "0"})
        report = self.run_extract()
        full = self.read_csv("morning_od.csv")
        self.write_output_csv("morning_od.csv", full[:-1])
        result = self.validate_files(report)
        self.assertEqual(result["measured_totals"]["morning"]["passengers"], report["total_morning"]["passengers"])
        self.assertFalse(result["checks"]["morning_totals_match_report"])
        self.assertFalse(result["checks"]["separated_totals_match_report"])

    def test_saved_validation_detects_duplicate_full_source_row(self):
        self.od.append({**self.od[-1], "PSNG_NUM": "0"})
        report = self.run_extract()
        full = self.read_csv("morning_od.csv")
        self.write_output_csv("morning_od.csv", full + [full[-1]])
        result = self.validate_files(report)
        self.assertEqual(result["duplicate_source_rows"]["morning"], 1)
        self.assertFalse(result["checks"]["morning_source_row_ids_unique"])
        self.assertFalse(result["all_checks_passed"])

    def test_extraction_rejects_bad_sequence_before_creating_output(self):
        self.visits[0]["BSST_ORDER"] = "SYNTHETIC_SECRET"
        with self.assertRaises(AnalysisError) as caught:
            self.run_extract()
        self.assertEqual(str(caught.exception), "route_stops.csv:2:BSST_ORDER")
        self.assertFalse(self.output.exists())

    def test_incomplete_selection_cannot_pass_vacuous_reference_checks(self):
        original = deepcopy(self.definition)
        for field, value in (("route_ids", []), ("stop_ids", []), ("evidence", []), ("sequences", ["1", "2", "3"])):
            with self.subTest(field=field):
                self.definition = deepcopy(original)
                candidate = self.definition["candidates"][0]
                common = candidate["all_routes_shared_source_run_through_hub"]
                if field == "route_ids":
                    candidate[field] = value
                elif field == "sequences":
                    common["evidence"][0]["matches"][0][field] = value
                else:
                    common[field] = value
                with self.assertRaises(AnalysisError):
                    self.run_extract()
                self.assertFalse(self.output.exists())

    def test_extraction_cli_sanitizes_input_exception_details(self):
        output = io.StringIO()
        with patch("sys.argv", ["extract_guro_draft"]), patch("data.extract_guro_draft.extract", side_effect=ValueError("SYNTHETIC_SECRET")):
            with redirect_stderr(output), self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("ValueError", output.getvalue())
        self.assertNotIn("SYNTHETIC_SECRET", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_failure_closes_partial_output_files_and_does_not_publish_report(self):
        self.od[-1]["RIDE_DTM_H"] = "09"
        with self.assertRaises(ValueError):
            self.run_extract()
        self.assertFalse((self.output / "validation_report.json").exists())
        # On Windows an open output handle prevents renaming the partial file.
        for name in ("morning_od.csv", "internal_forward_od.csv", "route_visits.csv"):
            path = self.output / name
            path.rename(path.with_suffix(".closed.csv"))


if __name__ == "__main__":
    unittest.main()
