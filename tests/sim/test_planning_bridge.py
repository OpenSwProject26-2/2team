"""C(planning) ↔ M05 연결(simulator.md §7.3, §10): 어댑터 → 시뮬레이션(M07 폴더) → M07 검증기 → 지표 → Assessment.

입력은 C의 합성 예제(tests/fixtures/planning/synthetic.json, 읽기만)와 테스트 안에서 만든 합성 입력뿐이다.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from busstrike.evaluation.verify import verify
from busstrike.planning.baselines import baseline
from busstrike.planning.evaluator import evaluate
from busstrike.planning.integration import checked_assessment, rerank
from busstrike.planning.models import Demand, InputError, Segment, make_plan
from busstrike.planning.search import search
from busstrike.sim.adapters import planning_bridge as pb
from busstrike.sim.models import SimError

from tests.sim import planning_cases as pc
from tests.sim.cases import pid


def events(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / "run" / "events.jsonl").read_text(encoding="utf-8").splitlines()]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.x = pc.c_synthetic()

    def test_scenario_and_travel_times(self):
        b = pb.to_sim_inputs(self.x)
        sc = b.scenario
        self.assertEqual((sc["scenario_id"], sc["operating_window"]), ("synthetic-v1", {"start_s": 0, "end_s": 480}))
        self.assertEqual(sc["policy"]["max_journey_s"], 420)
        self.assertEqual((sc["policy"]["dwell_base_s"], sc["policy"]["dwell_per_boarding_s"],
                          sc["policy"]["dwell_per_alighting_s"], sc["policy"]["cycle_rest_s"]), (0, 0, 0, 0))
        v = sc["fleet"]["vehicles"][0]
        self.assertEqual((v["seat_capacity"], v["standing_capacity"], v["initial_stop_id"], v["end_stop_id"]),
                         (2, 0, "A", "A"))
        segs = {(s["from_stop_id"], s["to_stop_id"]): s for s in b.travel_times["segments"]}
        self.assertEqual(len(segs), 6)
        self.assertEqual(segs[("A", "B")]["profile"], [[0, 60]])
        self.assertEqual(segs[("A", "B")]["distance_m"], 300)

    def test_demands_become_one_passenger_each(self):
        b = pb.to_sim_inputs(self.x)
        self.assertEqual(len(b.passengers), self.x.total_demand)
        self.assertEqual(b.demand_index, {"c000001": "d1", "c000002": "d2", "c000003": "d3", "c000004": "d4",
                                          "c000005": "d5"})
        first = b.passengers[0]
        self.assertEqual((first.passenger_id, first.origin, first.destination, first.arrival_s),
                         (pid("c000001"), "A", "C", 0))
        self.assertEqual([(r.aggregate_id, r.bin_start_s, r.bin_width_s, r.passenger_count) for r in b.rows],
                         [("c000001", 0, 1, 3), ("c000002", 60, 1, 2), ("c000003", 0, 1, 2), ("c000004", 0, 1, 1),
                          ("c000005", 120, 1, 1)])
        # 같은 도착 시각에서는 C의 (arrival_s, demand_id) 순서와 같다.
        self.assertEqual([json.loads(p.passenger_id)[0] for p in b.passengers],
                         ["c000001"] * 3 + ["c000003"] * 2 + ["c000004", "c000002", "c000002", "c000005"])

    def test_plan_dict_uses_c_service_plan(self):
        plan = make_plan(self.x, {"r1": 1, "r2": 0, "r3": 1}, "test")
        doc = pb.plan_dict(plan, self.x)
        self.assertEqual([r["route_id"] for r in doc["routes"]], ["r1", "r3"])   # 0대 노선 제외
        self.assertEqual(doc["routes"][0]["legs"], [{"in_service": True, "stop_ids": ["A", "B", "C"]},
                                                    {"in_service": False, "stop_ids": ["C", "B", "A"]}])
        service_plan = plan.to_service_plan(self.x)
        self.assertEqual({k: v for k, v in doc.items() if k != "routes"},
                         {k: v for k, v in service_plan.items() if k != "routes"})
        self.assertEqual((doc["trips"], doc["vehicle_duties"], doc["schedule_status"]), ([], [], "pending_b"))
        with self.assertRaises(InputError):
            pb.plan_dict(make_plan(self.x, {"r1": 3}, "test"), self.x)   # C의 배분 검사 그대로

    def test_rejects_ambiguous_segments_and_out_of_window_demand(self):
        x = replace(self.x, segments=self.x.segments + (Segment("ab2", "A", "B", 90, 300),))
        with self.assertRaises(SimError) as cm:
            pb.to_sim_inputs(x)
        self.assertEqual(cm.exception.code, SimError.INPUT_INVALID)
        x = replace(self.x, demands=self.x.demands + (Demand("late", "A", "B", 500, 1, "r1"),))
        with self.assertRaises(SimError) as cm:
            pb.to_sim_inputs(x)
        self.assertEqual(cm.exception.details["demand_id"], "late")


class EndToEndTests(unittest.TestCase):
    """C 합성 예제: 어댑터 → 시간표 → 엔진 → 검증기 → 지표 → Assessment."""

    def setUp(self):
        self.x = pc.c_synthetic()

    def test_equal_baseline_full_pipeline(self):
        plan = baseline(self.x)                    # r1 1대, r2 1대
        with tempfile.TemporaryDirectory() as d:
            br = pb.run_plan(plan, self.x, Path(d))
            self.assertTrue(br.validation["pass"], br.validation["failed_checks"])
            self.assertTrue((Path(d) / "run" / "verifier_report.json").is_file())
            self.assertEqual(verify(Path(d) / "run").payload["checks"], br.validation["checks"])
            log = events(Path(d))
        m = br.metrics
        self.assertEqual((m["M1"]["total"], m["M1"]["served"], m["M1"]["unserved"], m["physical_at_drain"]["arrived"]),
                         (9, 7, 2, 7))
        self.assertEqual({k: v for k, v in m["M2"]["unserved_by_reason"].items() if v}, {"no_connection": 1, "wait": 1})
        self.assertEqual(m["M2"]["refusal_events"], 4)    # A@0 d1 셋째, B@60 d2 둘, B@300 d2 둘째
        self.assertEqual(pb.total_distance_m(m), evaluate(self.x, plan).distance_m)
        # 손계산: d1 셋째(["c000001",2])는 r1 둘째 출발(240)에 타고, d2 둘째는 B에서 만차로 남겨져 360에 만료.
        boards = {e["passenger_id"]: (e["time_s"], e["vehicle_id"]) for e in log if e["kind"] == "boarded"}
        self.assertEqual(boards[pid("c000001", 2)], (240, "v1"))
        self.assertEqual(boards[pid("c000005")], (240, "v1"))
        self.assertEqual(boards[pid("c000002")], (300, "v1"))
        expired = [(e["passenger_id"], e["time_s"], e["stable_id"].split(":")[0]) for e in log if e["kind"] == "expired"]
        self.assertEqual(expired, [(pid("c000004"), 300, "wait"), (pid("c000002", 1), 360, "wait")])

    def test_evaluation_port_passes_c_checks_and_rerank(self):
        plan = baseline(self.x)
        a = checked_assessment(pb.evaluation_port, plan, self.x)
        self.assertEqual((a.schedule_feasible, a.independently_verified, a.violations), (True, True, ()))
        self.assertEqual((a.served, a.total, a.p90_wait_s, a.distance_m), (7, 9, 240, 4800))
        selection = rerank(self.x, search(self.x).top, pb.evaluation_port)
        self.assertIsNotNone(selection.recommended)
        self.assertEqual(len(selection.assessments), 3)
        self.assertTrue(all(r.independently_verified for r in selection.assessments))

    def test_port_work_dir_keeps_files(self):
        plan = baseline(self.x)
        with tempfile.TemporaryDirectory() as d:
            a = pb.make_evaluation_port(Path(d))(plan, self.x)
            root = Path(d) / plan.plan_id
            files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        self.assertTrue(a.independently_verified)
        self.assertEqual(files, ["inputs/demand_bins.csv", "inputs/demand_index.json", "inputs/plan.json",
                                 "inputs/run_inputs.json", "inputs/scenario.json", "inputs/travel_times.json",
                                 "run/events.jsonl", "run/measurement.json", "run/metrics.json",
                                 "run/passenger_outcomes.csv", "run/realization.jsonl", "run/timetable.json",
                                 "run/verifier_report.json", "scenario/demand_bins.csv", "scenario/network.json",
                                 "scenario/scenario_manifest.json"])

    def test_infeasible_schedule_is_reported_not_raised(self):
        plan = baseline(self.x)
        err = SimError(SimError.PLAN_INFEASIBLE, "synthetic")
        with mock.patch.object(pb, "run_plan", side_effect=err):
            a = checked_assessment(pb.evaluation_port, plan, self.x)
        self.assertEqual((a.schedule_feasible, a.independently_verified, a.served, a.violations),
                         (False, False, 0, ("PLAN_INFEASIBLE",)))
        with mock.patch.object(pb, "run_plan", side_effect=SimError(SimError.DATA_INCOMPLETE, "x")):
            with self.assertRaises(SimError):
                pb.evaluation_port(plan, self.x)

    def test_same_inputs_same_bytes(self):
        plan = baseline(self.x)
        outs = []
        with tempfile.TemporaryDirectory() as d:
            for name in ("a", "b"):
                pb.run_plan(plan, self.x, Path(d) / name, validate=False)
                root = Path(d) / name
                outs.append({p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
                             if p.is_file()})
        self.assertEqual(outs[0], outs[1])
        self.assertIn("run/events.jsonl", outs[0])


class CurveAndComparisonTests(unittest.TestCase):
    def setUp(self):
        self.x = pc.c_synthetic()

    def test_curve_report_on_c_synthetic(self):
        cr = pb.curve_report(self.x)
        self.assertEqual(cr["clusters"], [["r1", "r2", "r3"]])
        table = {r["route_id"]: [(p["n"], p["sim_satisfied"], p["sim_marginal"], p["est_served"], p["simulated"])
                                 for p in r["points"]] for r in cr["routes"]}
        # 손계산(r1 단독, 정원 2): 1대 = 출발 0·240 → 5명, 2대 = 출발 0·120·240 → eligible 6명 전원.
        self.assertEqual(table["r1"], [(0, 0, None, 0, True), (1, 5, 5, 5, True), (2, 6, 1, 6, True)])
        self.assertEqual(table["r2"], [(0, 0, None, 0, True), (1, 3, 3, 3, True), (2, 3, 0, 3, False)])
        self.assertEqual(table["r3"], [(0, 0, None, 0, True), (1, 1, 1, 1, True), (2, 1, 0, 1, False)])
        self.assertEqual({r["route_id"]: r["overlap_ratio"] for r in cr["routes"]},
                         {"r1": 0.166667, "r2": 0.333333, "r3": 1.0})
        self.assertEqual({r["route_id"]: r["beta"] for r in cr["routes"]}, {"r1": 1.0, "r2": 1.0, "r3": 1.0})

    def test_comparison_on_c_synthetic(self):
        plans = pb.comparison_plans(self.x)
        self.assertEqual(len(plans), 10)        # 기준안·탐색안 + 차량 미사용 포함 전수 배분(중복 제거)
        labels = [label for label, _ in plans]
        self.assertEqual(labels[:2], ["equal", "proportional"])
        self.assertEqual(labels[2], "search-top3")   # top1·top2는 기준안과 같은 배분이라 빠진다
        no_search = [label for label, _ in pb.comparison_plans(self.x, include_search=False)]
        self.assertNotIn("search-top3", no_search)
        self.assertEqual(len(no_search), 10)
        c = pb.compare_with_estimate(self.x, plans)
        rows = {tuple(sorted(r["allocation"].items())): r for r in c["rows"]}
        self.assertEqual(rows[(("r1", 1), ("r2", 1))]["sim_satisfied"], 7)
        self.assertEqual(rows[(("r1", 2),)]["sim_satisfied"], 6)
        self.assertTrue(all(r["verified"] for r in c["rows"]))     # M07 pass(실패 검사 0)
        s = c["summary"]
        self.assertEqual((s["plans"], s["mae"], s["distance_mismatch_plans"], s["sim_best_in_est_top3"]),
                         (10, 0.0, 0, True))

    def test_match_condition_without_capacity_contention(self):
        """§7.3 보정 1단계: 1인당 정차 0·고정 구간 시간·정원 여유면 est_served == sim_satisfied."""
        x = pc.with_capacity(self.x, 100)
        x = replace(x, policy=replace(x.policy, dwell_s=5, turnaround_s=30, end_s=900),
                    fleet=tuple(replace(v, available_end_s=900) for v in x.fleet),
                    demands=x.demands + (Demand("d6", "B", "D", 400, 4, "r2"), Demand("d7", "A", "C", 600, 2, "r1")))
        plans = pb.comparison_plans(x)
        c = pb.compare_with_estimate(x, plans, validate=False)
        for r in c["rows"]:
            with self.subTest(allocation=r["allocation"]):
                self.assertEqual(r["sim_satisfied"], r["est_served"])
                self.assertEqual(r["sim_total_m"], r["est_distance_m"])
                self.assertEqual(r["sim_mean_wait_s"], r["est_mean_wait_s"])

    def test_spearman(self):
        self.assertEqual(pb.spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertEqual(pb.spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(pb.spearman([1, 1], [2, 3]))
        self.assertIsNone(pb.spearman([1], [1]))
        self.assertEqual(pb.spearman([1, 2, 2, 3], [1, 2, 3, 4]), 0.948683)

    def test_cli_writes_reports_deterministically(self):
        outs = []
        with tempfile.TemporaryDirectory() as d:
            for name in ("a", "b"):
                out = Path(d) / name
                with mock.patch("sys.stdout"):
                    self.assertEqual(pb.main(["--input", str(pc.FIXTURE), "--out", str(out)]), 0)
                outs.append({n: (out / n).read_bytes() for n in ("curves.json", "comparison.json", "report.md")})
                self.assertEqual(len(list((out / "runs").iterdir())), 10)
            with mock.patch("sys.stderr"):
                self.assertEqual(pb.main(["--input", str(Path(d) / "missing.json"), "--out", str(Path(d) / "c")]), 2)
        self.assertEqual(outs[0], outs[1])
        self.assertIn("## 노선별 대수 곡선 S(n)", outs[0]["report.md"].decode("utf-8"))


class GuroScaleTimingTests(unittest.TestCase):
    """구로 규모 합성 입력(노선 30·차량 60·승객 약 4.8만)으로 1회 실행 시간 측정. 실패 기준이 아니라 측정만."""

    def test_one_run(self):
        x = pc.guro_scale()
        plan = baseline(x)
        t0 = time.perf_counter()
        bridge = pb.to_sim_inputs(x)
        t1 = time.perf_counter()
        est = evaluate(x, plan)
        t2 = time.perf_counter()
        with tempfile.TemporaryDirectory() as d:
            br = pb.run_plan(plan, x, Path(d), validate=False, bridge=bridge)
            t3 = time.perf_counter()
            report = verify(Path(d) / "run").payload
            t4 = time.perf_counter()
        m1 = br.metrics["M1"]
        sys.stderr.write(
            f"\n[bridge-speed] passengers={m1['total']} vehicles={len(x.fleet)} routes={len(x.routes)} "
            f"events={br.run['event_count']} | adapter={t1 - t0:.2f}s C_evaluate={t2 - t1:.2f}s "
            f"run_plan(files+run)={t3 - t2:.2f}s M07_verify={t4 - t3:.2f}s total={t1 - t0 + t4 - t2:.2f}s "
            f"| est_served={est.served} sim_served={m1['served']} not_run={report['not_run_checks']} "
            f"python={sys.version.split()[0]}\n")
        self.assertEqual(m1["total"], x.total_demand)
        self.assertTrue(report["pass"], report["failed_checks"])
        self.assertEqual(report["failed_checks"], [])
        # N > 10,000이면 재계산 oracle 검사(V6·V7·V9–V14)는 not_run이 정상(검증기 명세 §4)
        self.assertEqual(report["not_run_checks"], ["V6", "V7", "V9", "V10", "V11", "V12", "V13", "V14"])
        self.assertEqual(pb.total_distance_m(br.metrics), est.distance_m)
        self.assertLess(t3 - t2, 120.0)

    @unittest.skipUnless(os.environ.get("BUSSTRIKE_SCALE_CURVES"), "BUSSTRIKE_SCALE_CURVES=1일 때만(약 3분)")
    def test_all_route_curves(self):
        x = pc.guro_scale()
        bridge = pb.to_sim_inputs(x)
        ctx = pb.sim_context(bridge)
        from busstrike.sim.curve import fleet_curve
        t0 = time.perf_counter()
        for r in x.routes:
            fleet_curve(pb.route_spec(x, r, 0), 15, ctx)
        t1 = time.perf_counter()
        cr = pb.curve_report(x, n_max=15, bridge=bridge)
        t2 = time.perf_counter()
        sys.stderr.write(f"\n[bridge-curves] routes={len(x.routes)} n=0..15 sim_only={t1 - t0:.2f}s "
                         f"with_C_estimate={t2 - t1:.2f}s\n")
        self.assertEqual(len(cr["routes"]), len(x.routes))


if __name__ == "__main__":
    unittest.main()
