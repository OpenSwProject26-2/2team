"""손계산 시나리오 T1–T4(simulator.md §9.2–§9.5, M07 의미). 표가 기준이고 로그 SHA-256은 참고값이다.

모든 사례에서 출력 폴더가 M07 검증기(`busstrike.evaluation.verify`)를 통과하는지도 함께 본다.
"""

import csv
import hashlib
import io as _io
import json
import tempfile
import unittest
from pathlib import Path

from busstrike.evaluation.verify import main as verify_main
from busstrike.sim import normalize_plan, prepare, run, service_schedule, simulate
from busstrike.sim.eventlog import dumps_events

from tests.sim import cases
from tests.sim.cases import pid


class Out:
    """run() 한 번의 M07 폴더 내용."""

    def __init__(self, case, verify=True):
        with tempfile.TemporaryDirectory() as d:
            paths = case.write(Path(d) / "in")
            out = Path(d) / "out"
            self.measurement = run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], out)
            self.log = (out / "run" / "events.jsonl").read_bytes()
            self.metrics = json.loads((out / "run" / "metrics.json").read_text(encoding="utf-8"))
            self.timetable = json.loads((out / "run" / "timetable.json").read_text(encoding="utf-8"))
            self.network = json.loads((out / "scenario" / "network.json").read_text(encoding="utf-8"))
            self.outcomes = {r["passenger_id"]: r for r in csv.DictReader(_io.StringIO(
                (out / "run" / "passenger_outcomes.csv").read_text(encoding="utf-8")))}
            self.exit_code = None
            if verify:
                self.exit_code = verify_main([str(out / "run"), "--out", str(Path(d) / "report.json"), "--quiet"])
                self.report = json.loads((Path(d) / "report.json").read_text(encoding="utf-8"))
        self.events = [json.loads(line) for line in self.log.decode("utf-8").splitlines()]

    def trips(self):
        return {t["trip_id"]: (t["stops"], t["arrivals"], t["departures"], t["return_s"], t["distance_m"])
                for t in self.network["trips"]}

    def row(self, passenger):
        """(outcome, reason, physical, boarding_s, arrival_s, refused). 빈 칸은 None."""
        r = self.outcomes[pid(passenger)]
        num = lambda x: None if x == "" else int(x)
        return (r["outcome"], r["reason"] or None, r["physical"], num(r["boarding_s"]), num(r["arrival_s"]),
                int(r["refused"]))

    def vehicle(self, vehicle_id):
        return next(v for v in self.metrics["M6"]["vehicles"] if v["vehicle_id"] == vehicle_id)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class VerifierMixin:
    def assert_m07_pass(self, out):
        self.assertEqual(out.exit_code, 0, out.report["failed_checks"])
        self.assertTrue(out.report["pass"])
        self.assertEqual(out.report["failed_checks"], [])
        self.assertIn("V16", out.report["not_verified_checks"])       # V16은 늘 미검증(통과 아님)


