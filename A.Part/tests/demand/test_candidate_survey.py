import csv
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from data.candidate_survey import (forward_pairs, group_quality, indexed_pairs,
                                   ordered_runs, position_index, region_summary,
                                   shared_run, survey, validity, visit_groups)
from data.candidate_survey import main, visit_source_rows
from data.demand_analysis import AnalysisError


def visit(route, sequence, stop, name="synthetic", region="서울특별시"):
    return {"ROUTE_ID": route, "ROUTE_NM": name, "BSST_ORDER": str(sequence),
            "BSST_ID": stop, "SIDO_NM": region}


class CandidateSurveyTests(unittest.TestCase):
    def test_shared_ids_do_not_imply_directed_contiguous_run(self):
        groups = visit_groups([visit("a", 1, "A"), visit("a", 2, "B"),
                               visit("b", 1, "B"), visit("b", 2, "A")])
        self.assertEqual(shared_run(groups, ["a", "b"])["edge_count"], 0)

    def test_same_order_run_keeps_group_names_and_source_sequences(self):
        groups = visit_groups([visit("a", i, s, "variant-a") for i, s in ((4, "A"), (5, "B"), (6, "C"))]
                              + [visit("b", i, s, "variant-b") for i, s in ((8, "A"), (9, "B"), (10, "C"))])
        result = shared_run(groups, ["a", "b"], "B")
        self.assertEqual(result["stop_ids"], ["A", "B", "C"])
        self.assertEqual(result["evidence"][0]["matches"], [{"route_name": "variant-a", "sequences": [4, 5, 6]}])
        self.assertEqual(result["evidence"][1]["matches"][0]["sequences"], [8, 9, 10])

    def test_gap_or_conflicting_slot_is_not_filled(self):
        rows = [visit("a", 1, "A"), visit("a", 3, "B"),
                visit("a", 4, "C"), visit("a", 4, "D"), visit("a", 5, "E")]
        self.assertEqual(ordered_runs(rows), [[(1, "A")], [(3, "B")], [(5, "E")]])
        self.assertEqual(group_quality(rows)["conflicting_stop_sequence_groups"], 1)

    def test_duplicate_rows_and_repeated_visits_are_distinct(self):
        rows = [visit("a", 1, "A"), visit("a", 1, "A"),
                visit("a", 2, "B"), visit("a", 3, "A")]
        quality = group_quality(rows)
        self.assertEqual(quality["source_rows"], 4)
        self.assertEqual(quality["unique_sequence_stop_visits"], 3)
        self.assertEqual(quality["duplicate_sequence_groups"], 1)
        self.assertEqual(quality["repeated_stop_ids_at_different_sequences"], 1)
        groups = visit_groups(rows)
        self.assertEqual(forward_pairs(groups, "a", "A", "A"), 1)
        self.assertEqual(indexed_pairs(position_index(groups), "a", "A", "A"), 1)

    def test_order_candidate_zero_one_multiple_without_cross_group_splicing(self):
        rows = [visit("a", 1, "A"), visit("a", 2, "B"), visit("a", 3, "A"), visit("a", 4, "B"),
                visit("a", 1, "C", "other"), visit("a", 2, "D", "other")]
        index = position_index(visit_groups(rows))
        self.assertEqual(indexed_pairs(index, "a", "A", "B"), 3)
        self.assertEqual(indexed_pairs(index, "a", "C", "D"), 1)
        self.assertEqual(indexed_pairs(index, "a", "A", "D"), 0)

    def test_group_variants_never_form_one_contiguous_run(self):
        rows = [visit("a", 1, "A", "one"), visit("a", 2, "B", "one"),
                visit("a", 3, "C", "two"), visit("a", 4, "D", "two")]
        rows += [visit("b", i, stop) for i, stop in enumerate(("A", "B", "C", "D"), 1)]
        self.assertEqual(shared_run(visit_groups(rows), ["a", "b"])["edge_count"], 1)

    def test_validity_boundary_assumption_and_missing_values(self):
        rows = [{"START_DE": "20261102", "END_DE": "20261102", "ROUTE_NM": "same-day"},
                {"START_DE": "20200101", "END_DE": "20261101", "ROUTE_NM": "expired"},
                {"START_DE": "", "END_DE": "99991231", "ROUTE_NM": "unknown"}]
        result = validity(rows, "20261102")
        self.assertEqual((result["active_rows"], result["expired_rows"], result["unresolved_rows"]), (1, 1, 1))

    def test_region_metadata_preserves_exact_stop_ids_and_unknowns(self):
        result = region_summary([visit("a", 1, "A", region="경기도"), visit("a", 2, "B", region=""),
                                 visit("a", 3, "A-other", region="서울특별시")])
        self.assertEqual(result["unique_stop_ids"], 3)
        self.assertEqual(result["outside_seoul_stop_ids"], ["A"])
        self.assertEqual(result["unknown_region_stop_ids"], ["B"])

    def test_invalid_calendar_dates_are_unresolved_not_active(self):
        rows = [{"START_DE": "20260230", "END_DE": "99991231", "ROUTE_NM": "invalid-start"},
                {"START_DE": "20200101", "END_DE": "20261301", "ROUTE_NM": "invalid-end"}]
        result = validity(rows, "20261102")
        self.assertEqual(result["unresolved_rows"], 2)
        self.assertEqual(result["active_rows"], 0)

    def test_bad_visit_sequence_is_not_coerced_or_exposed(self):
        for value in ("0", "-1", " 2", "2.0", "２", "SYNTHETIC_SECRET"):
            with self.subTest(value=value), TemporaryDirectory() as directory:
                path = Path(directory) / "route_stops.csv"
                row = visit("r", 1, "A")
                row["BSST_ORDER"] = value
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, list(row))
                    writer.writeheader()
                    writer.writerow(row)
                with self.assertRaises(AnalysisError) as caught:
                    list(visit_source_rows(path, tuple(row)))
                self.assertEqual(str(caught.exception), "route_stops.csv:2:BSST_ORDER")

    def test_survey_cli_sanitizes_unexpected_input_error_details(self):
        output = io.StringIO()
        with patch("sys.argv", ["candidate_survey"]), patch("data.candidate_survey.survey", side_effect=ValueError("SYNTHETIC_SECRET")):
            with redirect_stderr(output), self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("ValueError", output.getvalue())
        self.assertNotIn("SYNTHETIC_SECRET", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_small_synthetic_files_exercise_report_and_weighted_scope(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, analysis = root / "input", root / "analysis"
            source.mkdir()
            analysis.mkdir()

            def write_csv(path, rows):
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)

            stops = [dict(**visit(rid, sequence, stop), BSST_NM="Synthetic " + stop,
                          lat="37.5", lon="127.0", STDR_DE="20261102")
                     for rid in ("a", "b", "c") for sequence, stop in enumerate(("H", "X", "Y"), 1)]
            write_csv(source / "route_stops.csv", stops)
            routes = [dict(ROUTE_ID=rid, ROUTE_NM="synthetic", ROUTE_NUM=rid,
                           START_DE="20200101", END_DE="99991231", STDR_DE="20261102") for rid in ("a", "b", "c")]
            for filename in ("routes_all_versions.csv", "routes_latest.csv"):
                write_csv(source / filename, routes)
            od = [dict(ROUTE_ID=rid, RIDE_BSST_ID=origin, ALGH_BSST_ID=destination,
                       RIDE_DTM_H="07", PSNG_NUM=str(n))
                  for rid, origin, destination, n in (("a", "H", "Y", 3), ("b", "H", "X", 2), ("c", "X", "Y", 1))]
            write_csv(source / "bus_od_0709.csv", od)
            write_csv(analysis / "morning_od_top20.csv", [{"RIDE_BSST_ID": "H", "ALGH_BSST_ID": "Y", "passenger_count": "3"}])
            (analysis / "summary.json").write_text(json.dumps({"group_counts": {"od": 3}, "morning_passengers": 6,
                "top20_sums_not_full_totals": {"od": 3}, "observation_date_from_readme": "2026-11-02"}), encoding="utf-8")
            with patch("data.candidate_survey.SEEDS", (("sample", "H", "synthetic seed"),)):
                result = survey(source, analysis, root / "output")
            candidate = result["candidates"][0]
            self.assertEqual(candidate["bundle_morning_passengers"], 6)
            self.assertEqual(candidate["hub_endpoint_passengers"], 5)
            self.assertEqual(candidate["shared_run_endpoint_passengers"], 6)
            self.assertEqual(candidate["order_candidates"]["one"], {"rows": 3, "passengers": 6})
            self.assertEqual(candidate["all_routes_shared_source_run_through_hub"]["edge_count"], 2)
            self.assertEqual(result["recommendation"], "sample")
            self.assertFalse(result["baseline_check"]["equal"])
            self.assertIsNotNone(result["baseline_check"]["mismatch_note"])
            self.assertTrue((root / "output/source_visits.csv").exists())
            self.assertTrue((root / "output/report.md").exists())


if __name__ == "__main__":
    unittest.main()
