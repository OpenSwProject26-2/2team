from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data.demand_analysis import AnalysisError, OD_FIELDS, analyze


def row(route="002", origin="0001", destination="0002", hour="07", count="2"):
    return dict(zip(OD_FIELDS, (route, origin, destination, hour, count)))


class DemandAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "input"
        self.source.mkdir()
        self.full = [row(), row(route="001", hour="08", count="3"),
                     row(route="001", origin="0002", destination="0001", hour="09", count="7"),
                     row(route="003", destination="0001", count="1"),
                     row(origin="0002", destination="0001", hour="06", count="4")]
        self.stops = [dict(BSST_ID=sid, BSST_NM=name, lat="37.5", lon="127.0",
                           STDR_DE="20261102", SIDO_NM="synthetic")
                      for sid, name in (("0001", "Synthetic A"), ("0002", "Synthetic B"))]

    def csv(self, name, rows, fields):
        with (self.source / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def prepare(self, full=None, morning=None):
        full = self.full if full is None else full
        if morning is None:
            morning = [r for r in full if r["RIDE_DTM_H"] in ("07", "08")]
        self.csv("bus_od_by_hour.csv", full, OD_FIELDS)
        self.csv("bus_od_0709.csv", morning, OD_FIELDS)
        self.csv("route_stops.csv", self.stops,
                 ("BSST_ID", "BSST_NM", "lat", "lon", "STDR_DE", "SIDO_NM"))
        routes = [dict(ROUTE_ID=rid, ROUTE_NM="Synthetic " + rid, ROUTE_NUM=rid, STDR_DE="20261102")
                  for rid in ("001", "002", "003")]
        for name in ("routes_all_versions.csv", "routes_latest.csv"):
            self.csv(name, routes, ("ROUTE_ID", "ROUTE_NM", "ROUTE_NUM", "STDR_DE"))
        (self.source / "README.md").write_text("관측일: **2026-11-02** · 다운로드: 2026-11-06\n", encoding="utf-8")
        stats = dict(od_psng=sum(int(r["PSNG_NUM"]) for r in full),
                     am_psng=sum(int(r["PSNG_NUM"]) for r in morning),
                     od_pairs=len(full), am_pairs=len(morning))
        (self.source / "stats.json").write_text(json.dumps(stats), encoding="utf-8")

    def run_analysis(self, **kwargs):
        return analyze(self.source, self.root / "output", **kwargs)

    def test_weighted_totals_hours_and_reconciliations(self):
        self.prepare()
        result = self.run_analysis()
        self.assertEqual(result["total_od_passengers"], 17)
        self.assertEqual(result["morning_passengers"], 6)
        self.assertEqual(result["morning_share_of_od_total"], 6 / 17)
        self.assertEqual(result["observation_date_from_readme"], "2026-11-02")
        self.assertTrue(result["all_reconciliations_passed"])
        self.assertEqual({r["group_total"] for r in result["reconciliations"]["morning_groups"].values()}, {6})
        with (self.root / "output/hourly.csv").open(encoding="utf-8-sig", newline="") as stream:
            hours = {r["RIDE_DTM_H"]: int(r["passenger_count"]) for r in csv.DictReader(stream)}
        self.assertEqual((hours["07"], hours["08"], hours["09"]), (3, 3, 7))
        self.assertFalse(Path(result["resources"]["temporary_database_path"]).exists())

    def test_cross_route_od_and_stop_totals_same_stop_retained(self):
        self.prepare()
        result = self.run_analysis()
        top = result["top3"]
        self.assertEqual(top["od"][0]["passenger_count"], 5)
        self.assertEqual(top["od"][0]["share"], 5 / 6)
        self.assertEqual(top["boarding"][0]["passenger_count"], 6)
        self.assertEqual([r["passenger_count"] for r in top["alighting"]], [5, 1])
        self.assertEqual(result["group_counts"]["od"], 2)
        self.assertEqual(top["od"][1]["RIDE_BSST_ID"], top["od"][1]["ALGH_BSST_ID"])

    def test_leading_zeroes_and_ties_sort_by_string_ids(self):
        full = [row(route="010", destination="0010", count="1"),
                row(route="002", origin="0002", destination="0002", count="1"),
                row(route="001", destination="0001", count="1")]
        self.prepare(full)
        result = self.run_analysis()
        self.assertEqual([r["ROUTE_ID"] for r in result["top3"]["routes"]], ["001", "002", "010"])
        self.assertEqual([(r["RIDE_BSST_ID"], r["ALGH_BSST_ID"]) for r in result["top3"]["od"]],
                         [("0001", "0001"), ("0001", "0010"), ("0002", "0002")])

    def test_zero_and_empty_inputs_use_null_shares(self):
        for full in ([], [row(count="0")]):
            with self.subTest(rows=len(full)):
                self.prepare(full)
                output = self.root / ("empty" if not full else "zero")
                result = analyze(self.source, output)
                self.assertIsNone(result["morning_share_of_od_total"])
                self.assertTrue(result["all_reconciliations_passed"])
                for records in result["top3"].values():
                    for r in records:
                        self.assertIsNone(r["share"])
                self.assertIn(",null", (output / "hourly.csv").read_text(encoding="utf-8-sig"))

    def test_ambiguous_and_duplicate_labels_never_multiply_passengers(self):
        self.stops.append(deepcopy(self.stops[0]))
        self.stops.append({**self.stops[0], "BSST_NM": "Synthetic alternative", "lat": "37.6"})
        self.prepare()
        # Multiple route names must also stay ambiguous.
        path = self.source / "routes_latest.csv"
        with path.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(("001", "Alternative route", "001", "20261102"))
        result = self.run_analysis()
        boarding = result["top3"]["boarding"][0]
        self.assertEqual(boarding["passenger_count"], 6)
        self.assertEqual(boarding["stop_name_status"], "ambiguous")
        self.assertIsNone(boarding["stop_name"])
        self.assertIsNone(boarding["lat"])
        self.assertEqual(result["top3"]["routes"][0]["route_name_status"], "ambiguous")
        self.assertTrue(result["all_reconciliations_passed"])

    def test_missing_or_invalid_display_information_retains_ids(self):
        self.stops[0]["BSST_NM"] = ""
        self.stops[0]["lat"] = "invalid"
        self.prepare([row(origin="unknown", count="2"), row(count="1")])
        result = self.run_analysis()
        first = result["top3"]["boarding"][0]
        self.assertEqual(first["RIDE_BSST_ID"], "unknown")
        self.assertEqual(first["stop_name_status"], "missing")
        self.assertEqual(result["label_coverage"]["morning_boarding"]["unmatched_ids"], 1)
        self.assertEqual(result["label_quality"]["invalid_coordinate_rows"], 1)
        self.assertEqual(result["morning_passengers"], 3)

    def test_invalid_values_fail_safely_with_filename_line_and_field(self):
        for field, invalid in (("PSNG_NUM", "-1"), ("PSNG_NUM", "2.0"), ("PSNG_NUM", " 2"),
                               ("PSNG_NUM", "２"), ("PSNG_NUM", "SYNTHETIC_SECRET"),
                               ("RIDE_DTM_H", "7"), ("RIDE_DTM_H", "24"), ("ROUTE_ID", " ")):
            with self.subTest(field=field, invalid=invalid):
                self.prepare()
                bad = row()
                bad[field] = invalid
                self.csv("bus_od_by_hour.csv", [row(), bad], OD_FIELDS)
                with self.assertRaises(AnalysisError) as caught:
                    self.run_analysis()
                self.assertEqual(str(caught.exception), f"bus_od_by_hour.csv:3:{field}")
                self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))

    def test_missing_required_column_fails(self):
        self.prepare()
        self.csv("bus_od_by_hour.csv", [], OD_FIELDS[:-1])
        with self.assertRaisesRegex(AnalysisError, r"bus_od_by_hour.csv:1:PSNG_NUM"):
            self.run_analysis()

    def test_morning_file_outside_window_is_error(self):
        self.prepare(morning=[row(hour="09")])
        with self.assertRaisesRegex(AnalysisError, r"bus_od_0709.csv:2:RIDE_DTM_H"):
            self.run_analysis()

    def test_mismatches_are_recorded_not_claimed_as_success(self):
        self.prepare(morning=[row(count="1")])
        (self.source / "stats.json").write_text(json.dumps(dict(od_psng=99, am_psng=99,
                                                                od_pairs=99, am_pairs=99)), encoding="utf-8")
        result = self.run_analysis()
        self.assertFalse(result["all_reconciliations_passed"])
        self.assertEqual(result["reconciliations"]["morning_files"]["difference"], 5)
        self.assertEqual(result["stats_comparisons"]["od_psng"]["difference"], -82)

    def test_top20_sum_is_not_full_total(self):
        self.prepare([row(route=f"{i:03d}", count="1") for i in range(25)])
        result = self.run_analysis()
        self.assertEqual(result["morning_passengers"], 25)
        self.assertEqual(result["top20_sums_not_full_totals"]["routes"], 20)
        self.assertEqual(result["reconciliations"]["morning_groups"]["routes"]["group_total"], 25)

    def test_output_cannot_overwrite_input_or_prior_run(self):
        self.prepare()
        with self.assertRaises(AnalysisError):
            analyze(self.source, self.source)
        self.run_analysis()
        with self.assertRaises(AnalysisError):
            self.run_analysis()

    def test_malformed_csv_is_rejected_without_exposing_content(self):
        self.prepare()
        path = self.source / "bus_od_by_hour.csv"
        path.write_text(",".join(OD_FIELDS) + '\n001,0001,0002,07,"SYNTHETIC_SECRET', encoding="utf-8")
        with self.assertRaises(AnalysisError) as caught:
            self.run_analysis()
        self.assertEqual(str(caught.exception), "bus_od_by_hour.csv:2:CSV")

    def test_morning_failure_closes_database_and_removes_temporary_files(self):
        self.prepare(morning=[row(hour="09")])
        scratch = self.root / "sqlite-temp"
        with self.assertRaises(AnalysisError):
            self.run_analysis(temp_dir=scratch)
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertFalse((self.root / "output/summary.json").exists())


if __name__ == "__main__":
    unittest.main()