class T1BasicFlowTests(VerifierMixin, unittest.TestCase):
    """§9.2 T1. 정차 10초 고정, 회차 60초, P = 680."""

    @classmethod
    def setUpClass(cls):
        cls.out = Out(cases.t1())

    def test_m07_verifier_passes(self):
        self.assert_m07_pass(self.out)

    def test_full_log_bytes(self):
        self.assertEqual(self.out.log, cases.t1_expected_log())
        self.assertEqual(len(self.out.events), 26)

    def test_schedule_table(self):
        self.assertEqual(self.out.trips(), {
            "R1-V1-000-0": (["A", "B", "S"], [0, 130, 320], [10, 140, 320], 680, 5000),
            "R1-V1-001-0": (["A", "B", "S"], [680, 810, 1000], [690, 820, 1000], 1360, 5000),
        })
        self.assertEqual(self.out.network["route_of"], {"R1-V1-000-0": "R1", "R1-V1-001-0": "R1"})
        self.assertEqual(self.out.network["spec"]["capacity"], 3)

    def test_passenger_table(self):
        o = self.out
        self.assertEqual(o.row("p1"), ("served", None, "arrived", 0, 320, 0))
        self.assertEqual(o.row("p2"), ("served", None, "arrived", 0, 130, 0))     # 중간 하차
        self.assertEqual(o.row("p3"), ("unserved", "wait", "waiting", None, None, 0))  # 5초 늦어 놓침, 605 만료
        self.assertEqual(o.row("p4"), ("served", None, "arrived", 130, 320, 0))
        self.assertEqual(o.row("p5"), ("served", None, "arrived", 810, 1000, 0))

    def test_expiry_event(self):
        expired = [e for e in self.out.events if e["kind"] == "expired"]
        self.assertEqual(expired, [{"time_s": 605, "phase": 4, "stable_id": "wait:" + pid("p3"), "kind": "expired",
                                    "passenger_id": pid("p3"), "vehicle_id": None, "occupancy": None}])

    def test_metrics(self):
        m = self.out.metrics
        self.assertEqual({k: m["M1"][k] for k in ("served", "unserved", "censored", "total", "rate")},
                         {"served": 4, "unserved": 1, "censored": 0, "total": 5, "rate": 0.8})
        self.assertEqual(m["M2"]["unserved_by_reason"], {"no_connection": 0, "service_end": 0, "journey": 0, "wait": 1})
        self.assertEqual(m["M2"]["refusal_events"], 0)
        b = m["M3"]["boarded"]
        self.assertEqual((b["n"], b["mean_s"], b["p90_s"], b["over_threshold_n"], b["over_threshold_rate"]),
                         (4, 35.0, 110, 1, 0.25))        # 대기 [0, 0, 30, 110], 임계 60
        self.assertEqual(m["M3"]["not_boarded_unserved_by_reason"]["wait"], 1)
        self.assertEqual(self.out.vehicle("V1"), {
            "vehicle_id": "V1", "trips_completed": 2, "boarded": 4, "served_alighted": 4,
            "service_time_s": 1360, "distance_m": 10000,
            "empty_time_s": 840})    # 회송 360+360 + 탑승 0으로 출발한 001-0 A→B 120
        r12 = m["G"]["R12"]
        self.assertEqual((r12["max_gap_by_route_s"], r12["violation_count"], r12["pass"]), ({"R1": 680}, 0, True))
        self.assertEqual(m["M5"]["by_origin_area"]["unknown"]["total"], 5)

    def test_measurement_record(self):
        r = self.out.measurement
        self.assertEqual((r["plan_id"], r["plan_revision"], r["input_bundle_id"]), ("plan-1", 1, "bundle-1"))
        self.assertEqual(r["config"], {"max_wait_s": 600, "max_journey_s": 1200, "drain_s": 2700})
        self.assertEqual(r["drain_rule"], "operating_window.end_s + max_journey_s")
        self.assertEqual(r["log_sha256"], sha(self.out.log))
        self.assertTrue(r["run_id"].startswith("run-"))
        self.assertRegex(r["env"]["commit"] or "", r"^[0-9a-f]{40}$")     # M07 보고서가 인용하는 엔진 커밋
        self.assertEqual(set(r["env"]), {"commit", "dirty", "sim_version", "note"})   # 시각·호스트·경로 없음


class T2CapacityTests(VerifierMixin, unittest.TestCase):
    """§9.3 T2: 좌석 2, W=900, J=1000. 여정 기한 당일(1000초) 하차는 served(경계 포함)."""

    @classmethod
    def setUpClass(cls):
        cls.out = Out(cases.t2())

    def test_m07_verifier_passes(self):
        self.assert_m07_pass(self.out)

    def test_boardings_and_refusals(self):
        boards = [(e["time_s"], e["passenger_id"]) for e in self.out.events if e["kind"] == "boarded"]
        self.assertEqual(boards, [(0, pid("q1")), (0, pid("q2")), (680, pid("q3")), (810, pid("q4"))])
        self.assertEqual(self.out.metrics["M2"]["refusal_events"], 4)   # A@0 q3, B@130 q4·q5, B@810 q5

    def test_passengers(self):
        o = self.out
        self.assertEqual(o.row("q1"), ("served", None, "arrived", 0, 320, 0))
        self.assertEqual(o.row("q2"), ("served", None, "arrived", 0, 320, 0))
        self.assertEqual(o.row("q3"), ("served", None, "arrived", 680, 1000, 1))   # 1000 = 0 + J
        self.assertEqual(o.row("q4"), ("served", None, "arrived", 810, 1000, 1))
        self.assertEqual(o.row("q5"), ("unserved", "wait", "waiting", None, None, 2))  # 100 + 900 = 1000 만료

    def test_metrics(self):
        m = self.out.metrics
        self.assertEqual((m["M1"]["served"], m["M1"]["unserved"]), (4, 1))
        self.assertEqual(m["M2"]["passengers_refused_by_outcome"], {"served": 2, "unserved": 1, "censored": 0})
        b = m["M3"]["boarded"]
        self.assertEqual((b["n"], b["mean_s"], b["p90_s"], b["over_threshold_n"], b["over_threshold_rate"]),
                         (4, 347.5, 710, 2, 0.5))
        self.assertEqual(self.out.vehicle("V1")["empty_time_s"], 720)   # 빈 차로 출발한 구간 없음


