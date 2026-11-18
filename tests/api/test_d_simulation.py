"""Published report and C/B artifact adapter boundaries."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from busstrike.api.d_geo import DataError
from busstrike.api.d_simulation import (
    SelectionStore,
    SimulationStore,
    _spatial_outcomes,
)


class SimulationTests(unittest.TestCase):
    def test_b_outcomes_are_aggregated_by_origin_without_passenger_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "passenger_outcomes.csv"
            file.write_text(
                "passenger_id,origin,destination,time_s,outcome\n"
                "private-1,s1,s2,25200,served\n"
                "private-2,s1,s3,25210,unserved\n",
                encoding="utf-8",
            )
            rows = _spatial_outcomes(file)
            self.assertEqual(rows["s1"]["hours"]["07"]["unserved"], 1)
            self.assertEqual(rows["s1"]["total"], 2)
            self.assertNotIn("private-1", str(rows))

    def test_v4_folder_layout_loads_plan_stops_metrics_and_verifier(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / "results" / "guro"
            inputs = root / "inputs" / "guro_0610_v4" / "network"
            for path in (base / "plans", base / "candidates", inputs):
                path.mkdir(parents=True)

            def put(path, payload):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )

            put(
                base / "manifest.json",
                {
                    "network_snapshot_id": "net-v4",
                    "plans": [
                        {
                            "plan_id": "plan-01-affected-f11",
                            "label": "ours",
                            "variant": "affected",
                            "fleet": 11,
                            "routes": {"r1": {"candidate": "c1", "vehicles": 3}},
                        }
                    ],
                },
            )
            put(
                base / "plans" / "plan-01-affected-f11.json",
                {
                    "plan_id": "plan-01-affected-f11",
                    "revision": 2,
                    "network_snapshot_id": "net-v4",
                    "routes": [
                        {"route_id": "r1", "stops": ["s1", "s2"], "vehicles": 3}
                    ],
                },
            )
            put(
                base / "candidates" / "affected.json",
                {
                    "candidates": [
                        {
                            "route_id": "c1",
                            "kind": "access",
                            "origin": "generated",
                            "stops": ["s1", "s2"],
                        }
                    ]
                },
            )
            run = base / "runs" / "affected"
            put(
                run / "runs.json",
                {
                    "runs": [
                        {
                            "label": "ours",
                            "fleet": 11,
                            "date": "20261026",
                            "sim_rc": 0,
                            "input_bundle_id": "bundle-v4",
                        }
                    ]
                },
            )
            daily = run / "ours" / "affected_20261026" / "f11"
            put(
                daily / "run" / "metrics.json",
                {
                    "M1": {
                        "total": 100,
                        "served": 20,
                        "unserved": 80,
                        "censored": 0,
                        "rate": 0.2,
                    },
                    "M2": {"unserved_by_reason": {"no_connection": 80}},
                },
            )
            put(
                daily / "verifier_report.json",
                {
                    "pass": True,
                    "fully_verified": False,
                    "failed_checks": [],
                    "not_run_checks": [],
                    "not_verified_checks": ["V12"],
                },
            )
            put(daily / "plan_m05.json", {"scenario_id": "scenario-v4"})
            put(inputs / "travel_times.json", {"network_snapshot_id": "net-v4"})
            (inputs / "stops.csv").write_text(
                "stop_id,stop_name,stop_lat,stop_lon\ns1,첫 정류장,37.5,126.9\ns2,끝 정류장,37.51,126.91\n",
                encoding="utf-8",
            )
            store = SimulationStore(root / "results", root / "inputs")
            result = store.selection({"date": ["2026-10-26"]})
            self.assertEqual(result["metrics"]["rate_percent"], 20)
            self.assertEqual(result["routes"][0]["vehicles"], 3)
            self.assertEqual(result["routes"][0]["stops"][0]["name"], "첫 정류장")
            self.assertEqual(len(result["geojson"]["features"]), 2)
            self.assertEqual(result["scenario_id"], "scenario-v4")
            self.assertTrue(result["verification"]["pass"])
            self.assertEqual(result["outcome"]["status"], "passed")
            runs = store.runs({"date": ["2026-10-26"]})
            self.assertEqual(runs["source"], "runs.json")
            self.assertEqual(runs["runs"][0]["date"], "2026-10-26")
            with self.assertRaises(DataError) as mismatch:
                store.selection({"date": ["2026-10-26"], "revision": ["3"]})
            self.assertEqual(mismatch.exception.code, "DATA_VERSION_MISMATCH")

    def test_failed_and_unverified_b_runs_are_distinct(self):
        store = SimulationStore()
        base = {
            "manifest": {"network_snapshot_id": "net-v4"},
            "selection": {"plan_id": "plan-v4"},
            "plan": {
                "plan_id": "plan-v4",
                "revision": 1,
                "network_snapshot_id": "net-v4",
                "routes": [],
            },
            "metrics": {
                "M1": {"total": 100, "served": 20, "unserved": 80, "rate": 0.2}
            },
        }
        cases = (
            (
                {"sim_rc": 1, "stage_failed": "simulation", "reason": "engine error"},
                "calculation_failed",
                None,
            ),
            ({"sim_rc": 0}, "verification_unknown", 20),
            ({"sim_rc": 0}, "verification_failed", 20),
        )
        for rec, expected_status, served in cases:
            artifacts = {**base, "run_record": rec}
            if expected_status == "verification_failed":
                artifacts["verifier"] = {"pass": False, "failed_checks": ["V03"]}
            with (
                patch.object(store, "_artifacts", return_value=artifacts),
                patch.object(store, "_stops", return_value={}),
            ):
                result = store.selection({"date": ["2026-10-26"]})
            self.assertEqual(result["outcome"]["status"], expected_status)
            self.assertEqual(
                result["metrics"]["served"] if result["metrics"] else None, served
            )
            if expected_status == "calculation_failed":
                self.assertEqual(result["outcome"]["error"]["code"], "JOB_FAILED")
                self.assertEqual(result["execution"]["reason"], "engine error")

    def test_selection_pins_verified_run_version_and_records_history(self):
        result = {
            "selection": {
                "region": "guro",
                "fleet": 11,
                "variant": "affected",
                "plan": "ours",
                "period": "exp",
                "date": "2026-10-26",
            },
            "plan_id": "plan-01-affected-f11",
            "revision": 2,
            "network_snapshot_id": "network-v4",
            "scenario_id": "scenario-v4",
            "metrics": {"total": 100, "served": 20, "source": "M05_metrics_day"},
            "execution": {"sim_rc": 0, "input_bundle_id": "bundle-v4"},
            "verification": {
                "source": "verifier_report.json",
                "pass": True,
                "fully_verified": False,
                "failed_checks": [],
                "not_run_checks": [],
                "not_verified_checks": ["V12"],
            },
        }
        request = {
            **result["selection"],
            "plan_id": result["plan_id"],
            "revision": 2,
            "network_snapshot_id": "network-v4",
        }
        with tempfile.TemporaryDirectory() as folder:
            store = SelectionStore(Path(folder) / "selection.json")
            simulation = Mock(selection=Mock(return_value=result))
            with self.assertRaises(DataError) as mismatch:
                store.choose(simulation, {**request, "revision": 3})
            self.assertEqual(mismatch.exception.code, "DATA_VERSION_MISMATCH")
            chosen = store.choose(simulation, request)
            self.assertEqual(chosen["selected"]["revision"], 2)
            self.assertFalse(chosen["selected"]["approved_for_service"])
            self.assertEqual(len(store.read()["history"]), 1)

    def test_report_covers_every_published_combination(self):
        store = SimulationStore()
        self.assertEqual(len(store.report), 128)
        self.assertEqual(len(store.reasons), 16)
        for region in store.options()["regions"]:
            for fleet in region["fleets"]:
                for variant in store.options()["variants"]:
                    for plan in ("ours", "sp"):
                        for period in ("exp", "test"):
                            query = {
                                k: [str(v)]
                                for k, v in {
                                    "region": region["id"],
                                    "fleet": fleet,
                                    "variant": variant,
                                    "plan": plan,
                                    "period": period,
                                }.items()
                            }
                            row = store.selection(query)
                            self.assertGreater(
                                row["metrics"]["total"], row["metrics"]["served"]
                            )
                            self.assertEqual(row["routes"], [])
                            self.assertEqual(row["geojson"]["features"], [])
                            self.assertIsNone(row["verification"]["pass"])
        self.assertEqual(store.selection({})["metrics"]["rate_percent"], 12.58)
        self.assertIsNone(store.selection({"date": ["2026-10-26"]})["metrics"])
        selected = store.comparison({"region": ["gangdong"], "fleet": ["70"]})
        self.assertEqual(selected["regions"][-1]["fleet"], 70)
        self.assertEqual(selected["regions"][0]["fleet"], 11)

    def test_artifact_values_and_stops_are_not_inferred_from_report(self):
        store = SimulationStore()
        artifacts = {
            "manifest": {"network_snapshot_id": "network-test"},
            "selection": {
                "plan_id": "plan-test",
                "routes": {"r01": {"candidate": "c0001", "vehicles": 3}},
            },
            "plan": {
                "plan_id": "plan-test",
                "revision": 2,
                "network_snapshot_id": "network-test",
                "routes": [{"route_id": "r01", "stops": ["s1", "s2"], "vehicles": 3}],
            },
            "candidates": {
                "candidates": [
                    {"route_id": "c0001", "kind": "access", "origin": "generated"}
                ]
            },
            "metrics": {
                "M1": {"total": 100, "served": 20},
                "M2": {"unserved_by_reason": {"no_connection": 80}},
            },
            "verifier": {
                "pass": True,
                "fully_verified": False,
                "failed_checks": [],
                "not_verified_checks": ["V16"],
                "not_run_checks": [],
            },
        }
        stops = {
            "s1": {"name": "첫 정류장", "lat": 37.5, "lon": 126.9},
            "s2": {"name": "끝 정류장", "lat": 37.51, "lon": 126.91},
        }
        with (
            patch.object(store, "_artifacts", return_value=artifacts),
            patch.object(store, "_stops", return_value=stops),
        ):
            selected = store.selection({"date": ["2026-10-26"]})
        self.assertEqual(selected["metrics"]["rate_percent"], 20)
        self.assertEqual(selected["routes"][0]["vehicles"], 3)
        self.assertEqual(selected["routes"][0]["stops"][0]["name"], "첫 정류장")
        self.assertEqual(len(selected["geojson"]["features"]), 2)
        self.assertTrue(selected["verification"]["pass"])
        self.assertFalse(selected["verification"]["fully_verified"])


if __name__ == "__main__":
    unittest.main()
