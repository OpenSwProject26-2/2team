"""운영 종료 규칙 policy.service_end_rule(simulator.md §1.3).

- cycle_within_window(기본): 순환 전체가 min(W_e, 가용 종료) 안에 끝나야 한다.
- start_within_window_return_by_available: 순환 시작(첫 영업 leg 출발) < W_e, 순환 전체 ≤ 가용 종료.

합성 노선: 영업 A→S 2400초, 공차 S→A 1880초, 정차 10초, 회차 180초 → 순환 4470초(74.5분).
운영 06:00–10:00, 차량 3대(가용 06:00–11:00) → 배차 1490초.
"""

import json
import tempfile
import unittest
from pathlib import Path

from busstrike.evaluation.verify import main as verify_main
from busstrike.sim import (
    Duty, Leg, RouteSpec, SimError, StopTime, Timetable, TimetableRoute, Trip, TravelTimeTable, Vehicle, Window,
    build_timetable, normalize_plan, run, validate_timetable,
)
from busstrike.sim import io
from busstrike.sim.models import SERVICE_END_CYCLE_WITHIN_WINDOW, SERVICE_END_START_WITHIN_WINDOW

from tests.sim import cases

W_S, W_E, AVAIL_END = 6 * 3600, 10 * 3600, 11 * 3600
CYCLE = 10 + 2400 + 180 + 1880
SEGMENTS = {("A", "S"): (12000, [[0, 2400]]), ("S", "A"): (12000, [[0, 1880]])}
LEGS = [{"in_service": True, "stop_ids": ["A", "S"]}, {"in_service": False, "stop_ids": ["S", "A"]}]


def hms(h, m, s=0):
    return h * 3600 + m * 60 + s


def policy(rule=None):
    raw = dict(cases.F1_POLICY, max_wait_s=1200, max_journey_s=3600, turnaround_s=180)
    if rule is not None:
        raw["service_end_rule"] = rule
    return io.policy_from_dict(raw)


def tt():
    return TravelTimeTable.from_dict(cases.travel_times_dict(SEGMENTS, valid_until_s=12 * 3600))


def route(n=3):
    return RouteSpec("R1", (Leg(True, ("A", "S")), Leg(False, ("S", "A"))), n)


def vehicles(n=3, end_s=AVAIL_END):
    return [Vehicle(f"V{i + 1}", "charter", 45, 0, W_S, end_s) for i in range(n)]


def build(rule=None, end_s=AVAIL_END, window=Window(W_S, W_E)):
    return build_timetable(cases.plan_spec([route()]), vehicles(end_s=end_s), window, policy(rule), tt())


def service_starts(tb):
    return sorted(t.stop_times[0].sched_s for t in tb.trips if t.in_service)


def cycle_ends(tb):
    ends = {}
    for t in tb.trips:
        key = (t.vehicle_id, t.cycle_index)
        ends[key] = max(ends.get(key, 0), t.stop_times[-1].sched_s)
    return ends