class T2JourneyDeadlineTests(VerifierMixin, unittest.TestCase):
    """T2b(새로 만듦): J=999로 하나 줄이면 q3는 차내에서 999에 만료되고 1000 하차는 남는다."""

    @classmethod
    def setUpClass(cls):
        cls.out = Out(cases.t2(max_journey_s=999))

    def test_m07_verifier_passes(self):
        self.assert_m07_pass(self.out)

    def test_onboard_expiry(self):
        expired = [e for e in self.out.events if e["kind"] == "expired"]
        self.assertEqual([(e["time_s"], e["stable_id"], e["vehicle_id"], e["occupancy"]) for e in expired],
                         [(999, "journey:" + pid("q3"), "V1", 2), (1000, "wait:" + pid("q5"), None, None)])
        self.assertEqual(self.out.row("q3"), ("unserved", "journey", "arrived", 680, 1000, 1))
        self.assertEqual(self.out.row("q4"), ("served", None, "arrived", 810, 1000, 1))

    def test_metrics(self):
        m = self.out.metrics
        self.assertEqual((m["M1"]["served"], m["M1"]["unserved"]), (3, 2))
        self.assertEqual(m["M2"]["unserved_by_reason"], {"no_connection": 0, "service_end": 0, "journey": 1, "wait": 1})
        self.assertEqual(m["M2"]["passengers_refused_by_outcome"], {"served": 1, "unserved": 2, "censored": 0})
        self.assertEqual((self.out.vehicle("V1")["boarded"], self.out.vehicle("V1")["served_alighted"]), (4, 3))


class T3SharedStopTests(VerifierMixin, unittest.TestCase):
    """§9.4 T3: 같은 초 같은 정류장의 두 차량은 stable_id(trip_id) 순서로 태운다."""

    @classmethod
    def setUpClass(cls):
        cls.out = Out(cases.t3())

    def test_m07_verifier_passes(self):
        self.assert_m07_pass(self.out)

    def test_schedule(self):
        self.assertEqual(self.out.trips(), {
            "R1-V1-000-0": (["B", "S1"], [0, 190], [10, 190], 430, 3000),
            "R2-V2-000-0": (["B", "S2"], [0, 110], [10, 110], 270, 1600),
        })

    def test_t0_order(self):
        t0 = [(e["phase"], e["kind"], e["stable_id"], e["passenger_id"]) for e in self.out.events if e["time_s"] == 0]
        self.assertEqual(t0, [
            (0, "vehicle_arrived", "R1-V1-000-0:00000000", None),
            (0, "vehicle_arrived", "R2-V2-000-0:00000000", None),
        ] + [(1, "generated", "generate:" + pid(w), pid(w)) for w in ("w1", "w2", "w3", "w4", "w5", "w6")] + [
            (2, "boarded", "R1-V1-000-0:00000000", pid("w3")),   # 줄 앞 w1·w2는 S1에 안 가므로 건너뜀
            (2, "boarded", "R2-V2-000-0:00000000", pid("w1")),
        ])

    def test_outcomes(self):
        o = self.out
        self.assertEqual(o.row("w1"), ("served", None, "arrived", 0, 110, 0))
        self.assertEqual(o.row("w2"), ("unserved", "wait", "waiting", None, None, 1))   # R2 만석, 600 만료
        self.assertEqual(o.row("w3"), ("served", None, "arrived", 0, 190, 0))
        for w in ("w4", "w5", "w6"):     # B→A, X→S1, S1→B: 출발지 뒤에 목적지가 오는 운행편이 없다
            self.assertEqual(o.row(w), ("unserved", "no_connection", "waiting", None, None, 0), w)

    def test_metrics(self):
        m = self.out.metrics
        self.assertEqual(m["M2"]["unserved_by_reason"], {"no_connection": 3, "service_end": 0, "journey": 0, "wait": 1})
        self.assertEqual(self.out.vehicle("V1"), {"vehicle_id": "V1", "trips_completed": 1, "boarded": 1,
                                                  "served_alighted": 1, "service_time_s": 430, "distance_m": 3000,
                                                  "empty_time_s": 240})
        self.assertEqual(self.out.vehicle("V2")["empty_time_s"], 160)
        self.assertNotIn("G", m)          # max_headway_s를 주지 않으면 R12를 싣지 않는다(V15 미검증)

    def test_disjoint_routes_add_up(self):
        """§7.2: 공통 승객이 없는 노선은 단독 실행 합 = 함께 실행."""
        case = cases.t3(w1_dest="S1", w2_dest="S2", w3_dest="S1")
        both = Out(case, verify=False).metrics["M1"]["served"]
        alone = 0
        for keep in ("R1", "R2"):
            c = cases.t3(w1_dest="S1", w2_dest="S2", w3_dest="S1")
            c.plan["routes"] = [r for r in c.plan["routes"] if r["route_id"] == keep]
            alone += Out(c, verify=False).metrics["M1"]["served"]
        self.assertEqual(both, alone)
        self.assertEqual(both, 2)     # w1(R1)·w2(R2). w3는 R1 정원 1에 밀림


