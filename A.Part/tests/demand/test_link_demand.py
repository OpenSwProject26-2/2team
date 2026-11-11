from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from data.clean_network import build_network, clean, load_visits, settings
from data.demand_analysis import AnalysisError
from data.extract_guro_draft import digest, dump_json
from data.link_demand import (VisitIndex, SOURCE_FIELDS, link_row, run, source_rows,
                              validate_saved)


PART = Path(__file__).resolve().parents[2]
FIXTURE = PART / "tests/network/fixtures/synthetic"


class DemandLinkTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_visits(FIXTURE / "route_visits.csv")
        # This test case revisits A before C, unlike the shared A-B-C-A fixture.
        self.rows[2]["BSST_ID"] = "stop-A"
        self.rows[3]["BSST_ID"] = "stop-C"
        self.stops = json.loads((FIXTURE / "stops.json").read_text(encoding="utf-8"))
        self.config = settings(json.loads((PART / "data/examples/network_cleaning.synthetic.json").read_text(encoding="utf-8")))
        self.route = self.rows[0]["ROUTE_ID"]

    def network(self):
        return build_network(self.rows, self.stops, self.config, "synthetic-checksum")

    def od(self, origin="stop-B", destination="stop-C", hour="07", count="2", serial="2"):
        return dict(zip(SOURCE_FIELDS, (serial, self.route, origin, destination, hour, count,
                                       "synthetic", "synthetic", "synthetic")))

    def record(self, row=None, network=None):
        return link_row(2, row or self.od(), "synthetic-checksum",
                        VisitIndex(network or self.network()))

    def test_unique_connection_is_not_verified_direction_or_engine_input(self):
        record = self.record()
        self.assertEqual(record["classification"], "unique_connection")
        self.assertEqual(record["disposition"], "included")
        self.assertEqual(len(record["candidates"][0]["segment_ids"]), 2)
        self.assertEqual(record["actual_direction_status"], "unverified")
        self.assertEqual(record["route_version_status"], "unverified")
        self.assertFalse(record["engine_ready"])
        self.assertNotIn("arrival_s", record)
        self.assertNotIn("bin", record)

    def test_repeated_stop_visits_create_distinct_candidates(self):
        record = self.record(self.od("stop-A", "stop-C"))
        self.assertEqual(record["classification"], "multiple_candidates")
        self.assertEqual(record["disposition"], "held")
        self.assertEqual(len({c["origin_visit_id"] for c in record["candidates"]}), 2)

    def test_all_preserved_groups_searched_not_only_selected(self):
        for row in deepcopy(self.rows):
            row["ROUTE_NM"] = "other synthetic source name"
            row["input_csv_line"] += 10
            self.rows.append(row)
        record = self.record()
        self.assertEqual(record["candidate_count"], 2)
        self.assertEqual(len(record["examined_groups"]), 2)
        self.assertEqual({p["group_role"] for p in record["candidates"]},
                         {"selected_source_group", "other_source_group_preserved"})

    def test_gap_does_not_connect_endpoints(self):
        self.rows = [r for r in self.rows if r["BSST_ORDER"] != "3"]
        record = self.record()
        self.assertEqual(record["classification"], "no_connection")
        self.assertEqual(record["rejected_ordered_pairs"][0]["reason"], "sequence_gap")

    def test_conflict_does_not_connect_through_slot(self):
        row = deepcopy(self.rows[2])
        row.update(BSST_ID="stop-C", input_csv_line=20)
        self.rows.append(row)
        record = self.record()
        self.assertEqual(record["classification"], "no_connection")
        self.assertEqual(record["rejected_ordered_pairs"][0]["reason"], "sequence_conflict")

    def test_missing_segment_never_becomes_direct_shortcut(self):
        network = self.network()
        network["segments"] = []
        record = self.record(network=network)
        self.assertEqual(record["classification"], "no_connection")
        self.assertEqual(record["rejected_ordered_pairs"][0]["reason"], "missing_segment")

    def test_same_stop_and_no_connection_are_preserved_separately(self):
        same = self.record(self.od("stop-A", "stop-A"))
        self.assertEqual((same["classification"], same["disposition"]), ("same_stop", "excluded"))
        for origin, destination in (("stop-C", "stop-B"), ("missing", "stop-C")):
            record = self.record(self.od(origin, destination))
            self.assertEqual((record["classification"], record["disposition"]), ("no_connection", "held"))

    def test_source_hours_weights_and_original_strings_preserved(self):
        for hour in ("07", "08"):
            row = self.od(hour=hour, count="02")
            record = self.record(row)
            self.assertEqual(record["source"], row)
        self.assertEqual(self.record(self.od(count="0"))["source"]["PSNG_NUM"], "0")

    def test_candidate_determinism_source_refs_and_no_mutation(self):
        network = self.network()
        before = deepcopy(network)
        first = self.record(network=network)
        self.assertEqual(network, before)
        network["visits"].reverse()
        network["segments"].reverse()
        self.assertEqual(first, self.record(network=network))
        self.assertTrue(first["candidates"][0]["source_row_ids"])
        self.assertTrue(first["candidates"][0]["source_date_candidates"])

    def test_broken_references_duplicate_visit_and_unsafe_edge_rejected(self):
        for mutation in ("visit", "reference", "sequence"):
            network = self.network()
            if mutation == "visit":
                network["visits"].append(deepcopy(network["visits"][0]))
            elif mutation == "reference":
                network["segments"][0]["origin_visit_id"] = "missing"
            else:
                network["segments"][0]["destination_source_sequence"] += 1
            with self.subTest(mutation=mutation), self.assertRaises(AnalysisError):
                VisitIndex(network)

    def prepare(self, root, two_routes=False):
        draft = root / "draft"
        draft.mkdir()
        visit_rows = deepcopy(self.rows)
        config = deepcopy(self.config)
        if two_routes:
            for row in deepcopy(visit_rows):
                row.update(ROUTE_ID="route-B", ROUTE_NM="Second synthetic group",
                           input_csv_line=row["input_csv_line"] + 10,
                           source_csv_line=str(int(row["source_csv_line"]) + 10))
                visit_rows.append(row)
            config["route_ids"].append("route-B")
            config["selected_groups"].append({"route_id": "route-B", "source_route_name": "Second synthetic group"})
        with (FIXTURE / "route_visits.csv").open(encoding="utf-8", newline="") as stream:
            fields = csv.DictReader(stream).fieldnames
        with (draft / "route_visits.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in visit_rows:
                writer.writerow({**row, "group_role": row["prior_group_role"],
                                 "same_sequence_stop_source_rows": row["declared_multiplicity"]})
        dump_json(draft / "stops.json", self.stops)
        # Declared fixture expectations predate OD mutations in regression tests.
        # Match the real extractor's schema; do not derive expectations from OD.
        extraction = {"schema_version": "guro-real-input-extraction-draft-v1",
                      "status": "development_only_not_operational_input", "all_checks_passed": True,
                      "route_ids": config["route_ids"], "selected_source_groups": config["selected_groups"],
                      "total_morning": {"rows": 8 if two_routes else 4, "passengers": 28 if two_routes else 14},
                      "route_totals": {rid: {"rows": 4, "passengers": 14} for rid in config["route_ids"]},
                      "observation_date_from_survey": "20261102",
                      "sources": [{"path": "synthetic/" + name, "bytes": 0, "sha256": "0" * 64}
                                  for name in ("survey.json", "report.md", "route_stops.csv", "bus_od_0709.csv")]}
        dump_json(draft / "validation_report.json", extraction)
        config.update(input_dir=str(draft), output_dir=str(root / "network"))
        dump_json(root / "config.json", config)
        clean(root / "config.json")
        rows = [self.od(count="2", serial="2"),
                self.od("stop-A", "stop-C", count="3", serial="3"),
                self.od("stop-C", "stop-B", count="4", serial="4"),
                self.od("stop-A", "stop-A", hour="08", count="5", serial="5")]
        if two_routes:
            rows += [{**row, "ROUTE_ID": "route-B", "source_csv_line": str(int(row["source_csv_line"]) + 10)}
                     for row in deepcopy(rows)]
        with (draft / "morning_od.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=SOURCE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return draft, root / "network", rows

    def test_end_to_end_conservation_and_saved_content_revalidation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            draft, network_dir, _ = self.prepare(root)
            output = root / "output"
            report = run(draft, network_dir, output)
            self.assertTrue(report["all_checks_passed"])
            self.assertEqual(report["summary"]["total"], {"rows": 4, "passengers": 14})
            self.assertEqual(report["summary"]["dispositions"],
                             {"included": {"rows": 1, "passengers": 2},
                              "excluded": {"rows": 1, "passengers": 5},
                              "held": {"rows": 2, "passengers": 7}})
            with self.assertRaises(AnalysisError):
                run(draft, network_dir, output)
            with self.assertRaises(AnalysisError):
                run(draft, network_dir, draft / "nested")

    def test_saved_missing_duplicate_wrong_content_and_summary_detected(self):
        for mode in ("missing", "duplicate", "link_content", "summary"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network_dir, _ = self.prepare(root)
                output = root / "output"
                report = run(draft, network_dir, output)
                path = output / "od_links.jsonl"
                records = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
                if mode == "missing":
                    records.pop()
                elif mode == "duplicate":
                    records.append(deepcopy(records[0]))
                elif mode == "link_content":
                    records[0]["candidates"][0]["source_group_id"] = "wrong"
                else:
                    report["summary"]["total"]["passengers"] += 1
                path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
                # Saved source IDs derive from the actual copied visit file checksum.
                from data.link_demand import load_network
                network, _ = load_network(draft, network_dir)
                index = VisitIndex(network)
                check = validate_saved(draft / "morning_od.csv", output,
                                       digest(draft / "morning_od.csv")["sha256"], index, report["summary"])
                self.assertFalse(check["all_checks_passed"])

    def write_od(self, draft, rows):
        with (draft / "morning_od.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=SOURCE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_removed_od_and_changed_weight_fail_against_unchanged_extraction(self):
        for mode in ("removed", "count"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network_dir, rows = self.prepare(root)
                report_bytes = (draft / "validation_report.json").read_bytes()
                if mode == "removed":
                    rows.pop()
                else:
                    rows[0]["PSNG_NUM"] = "3"
                self.write_od(draft, rows)
                report = run(draft, network_dir, root / "bad")
                self.assertFalse(report["all_checks_passed"])
                self.assertFalse(report["extraction_reconciliation"]["all_checks_passed"])
                self.assertEqual((draft / "validation_report.json").read_bytes(), report_bytes)

    def test_same_total_but_changed_route_weights_fails(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            draft, network_dir, rows = self.prepare(root, two_routes=True)
            rows[0]["PSNG_NUM"], rows[4]["PSNG_NUM"] = "3", "1"
            self.write_od(draft, rows)
            report = run(draft, network_dir, root / "bad")
            check = report["extraction_reconciliation"]
            self.assertTrue(check["checks"]["total_rows"])
            self.assertTrue(check["checks"]["total_passengers"])
            self.assertFalse(check["checks"]["all_route_totals"])
            self.assertFalse(report["all_checks_passed"])

    def test_changed_report_date_or_source_fails_cleaning_checksum(self):
        for field in ("observation_date_from_survey", "sources"):
            with self.subTest(field=field), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network_dir, _ = self.prepare(root)
                path = draft / "validation_report.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                if field == "sources":
                    report[field][0]["sha256"] = "1" * 64
                else:
                    report[field] = "20261103"
                dump_json(path, report)
                with self.assertRaisesRegex(AnalysisError, r"validation_report.json:0:cleaning_source_checksum_mismatch"):
                    run(draft, network_dir, root / "bad")
                self.assertFalse((root / "bad").exists())

    def test_report_schema_missing_fields_or_scope_group_mismatch_fail(self):
        mutations = ["schema_version", "status", "all_checks_passed", "total_morning", "route_totals",
                     "route_ids", "selected_source_groups", "sources", "observation_date_from_survey",
                     "route_mismatch", "group_mismatch", "count_type", "route_keys", "missing_upstream"]
        for mode in mutations:
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network_dir, _ = self.prepare(root)
                path = draft / "validation_report.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                if mode == "route_mismatch":
                    report["route_ids"] = ["different synthetic route"]
                elif mode == "group_mismatch":
                    report["selected_source_groups"][0]["source_route_name"] = "different synthetic group"
                elif mode == "count_type":
                    report["total_morning"]["rows"] = True
                elif mode == "route_keys":
                    report["route_totals"]["unexpected"] = {"rows": 0, "passengers": 0}
                elif mode == "missing_upstream":
                    report["sources"].pop()
                else:
                    del report[mode]
                dump_json(path, report)
                # Bind this deliberately malformed synthetic report to the cleaning
                # manifest to exercise schema/scope checks beyond checksum equality.
                quality_path = network_dir / "quality_report.json"
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
                quality["inputs"]["validation_report.json"] = digest(path)
                dump_json(quality_path, quality)
                with self.assertRaises(AnalysisError):
                    run(draft, network_dir, root / "bad")
                self.assertFalse((root / "bad").exists())

    def test_missing_required_cleaning_checksum_fails_for_every_input(self):
        for filename in ("validation_report.json", "route_visits.csv", "stops.json"):
            with self.subTest(filename=filename), TemporaryDirectory() as folder:
                root = Path(folder)
                draft, network_dir, _ = self.prepare(root)
                path = network_dir / "quality_report.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                del report["inputs"][filename]
                dump_json(path, report)
                with self.assertRaisesRegex(AnalysisError, "sha256_missing_or_invalid"):
                    run(draft, network_dir, root / "bad")

    def test_bad_source_is_not_silently_discarded_and_error_hides_values(self):
        for field, value in (("PSNG_NUM", "-1"), ("PSNG_NUM", "2.0"), ("PSNG_NUM", " 2"),
                             ("PSNG_NUM", "٢"), ("RIDE_DTM_H", "09"), ("ROUTE_ID", " "),
                             ("source_csv_line", "1")):
            with self.subTest(field=field, value=value), TemporaryDirectory() as folder:
                path = Path(folder) / "od.csv"
                row = self.od()
                row[field] = value
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=SOURCE_FIELDS)
                    writer.writeheader()
                    writer.writerow(row)
                with self.assertRaises(AnalysisError) as caught:
                    list(source_rows(path))
                self.assertEqual(str(caught.exception), f"od.csv:2:{field}")

    def test_duplicate_source_line_missing_column_and_network_tampering_fail(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            draft, network_dir, rows = self.prepare(root)
            path = draft / "morning_od.csv"
            with path.open("a", encoding="utf-8", newline="") as stream:
                csv.DictWriter(stream, fieldnames=SOURCE_FIELDS).writerow(rows[0])
            with self.assertRaises(AnalysisError):
                list(source_rows(path))
            path.write_text("ROUTE_ID\nsynthetic\n", encoding="utf-8")
            with self.assertRaises(AnalysisError):
                list(source_rows(path))
            payload = json.loads((network_dir / "visits.json").read_text())
            payload["visits"] = payload["visits"][1:]
            dump_json(network_dir / "visits.json", payload)
            with self.assertRaises(AnalysisError):
                run(draft, network_dir, root / "bad")


if __name__ == "__main__":
    unittest.main()