class GeneratorTests(unittest.TestCase):
    def test_cycle_length(self):
        tb = build()
        self.assertEqual(tb.routes[0].period_s, CYCLE)
        self.assertEqual(CYCLE, 4470)

    def test_current_rule_last_start_0829(self):
        """기본 규칙: 순환이 10:00 안에 끝나야 하므로 막차가 08:29에 떠난다."""
        for tb in (build(), build(SERVICE_END_CYCLE_WITHIN_WINDOW)):
            starts = service_starts(tb)
            self.assertEqual(starts[-1], hms(8, 29))
            self.assertEqual(len(starts), 7)
            self.assertTrue(all(end <= W_E for end in cycle_ends(tb).values()))

    def test_explicit_default_equals_missing_field(self):
        self.assertEqual(build(), build(SERVICE_END_CYCLE_WITHIN_WINDOW))

    def test_new_rule_last_start_before_0946(self):
        """새 규칙: 시작 < 10:00, 순환 끝 ≤ 11:00 → 마지막 시작 ≤ 09:46(= 11:00 − 74.5분 내림)."""
        tb = build(SERVICE_END_START_WITHIN_WINDOW)
        starts = service_starts(tb)
        self.assertEqual(starts[-1], hms(9, 43, 30))
        self.assertLessEqual(starts[-1], hms(9, 46))
        self.assertLess(starts[-1], W_E)
        self.assertEqual(len(starts), 10)
        self.assertEqual(starts[:7], service_starts(build()))     # 앞부분 시간표는 그대로

    def test_new_rule_cycles_end_by_available_end(self):
        tb = build(SERVICE_END_START_WITHIN_WINDOW)
        ends = cycle_ends(tb)
        self.assertTrue(all(end <= AVAIL_END for end in ends.values()))
        self.assertGreater(max(ends.values()), W_E)               # 10:00 뒤에도 돈다
        self.assertTrue(all(d.end_s <= AVAIL_END for d in tb.duties))

    def test_new_rule_available_end_binds(self):
        """가용 종료가 10:33이면 시작이 10:00 전이어도 순환이 10:33을 넘는 cycle은 넣지 않는다."""
        end_s = hms(10, 33)
        tb = build(SERVICE_END_START_WITHIN_WINDOW, end_s=end_s)
        starts = service_starts(tb)
        self.assertLessEqual(starts[-1] + CYCLE, end_s)
        self.assertEqual(starts[-1], W_S + 7 * 1490)              # 08:53:50. 다음 09:18:40은 10:33:10에 끝난다
        self.assertTrue(all(end <= end_s for end in cycle_ends(tb).values()))

    def test_new_rule_vehicle_ending_before_window_end(self):
        """가용 종료 < W_e면 두 규칙이 같다(끝 = 가용 종료)."""
        end_s = hms(9, 30)
        self.assertEqual(build(end_s=end_s), build(SERVICE_END_START_WITHIN_WINDOW, end_s=end_s))

    def test_normalize_plan_uses_policy_rule(self):
        tb = normalize_plan(cases.plan_spec([route()]), vehicles(), Window(W_S, W_E),
                            policy(SERVICE_END_START_WITHIN_WINDOW), tt())
        self.assertEqual(service_starts(tb)[-1], hms(9, 43, 30))


class ValidateTests(unittest.TestCase):
    """운행안이 준 시간표(provided)도 같은 규칙으로 검사한다."""

    def one_cycle(self, start_s):
        dep = start_s + 10
        legs = ((start_s, dep + 2400), (dep + 2400 + 180, dep + 2400 + 180 + 1880))
        trips = tuple(Trip(f"R1-V1-000-{li}", "R1", "V1", 0, li, li == 0,
                           tuple(StopTime(seq, stop, legs[li][seq]) for seq, stop in enumerate(leg["stop_ids"])))
                      for li, leg in enumerate(LEGS))
        return Timetable("plan-1", 1, "provided", (TimetableRoute("R1", None, ("V1",)),), trips,
                         (Duty("V1", "R1", tuple(t.trip_id for t in trips), legs[0][0], legs[1][-1]),))

    def check(self, start_s, rule, end_s=AVAIL_END):
        validate_timetable(self.one_cycle(start_s), cases.plan_spec([route(1)]), vehicles(1, end_s),
                           Window(W_S, W_E), tt(), policy(rule))

    def test_after_window_end_rejected_by_default(self):
        with self.assertRaises(SimError) as cm:
            self.check(hms(9, 30), None)
        self.assertEqual(cm.exception.code, "PLAN_INFEASIBLE")

    def test_after_window_end_accepted_by_new_rule(self):
        self.check(hms(9, 30), SERVICE_END_START_WITHIN_WINDOW)          # 끝 10:44:30 ≤ 11:00

    def test_new_rule_rejects_start_at_window_end(self):
        with self.assertRaises(SimError) as cm:
            self.check(W_E, SERVICE_END_START_WITHIN_WINDOW, end_s=12 * 3600)   # 끝 11:14:30은 가용 안
        self.assertIn("window end", cm.exception.message)

    def test_new_rule_rejects_end_after_available_end(self):
        with self.assertRaises(SimError) as cm:
            self.check(hms(9, 50), SERVICE_END_START_WITHIN_WINDOW)      # 끝 11:04:30
        self.assertIn("outside vehicle window", cm.exception.message)