class T4TimeBinTests(VerifierMixin, unittest.TestCase):
    """§9.5 T4: A→B 600초부터 240초, P = 800. 마지막 방문 뒤 승객은 service_end."""

    @classmethod
    def setUpClass(cls):
        cls.out = Out(cases.t4())

    def test_m07_verifier_passes(self):
        self.assert_m07_pass(self.out)

    def test_schedule(self):
        self.assertEqual(self.out.trips(), {
            "R1-V1-000-0": (["A", "B", "S"], [0, 130, 320], [10, 140, 320], 680, 5000),
            "R1-V1-001-0": (["A", "B", "S"], [800, 1050, 1240], [810, 1060, 1240], 1600, 5000),
        })

    def test_passengers(self):
        o = self.out
        self.assertEqual(o.row("x1"), ("served", None, "arrived", 800, 1240, 0))
        self.assertEqual(o.row("x2"), ("unserved", "service_end", "waiting", None, None, 0))   # A 마지막 800 < 801
        self.assertEqual(o.row("x3"), ("unserved", "service_end", "waiting", None, None, 0))   # B 마지막 1050
        expired = [(e["time_s"], e["stable_id"]) for e in o.events if e["kind"] == "expired"]
        self.assertEqual(expired, [(1401, "wait:" + pid("x2")), (2100, "wait:" + pid("x3"))])

    def test_vehicle(self):
        self.assertEqual(self.out.vehicle("V1"), {"vehicle_id": "V1", "trips_completed": 2, "boarded": 1,
                                                  "served_alighted": 1, "service_time_s": 1480, "distance_m": 10000,
                                                  "empty_time_s": 1020})   # 회송 720 + 000-0 빈 차 120 + 180

    def test_log_reference(self):
        self.assertEqual(len(self.out.events), 19)


