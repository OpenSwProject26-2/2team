"""손계산 시나리오 T1–T4(simulator.md §9.2–§9.5). 표가 기준이고 로그 SHA-256은 참고값이다."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from busstrike.sim import compute_metrics, normalize_plan, prepare, run, simulate
from busstrike.sim.eventlog import dumps_events

from tests.sim import cases


def run_case(case):
    with tempfile.TemporaryDirectory() as d:
        paths = case.write(Path(d) / "in")
        out = Path(d) / "out"
        result = run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], out)
        log = (out / "events.jsonl").read_bytes()
        metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        timetable = json.loads((out / "timetable.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in log.decode("utf-8").splitlines()]
    return log, events, metrics, timetable, result


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def of_type(events, typ):
    return [e for e in events if e["type"] == typ]


def outcomes(events):
    """passenger_id → (결과, t, cause)."""
    out = {}
    for e in events:
        if e["type"] in ("alight", "abandon", "no_service", "left_waiting"):
            out[e["passenger_id"]] = (e["type"], e["t"], e.get("cause"))
    return out


def vehicle_track(events, vehicle_id):
    """(visit, trip_id, stop_id, arrive.t, alighted, boarded, depart.t|None)."""
    arr = {e["visit"]: e for e in events if e["type"] == "arrive" and e["vehicle_id"] == vehicle_id}
    dep = {e["visit"]: e["t"] for e in events if e["type"] == "depart" and e["vehicle_id"] == vehicle_id}
    return [(v, a["trip_id"], a["stop_id"], a["t"], a["alighted"], a["boarded"], dep.get(v))
            for v, a in sorted(arr.items())]


class T1BasicFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log, cls.events, cls.metrics, cls.timetable, cls.result = run_case(cases.t1())

    def test_full_log_bytes(self):
        expected = cases.t1_expected_log()
        self.assertEqual(self.log, expected)
        self.assertEqual(len(self.events), 29)
        self.assertEqual(len(self.log), 4093)
        self.assertEqual(sha(self.log), "796c7b6a88ec8a1379a1e80ebe3726b7e3b20e20cdc0ccde4e0b504ad3b05a78")

    def test_timetable_table(self):
        trips = {t["trip_id"]: (t["in_service"], [(s["stop_id"], s["sched_s"]) for s in t["stop_times"]])
                 for t in self.timetable["trips"]}
        self.assertEqual(trips, {
            "R1-V1-000-0": (True, [("A", 0), ("B", 130), ("S", 320)]),
            "R1-V1-000-1": (False, [("S", 380), ("B", 560), ("A", 680)]),
            "R1-V1-001-0": (True, [("A", 680), ("B", 810), ("S", 1000)]),
            "R1-V1-001-1": (False, [("S", 1060), ("B", 1240), ("A", 1360)]),
        })

    def test_vehicle_progress_table(self):
        self.assertEqual(vehicle_track(self.events, "V1"), [
            (0, "R1-V1-000-0", "A", 0, 0, 2, 14),
            (1, "R1-V1-000-0", "B", 134, 1, 1, 147),
            (2, "R1-V1-000-0", "S", 327, 2, 0, None),
            (3, "R1-V1-000-1", "S", 389, 0, 0, 389),
            (4, "R1-V1-000-1", "B", 569, 0, 0, 569),
            (5, "R1-V1-000-1", "A", 689, 0, 0, None),
            (6, "R1-V1-001-0", "A", 689, 0, 0, 699),
            (7, "R1-V1-001-0", "B", 819, 0, 1, 831),
            (8, "R1-V1-001-0", "S", 1011, 1, 0, None),
            (9, "R1-V1-001-1", "S", 1072, 0, 0, 1072),
            (10, "R1-V1-001-1", "B", 1252, 0, 0, 1252),
            (11, "R1-V1-001-1", "A", 1372, 0, 0, None),
        ])

    def test_passenger_table(self):
        self.assertEqual(outcomes(self.events), {
            "p1": ("alight", 327, None), "p2": ("alight", 134, None), "p3": ("abandon", 605, "WAIT_EXCEEDED"),
            "p4": ("alight", 327, None), "p5": ("alight", 1011, None),
        })
        boards = {e["passenger_id"]: e["t"] for e in of_type(self.events, "board")}
        self.assertEqual(boards, {"p1": 0, "p2": 0, "p4": 134, "p5": 819})

    def test_metrics(self):
        m = self.metrics
        p = m["passengers"]
        self.assertEqual((p["generated"], p["transported"], p["satisfied"], p["abandoned"], p["no_service"],
                          p["left_waiting"]), (5, 4, 4, 1, 0, 0))
        self.assertEqual((p["transport_rate"], p["satisfaction_rate"]), (0.8, 0.8))
        self.assertEqual(m["wait_s"], {"count": 4, "mean": 38.25, "p50": 0, "p90": 119, "p95": 119, "max": 119})
        self.assertEqual(m["in_vehicle_s"]["mean"], 211.5)
        self.assertEqual((m["journey_s"]["mean"], m["journey_s"]["p90"]), (249.75, 327))
        v = m["vehicles"][0]
        self.assertEqual((v["vehicle_id"], v["revenue_m"], v["deadhead_m"], v["first_s"], v["last_s"]),
                         ("V1", 5000, 5000, 0, 1372))
        self.assertEqual((v["cycles_run"], v["cycles_cancelled"], v["boardings"]), (2, 0, 4))
        trips = {t["trip_id"]: t for t in m["trips"]}
        self.assertEqual(set(trips), {"R1-V1-000-0", "R1-V1-001-0"})
        t0, t1 = trips["R1-V1-000-0"], trips["R1-V1-001-0"]
        self.assertEqual((t0["boardings"], t0["max_load"], t0["load_factor_seat"], t0["start_delay_s"]),
                         (3, 2, 0.666667, 0))
        self.assertEqual((t1["boardings"], t1["max_load"], t1["load_factor_seat"], t1["start_delay_s"]),
                         (1, 1, 0.333333, 9))
        self.assertEqual(m["denied"], {"boardings": 0, "passengers": 0})
        self.assertEqual(m["unserved"]["by_cause"]["WAIT_EXCEEDED"], 1)
        self.assertEqual(m["unserved"]["by_stop"], [{"stop_id": "A", "cause": "WAIT_EXCEEDED", "count": 1}])
        self.assertEqual(m["fleet"]["total_m"], 10000)
        self.assertEqual(m["routes"][0]["satisfied"], 4)

    def test_run_record(self):
        r = self.result
        self.assertEqual(r["event_log_sha256"], sha(cases.t1_expected_log()))
        self.assertEqual(r["timetable_origin"], "generated")
        self.assertTrue(r["run_id"].startswith("run-") and len(r["run_id"]) == 24)
        self.assertEqual((r["plan_id"], r["plan_revision"], r["input_bundle_id"]), ("plan-1", 1, "bundle-1"))


class T2CapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log, cls.events, cls.metrics, _, _ = run_case(cases.t2())

    def test_vehicle_table(self):
        track = vehicle_track(self.events, "V1")
        self.assertEqual([(a[2], a[3], a[6]) for a in track], [
            ("A", 0, 14), ("B", 134, 144), ("S", 324, None),
            ("S", 386, 386), ("B", 566, 566), ("A", 686, None),
            ("A", 686, 698), ("B", 818, 830), ("S", 1010, None),
            ("S", 1072, 1072), ("B", 1252, 1252), ("A", 1372, None),
        ])
        left = [(e["visit"], e["left_behind"]) for e in of_type(self.events, "arrive") if e["left_behind"]]
        self.assertEqual(left, [(0, 1), (1, 2), (7, 1)])

    def test_denied_first_only(self):
        self.assertEqual([(e["visit"], e["passenger_id"]) for e in of_type(self.events, "denied")],
                         [(0, "q3"), (1, "q4"), (1, "q5")])

    def test_passengers(self):
        self.assertEqual(outcomes(self.events), {
            "q1": ("alight", 324, None), "q2": ("alight", 324, None), "q3": ("alight", 1010, None),
            "q4": ("alight", 1010, None), "q5": ("abandon", 1000, "FULL"),
        })
        boards = {e["passenger_id"]: e["t"] for e in of_type(self.events, "board")}
        self.assertEqual(boards, {"q1": 0, "q2": 0, "q3": 686, "q4": 818})

    def test_metrics(self):
        m = self.metrics
        p = m["passengers"]
        self.assertEqual((p["generated"], p["transported"], p["satisfied"], p["abandoned"]), (5, 4, 3, 1))
        self.assertEqual(m["denied"], {"boardings": 4, "passengers": 3})
        self.assertEqual((m["wait_s"]["mean"], m["wait_s"]["p90"]), (351.0, 718))
        self.assertEqual(m["unserved"]["by_cause"]["JOURNEY_EXCEEDED"], 1)
        self.assertEqual(m["unserved"]["by_cause"]["FULL"], 1)

    def test_log_reference(self):
        self.assertEqual(len(self.events), 32)
        self.assertEqual(sha(self.log), "167c8484a4b4cc79fffc88203410c1c9d9f66b95309814ccbc51eb3afe50a4f0")


class T2bCancelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log, cls.events, cls.metrics, cls.timetable, _ = run_case(cases.t2(end_s=1370))

    def test_timetable_has_cycle_1(self):
        self.assertIn("R1-V1-001-0", [t["trip_id"] for t in self.timetable["trips"]])

    def test_cancel_event(self):
        cancels = of_type(self.events, "cancel")
        self.assertEqual(cancels, [{"seq": 17, "t": 686, "type": "cancel", "vehicle_id": "V1",
                                    "trip_id": "R1-V1-001-0", "visit": 6, "cause": "LATE"}])

    def test_outcomes_and_metrics(self):
        o = outcomes(self.events)
        self.assertEqual(o["q3"], ("abandon", 900, "FULL"))
        self.assertEqual(o["q4"], ("abandon", 1000, "FULL"))
        self.assertEqual(o["q5"], ("abandon", 1000, "FULL"))
        p = self.metrics["passengers"]
        self.assertEqual((p["transported"], p["satisfied"], p["abandoned"]), (2, 2, 3))
        v = self.metrics["vehicles"][0]
        self.assertEqual((v["last_s"], v["revenue_m"], v["deadhead_m"], v["cycles_cancelled"]), (686, 2500, 2500, 1))

    def test_log_reference(self):
        self.assertEqual(len(self.events), 21)
        self.assertEqual(sha(self.log), "fbcf73f559c6967af8e082d01701bc7d80cf26633e7d828b253bc8c8e86674e9")


class T3SharedStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log, cls.events, cls.metrics, cls.timetable, _ = run_case(cases.t3())

    def test_timetable(self):
        self.assertEqual({r["route_id"]: (r["period_s"], r["vehicle_ids"]) for r in self.timetable["routes"]},
                         {"R1": (430, ["V1"]), "R2": (270, ["V2"])})
        self.assertEqual(sorted({(t["route_id"], t["cycle_index"]) for t in self.timetable["trips"]}),
                         [("R1", 0), ("R2", 0)])

    def test_t0_order(self):
        head = [(e["type"], e.get("vehicle_id"), e.get("passenger_id")) for e in self.events if e["t"] == 0]
        self.assertEqual(head, [
            ("no_service", None, "w3"), ("no_service", None, "w4"), ("no_service", None, "w5"),
            ("arrive", "V1", None), ("board", "V1", "w1"), ("denied", "V1", "w2"),
            ("arrive", "V2", None), ("board", "V2", "w2"),
        ])
        arrives = [(e["vehicle_id"], e["boarded"], e["left_behind"]) for e in of_type(self.events, "arrive")
                   if e["t"] == 0]
        self.assertEqual(arrives, [("V1", 1, 1), ("V2", 1, 0)])
        at12 = [(e["type"], e["vehicle_id"]) for e in self.events if e["t"] == 12]
        self.assertEqual(at12, [("depart", "V1"), ("depart", "V2")])

    def test_outcomes(self):
        self.assertEqual(outcomes(self.events), {
            "w1": ("alight", 192, None), "w2": ("alight", 112, None),
            "w3": ("no_service", 0, "NO_ROUTE_TO_DEST"), "w4": ("no_service", 0, "NO_ROUTE_AT_STOP"),
            "w5": ("no_service", 0, "NO_ROUTE_AT_STOP"),
        })
        alights = {e["passenger_id"]: e["stop_id"] for e in of_type(self.events, "alight")}
        self.assertEqual(alights, {"w1": "S1", "w2": "S2"})

    def test_vehicles(self):
        self.assertEqual([(a[2], a[3], a[6]) for a in vehicle_track(self.events, "V1")],
                         [("B", 0, 12), ("S1", 192, None), ("S1", 253, 253), ("B", 433, None)])
        self.assertEqual([(a[2], a[3], a[6]) for a in vehicle_track(self.events, "V2")],
                         [("B", 0, 12), ("S2", 112, None), ("S2", 173, 173), ("B", 273, None)])

    def test_metrics(self):
        m = self.metrics
        p = m["passengers"]
        self.assertEqual((p["generated"], p["transported"], p["satisfied"], p["no_service"]), (5, 2, 2, 3))
        self.assertEqual((m["unserved"]["by_cause"]["NO_ROUTE_AT_STOP"], m["unserved"]["by_cause"]["NO_ROUTE_TO_DEST"]),
                         (2, 1))
        self.assertEqual((m["wait_s"]["count"], m["wait_s"]["max"]), (2, 0))
        self.assertEqual(m["denied"], {"boardings": 1, "passengers": 1})
        v = {r["vehicle_id"]: (r["revenue_m"], r["deadhead_m"]) for r in m["vehicles"]}
        self.assertEqual(v, {"V1": (1500, 1500), "V2": (800, 800)})

    def test_log_reference(self):
        self.assertEqual(len(self.events), 20)
        self.assertEqual(sha(self.log), "caee12748b596343cdd1d9e1cacfa8f702e17e5878467da7f0d0da6c025b662d")

    def test_disjoint_routes_add_up(self):
        """§7.2·§9.4: 목적지를 {S1}, {S2}로 나누면 노선별 단독 실행 합 = 함께 실행."""
        case = cases.t3(w1_dest=("S1",), w2_dest=("S2",))
        sc, tt, plan, pas = case.objects()

        def satisfied(routes, passengers):
            from dataclasses import replace
            p = replace(plan, routes=tuple(r for r in plan.routes if r.route_id in routes))
            timetable = normalize_plan(p, sc.vehicles, sc.window, sc.policy, tt)
            ctx = prepare(passengers, sc.vehicles, sc.window, sc.policy, tt)
            events = simulate(ctx, timetable)
            return compute_metrics(events, ctx.passengers, timetable, sc.vehicles, sc.policy, tt)["passengers"]["satisfied"]

        joint = satisfied({"R1", "R2"}, pas)
        alone = satisfied({"R1"}, pas) + satisfied({"R2"}, pas)
        self.assertEqual(joint, 2)
        self.assertEqual(alone, joint)


class T4TimeBinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log, cls.events, cls.metrics, cls.timetable, _ = run_case(cases.t4())

    def test_timetable(self):
        self.assertEqual(self.timetable["routes"][0]["period_s"], 800)
        self.assertEqual([[s["sched_s"] for s in t["stop_times"]] for t in self.timetable["trips"]],
                         [[0, 130, 320], [380, 560, 680], [800, 1050, 1240], [1300, 1480, 1600]])

    def test_vehicle(self):
        self.assertEqual([(a[2], a[3], a[6]) for a in vehicle_track(self.events, "V1")], [
            ("A", 0, 10), ("B", 130, 140), ("S", 320, None), ("S", 380, 380), ("B", 560, 560), ("A", 680, None),
            ("A", 800, 812), ("B", 1052, 1062), ("S", 1242, None), ("S", 1303, 1303), ("B", 1483, 1483),
            ("A", 1603, None),
        ])

    def test_passengers(self):
        self.assertEqual(outcomes(self.events), {
            "x1": ("alight", 1242, None), "x2": ("abandon", 1401, "WAIT_EXCEEDED"),
            "x3": ("left_waiting", 1700, "SERVICE_ENDED"),
        })
        m = self.metrics
        p = m["passengers"]
        self.assertEqual((p["generated"], p["transported"], p["satisfied"], p["abandoned"], p["left_waiting"]),
                         (3, 1, 1, 1, 1))
        self.assertEqual((m["wait_s"]["max"], m["in_vehicle_s"]["max"], m["journey_s"]["max"]), (10, 442, 452))

    def test_log_reference(self):
        self.assertEqual(len(self.events), 24)
        self.assertEqual(sha(self.log), "4de62ffa4f405121a74f4b3891239d474cf049c519e01b43a9f669806ab1651f")


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

    def test_tampered_arrivals_rejected(self):
        def tamper(paths):
            data = paths["arrivals.jsonl"].read_bytes().replace(b'"arrival_s":5', b'"arrival_s":6')
            paths["arrivals.jsonl"].write_bytes(data)
        e = self.run_expect(cases.t1(), "INPUT_INVALID", tamper)
        self.assertEqual(e.details["field"], "arrival_meta.sha256")

    def test_transfers_out_of_scope(self):
        case = cases.t1()
        case.scenario["policy"]["allowed_transfers"] = 1
        self.run_expect(case, "OUT_OF_SCOPE")

    def test_plan_for_other_scenario(self):
        case = cases.t1()
        case.plan["scenario_id"] = "other"
        self.run_expect(case, "INPUT_INVALID")

    def test_arrival_outside_window(self):
        case = cases.t1()
        case.passengers[0]["arrival_s"] = 1501
        self.run_expect(case, "INPUT_INVALID")

    def test_origin_dest_overlap(self):
        case = cases.t1()
        case.passengers[0]["dest_stop_ids"] = ["A", "S"]
        from busstrike.sim import SimError
        with self.assertRaises(SimError) as cm:
            case.objects()
        self.assertEqual(cm.exception.code, "INPUT_INVALID")

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
            self.assertEqual((Path(d) / "out" / "events.jsonl").read_bytes(), cases.t1_expected_log())
            paths["plan.json"].write_text('{"plan_id": "x"}', encoding="utf-8")
            bad = subprocess.run(args + ["--out", str(Path(d) / "out2")], env=env, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("INPUT_INVALID", bad.stderr)


class InMemoryEqualsFileRunTests(unittest.TestCase):
    def test_t1_in_memory(self):
        sc, tt, plan, pas = cases.t1().objects()
        timetable = normalize_plan(plan, sc.vehicles, sc.window, sc.policy, tt)
        events = simulate(prepare(pas, sc.vehicles, sc.window, sc.policy, tt), timetable)
        self.assertEqual(dumps_events(events), cases.t1_expected_log())


if __name__ == "__main__":
    unittest.main()