class PolicySchemaTests(unittest.TestCase):
    def test_missing_field_is_default(self):
        self.assertEqual(policy().service_end_rule, SERVICE_END_CYCLE_WITHIN_WINDOW)
        raw = dict(cases.F1_POLICY, max_wait_s=1, max_journey_s=1, service_end_rule=None)
        self.assertEqual(io.policy_from_dict(raw).service_end_rule, SERVICE_END_CYCLE_WITHIN_WINDOW)

    def test_new_rule_value(self):
        self.assertEqual(policy(SERVICE_END_START_WITHIN_WINDOW).service_end_rule, SERVICE_END_START_WITHIN_WINDOW)

    def test_unknown_value_rejected(self):
        for bad in ("return_by_available", "", 1):
            with self.assertRaises(SimError) as cm:
                policy(bad)
            self.assertEqual(cm.exception.code, "INPUT_INVALID")


def e2e_case(rule=None):
    vs = [cases.vehicle_dict(f"V{i + 1}", 45, AVAIL_END, start_s=W_S) for i in range(3)]
    extra = {} if rule is None else {"service_end_rule": rule}
    sc = cases.scenario_dict((W_S, W_E), vs, 1200, 3600, turnaround_s=180, **extra)
    plan = cases.plan_dict([{"route_id": "R1", "legs": LEGS, "vehicle_count": 3}])
    # 09:40에 A에서 기다리는 승객: 기본 규칙이면 탈 차가 없고(service_end), 새 규칙이면 09:43:30 차를 탄다
    rows = [cases.pax("p0700", "A", "S", hms(7, 0)), cases.pax("p0940", "A", "S", hms(9, 40))]
    return cases.Case(sc, cases.travel_times_dict(SEGMENTS, valid_until_s=12 * 3600), plan, rows)


class EndToEndTests(unittest.TestCase):
    """M05 실행 → M07 검증기(pass=true)."""

    def run_case(self, rule):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            paths = e2e_case(rule).write(d / "in")
            run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], d / "out")
            code = verify_main([str(d / "out" / "run"), "--out", str(d / "report.json"), "--quiet"])
            report = json.loads((d / "report.json").read_text(encoding="utf-8"))
            read = lambda p: (d / "out" / p).read_bytes()
            return code, report, {p: read(p) for p in ("scenario/network.json", "run/events.jsonl",
                                                       "run/passenger_outcomes.csv", "run/metrics.json",
                                                       "run/measurement.json")}

    def test_default_rule(self):
        code, report, files = self.run_case(None)
        self.assertEqual((code, report["pass"]), (0, True))
        spec = json.loads(files["scenario/network.json"])["spec"]
        self.assertNotIn("service_end_rule", spec)                 # 기본 규칙은 network.json 형식 그대로
        self.assertNotIn("vehicle_available_end_s", spec)
        self.assertNotIn("service_end_rule", json.loads(files["run/measurement.json"])["policy"])
        metrics = json.loads(files["run/metrics.json"])
        self.assertEqual(metrics["M2"]["unserved_by_reason"]["service_end"], 1)

    def test_explicit_default_same_outputs(self):
        _, _, missing = self.run_case(None)
        _, _, explicit = self.run_case(SERVICE_END_CYCLE_WITHIN_WINDOW)
        for name in ("scenario/network.json", "run/events.jsonl", "run/passenger_outcomes.csv", "run/metrics.json"):
            self.assertEqual(missing[name], explicit[name], name)

    def test_new_rule_passes_verifier(self):
        code, report, files = self.run_case(SERVICE_END_START_WITHIN_WINDOW)
        self.assertEqual((code, report["pass"]), (0, True), report.get("failed_checks"))
        network = json.loads(files["scenario/network.json"])
        self.assertEqual(network["spec"]["service_end_rule"], SERVICE_END_START_WITHIN_WINDOW)
        self.assertEqual(network["spec"]["vehicle_available_end_s"], {"V1": AVAIL_END, "V2": AVAIL_END,
                                                                      "V3": AVAIL_END})
        self.assertEqual(max(t["arrivals"][0] for t in network["trips"]), hms(9, 43, 30))
        self.assertTrue(all(t["return_s"] <= AVAIL_END for t in network["trips"]))
        metrics = json.loads(files["run/metrics.json"])
        self.assertEqual(metrics["M1"]["served"], 2)
        self.assertEqual(json.loads(files["run/measurement.json"])["policy"]["service_end_rule"],
                         SERVICE_END_START_WITHIN_WINDOW)


if __name__ == "__main__":
    unittest.main()