class RunInputErrorTests(unittest.TestCase):
    def run_expect(self, case, code, mutate=None):
        from busstrike.sim import SimError
        with tempfile.TemporaryDirectory() as d:
            paths = case.write(Path(d) / "in")
            if mutate:
                mutate(paths)
            with self.assertRaises(SimError) as cm:
                run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], Path(d) / "out")
        self.assertEqual(cm.exception.code, code)
        return cm.exception

    def test_tampered_demand_rejected(self):
        def tamper(paths):
            data = paths["demand_bins.csv"].read_bytes().replace(b"p3,A,S,5,", b"p3,A,S,6,")
            paths["demand_bins.csv"].write_bytes(data)
        e = self.run_expect(cases.t1(), "INPUT_INVALID", tamper)
        self.assertEqual(e.details["field"], "arrival_meta.sha256")

    def test_transfers_out_of_scope(self):
        case = cases.t1()
        case.scenario["policy"]["allowed_transfers"] = 1
        self.run_expect(case, "OUT_OF_SCOPE")

    def test_per_passenger_dwell_out_of_scope(self):
        case = cases.t1()
        case.scenario["policy"]["dwell_per_boarding_s"] = 2
        self.run_expect(case, "OUT_OF_SCOPE")

    def test_two_service_legs_out_of_scope(self):
        case = cases.t1()
        case.plan["routes"][0]["legs"] = [{"in_service": True, "stop_ids": ["A", "B", "S"]},
                                          {"in_service": True, "stop_ids": ["S", "B", "A"]}]
        e = self.run_expect(case, "OUT_OF_SCOPE")
        self.assertEqual(e.details["in_service"], [True, True])

    def test_plan_for_other_scenario(self):
        case = cases.t1()
        case.plan["scenario_id"] = "other"
        self.run_expect(case, "INPUT_INVALID")

    def test_arrival_outside_window(self):
        case = cases.t1()
        case.rows[0] = cases.pax("p1", "A", "S", 1501)
        self.run_expect(case, "INPUT_INVALID")

    def test_drain_before_last_arrival(self):
        case = cases.t1()
        case.scenario["policy"]["drain_s"] = 600       # p5가 700에 발생한다
        self.run_expect(case, "INPUT_INVALID")

    def test_origin_equals_destination(self):
        case = cases.t1()
        case.rows[0] = cases.pax("p1", "A", "A", 0)
        with tempfile.TemporaryDirectory() as d:
            from busstrike.sim import SimError
            with self.assertRaises(SimError) as cm:
                case.write(Path(d))
        self.assertEqual(cm.exception.code, "INPUT_INVALID")

    def test_provided_timetable_must_match_travel_times(self):
        sc, tt, plan, _ = cases.t1().objects()
        timetable = normalize_plan(plan, sc.vehicles, sc.window, sc.policy, tt)
        trips = [t for t in timetable.trips]
        case = cases.t1()
        doc = [{"trip_id": t.trip_id, "route_id": t.route_id, "vehicle_id": t.vehicle_id,
                "cycle_index": t.cycle_index, "leg_index": t.leg_index, "in_service": t.in_service,
                "stop_times": [{"stop_seq": s.stop_seq, "stop_id": s.stop_id,
                                "sched_s": s.sched_s + (5 if (t.leg_index, s.stop_seq) == (0, 1) else 0)}
                               for s in t.stop_times]} for t in trips]
        case.plan["trips"] = doc
        case.plan["vehicle_duties"] = [{"vehicle_id": d.vehicle_id, "route_id": d.route_id,
                                        "trip_ids": list(d.trip_ids), "start_s": d.start_s, "end_s": d.end_s}
                                       for d in timetable.duties]
        self.run_expect(case, "PLAN_INFEASIBLE")

    def test_cli_exit_codes(self):
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(root / "src"))
        with tempfile.TemporaryDirectory() as d:
            case = cases.t1()
            paths = case.write(Path(d) / "in")
            args = [sys.executable, "-m", "busstrike.sim", "run", "--scenario", str(paths["scenario.json"]),
                    "--run-inputs", str(paths["run_inputs.json"]), "--plan", str(paths["plan.json"])]
            ok = subprocess.run(args + ["--out", str(Path(d) / "out")], env=env, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual((Path(d) / "out" / "run" / "events.jsonl").read_bytes(), cases.t1_expected_log())
            m07 = subprocess.run([sys.executable, "-m", "busstrike.evaluation.verify", str(Path(d) / "out" / "run"),
                                  "--out", str(Path(d) / "report.json"), "--quiet"],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(m07.returncode, 0, m07.stderr)
            paths["plan.json"].write_text('{"plan_id": "x"}', encoding="utf-8")
            bad = subprocess.run(args + ["--out", str(Path(d) / "out2")], env=env, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("INPUT_INVALID", bad.stderr)


class InMemoryEqualsFileRunTests(unittest.TestCase):
    def test_t1_in_memory(self):
        sc, tt, plan, pas = cases.t1().objects()
        ctx = prepare(pas, sc.vehicles, sc.window, sc.policy, tt)
        timetable = normalize_plan(plan, sc.vehicles, sc.window, sc.policy, tt)
        result = simulate(ctx, service_schedule(timetable, ctx))
        self.assertEqual(dumps_events(result.events), cases.t1_expected_log())


if __name__ == "__main__":
    unittest.main()
