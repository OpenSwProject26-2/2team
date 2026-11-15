"""시간표 생성기: simulator.md §1, §9.1, §9.5(lookup·P), §9.7 T6."""

import unittest

from busstrike.sim import (
    Leg, RouteSpec, SimError, TravelTimeTable, Vehicle, Window, assign_vehicles, build_timetable,
    normalize_plan, planned_cycle,
)
from busstrike.sim.timetable import route_period

from tests.sim import cases


def anchor_departures(timetable, vehicle_id):
    return [t.stop_times[0].sched_s for t in timetable.trips if t.vehicle_id == vehicle_id and t.leg_index == 0]


class PlannedCycleTests(unittest.TestCase):
    def test_f1_planned_cycle(self):
        """§9.1: A 0 → 10 → B 130 → 140 → S 320 → 회차 → S 380 → B 560 → A 680."""
        legs, finish, ready = planned_cycle(cases.f1_route(), 0, cases.f1_policy(), cases.f1_tt())
        self.assertEqual(legs, ((0, 130, 320), (380, 560, 680)))
        self.assertEqual((finish, ready), (680, 680))

    def test_lookup_bins_t4(self):
        segs = dict(cases.F1_SEGMENTS)
        segs[("A", "B")] = (1000, [[0, 120], [600, 240]])
        tt = cases.f1_tt(segs)
        self.assertEqual(tt.lookup("A", "B", 599), 120)
        self.assertEqual(tt.lookup("A", "B", 600), 240)
        self.assertEqual(tt.lookup("A", "B", 9999), 240)
        legs, _, ready = planned_cycle(cases.f1_route(), 600, cases.f1_policy(), tt)
        self.assertEqual(legs, ((600, 850, 1040), (1100, 1280, 1400)))
        self.assertEqual(ready - 600, 800)
        _, period = route_period(cases.f1_route(), cases.f1_vehicles(1), Window(0, 1700), cases.f1_policy(), tt)
        self.assertEqual(period, 800)
        tb = build_timetable(cases.plan_spec([cases.f1_route()]), cases.f1_vehicles(1, end_s=1700),
                             Window(0, 1700), cases.f1_policy(), tt)
        self.assertEqual([[s.sched_s for s in t.stop_times] for t in tb.trips],
                         [[0, 130, 320], [380, 560, 680], [800, 1050, 1240], [1300, 1480, 1600]])

    def test_lookup_errors(self):
        tt = cases.f1_tt()
        with self.assertRaises(SimError) as cm:
            tt.lookup("A", "S", 0)
        self.assertEqual(cm.exception.code, "DATA_INCOMPLETE")
        late = TravelTimeTable.from_dict(cases.travel_times_dict(cases.F1_SEGMENTS, valid_until_s=1000))
        with self.assertRaises(SimError) as cm:
            late.check_window(Window(0, 1500))
        self.assertEqual(cm.exception.code, "DATA_INCOMPLETE")


class T6FleetSizeTests(unittest.TestCase):
    """§9.7 T6: F1, 좌석 3(allow=9), 운영시간 [0,1500], P=680."""

    def build(self, n):
        route = cases.f1_route(n)
        return build_timetable(cases.plan_spec([route]), cases.f1_vehicles(max(n, 1)), cases.F1_WINDOW,
                               cases.f1_policy(), cases.f1_tt())

    def test_n1(self):
        tb = self.build(1)
        self.assertEqual(tb.routes[0].period_s, 680)
        self.assertEqual(anchor_departures(tb, "V1"), [0, 680])
        self.assertEqual([t.trip_id for t in tb.trips], ["R1-V1-000-0", "R1-V1-000-1", "R1-V1-001-0", "R1-V1-001-1"])
        self.assertEqual([[s.sched_s for s in t.stop_times] for t in tb.trips],
                         [[0, 130, 320], [380, 560, 680], [680, 810, 1000], [1060, 1240, 1360]])
        self.assertEqual(tb.origin, "generated")
        self.assertEqual([(d.vehicle_id, d.start_s, d.end_s) for d in tb.duties], [("V1", 0, 1360)])

    def test_n2(self):
        tb = self.build(2)
        self.assertEqual(anchor_departures(tb, "V1"), [0, 680])
        self.assertEqual(anchor_departures(tb, "V2"), [340])
        v2 = [[s.sched_s for s in t.stop_times] for t in tb.trips if t.vehicle_id == "V2"]
        self.assertEqual(v2, [[340, 470, 660], [720, 900, 1020]])

    def test_n3(self):
        tb = self.build(3)
        self.assertEqual(anchor_departures(tb, "V1"), [0, 680])
        self.assertEqual(anchor_departures(tb, "V2"), [226])
        self.assertEqual(anchor_departures(tb, "V3"), [453])
        v2 = [[s.sched_s for s in t.stop_times] for t in tb.trips if t.vehicle_id == "V2"]
        v3 = [[s.sched_s for s in t.stop_times] for t in tb.trips if t.vehicle_id == "V3"]
        self.assertEqual(v2, [[226, 356, 546], [606, 786, 906]])
        self.assertEqual(v3, [[453, 583, 773], [833, 1013, 1133]])

    def test_n0(self):
        tb = build_timetable(cases.plan_spec([cases.f1_route(0)]), cases.f1_vehicles(1), cases.F1_WINDOW,
                             cases.f1_policy(), cases.f1_tt())
        self.assertEqual(tb.trips, ())
        self.assertEqual(tb.duties, ())
        self.assertIsNone(tb.routes[0].period_s)

    def test_too_many_vehicles(self):
        with self.assertRaises(SimError) as cm:
            build_timetable(cases.plan_spec([cases.f1_route(2)]), cases.f1_vehicles(1), cases.F1_WINDOW,
                            cases.f1_policy(), cases.f1_tt())
        self.assertEqual(cm.exception.code, "PLAN_INFEASIBLE")

    def test_discontinuous_legs(self):
        route = RouteSpec("R1", (Leg(True, ("A", "B", "S")), Leg(False, ("S", "B"))), 1)
        with self.assertRaises(SimError) as cm:
            build_timetable(cases.plan_spec([route]), cases.f1_vehicles(1), cases.F1_WINDOW,
                            cases.f1_policy(), cases.f1_tt())
        self.assertEqual(cm.exception.code, "PLAN_INFEASIBLE")

    def test_initial_stop_not_anchor(self):
        v = [Vehicle("V1", "charter", 3, 0, 0, 1500, initial_stop_id="B")]
        with self.assertRaises(SimError) as cm:
            build_timetable(cases.plan_spec([cases.f1_route(1)]), v, cases.F1_WINDOW, cases.f1_policy(),
                            cases.f1_tt())
        self.assertEqual(cm.exception.code, "PLAN_INFEASIBLE")
        self.assertEqual(cm.exception.details["reason"], "DEADHEAD_DATA_REQUIRED")
        ok = [Vehicle("V1", "charter", 3, 0, 0, 1500, initial_stop_id="A", end_stop_id="A")]
        build_timetable(cases.plan_spec([cases.f1_route(1)]), ok, cases.F1_WINDOW, cases.f1_policy(), cases.f1_tt())

    def test_missing_segment(self):
        segs = dict(cases.F1_SEGMENTS)
        del segs[("B", "S")]
        with self.assertRaises(SimError) as cm:
            build_timetable(cases.plan_spec([cases.f1_route(1)]), cases.f1_vehicles(1), cases.F1_WINDOW,
                            cases.f1_policy(), cases.f1_tt(segs))
        self.assertEqual(cm.exception.code, "DATA_INCOMPLETE")

    def test_circular_and_two_station_routes_share_one_function(self):
        segs = {("A", "B"): (1000, [[0, 120]]), ("B", "S"): (1500, [[0, 180]]), ("S", "A"): (2000, [[0, 200]]),
                ("S1", "M"): (900, [[0, 90]]), ("M", "S2"): (900, [[0, 90]]),
                ("S2", "M"): (900, [[0, 90]]), ("M", "S1"): (900, [[0, 90]])}
        circ = RouteSpec("C1", (Leg(True, ("A", "B", "S", "A")),), 1)
        two = RouteSpec("C2", (Leg(True, ("S1", "M", "S2")), Leg(True, ("S2", "M", "S1"))), 1)
        tb = build_timetable(cases.plan_spec([circ, two]), cases.f1_vehicles(2), cases.F1_WINDOW,
                             cases.f1_policy(), TravelTimeTable.from_dict(cases.travel_times_dict(segs)))
        circ_trips = [t for t in tb.trips if t.route_id == "C1"]
        two_trips = [t for t in tb.trips if t.route_id == "C2"]
        # 순환형: A 0 → 10 → B 130 → 140 → S 320 → 330 → A 530, 회차 60 → P = 590
        self.assertEqual([s.sched_s for s in circ_trips[0].stop_times], [0, 130, 320, 530])
        self.assertEqual(tb.routes[0].period_s, 590)
        # 두 역 연결형: 영업 두 leg 모두 기본 정차·회차 적용, P = 2·(10+90+10+90+60) = 520
        self.assertEqual([[s.sched_s for s in t.stop_times] for t in two_trips[:2]], [[0, 100, 200], [260, 360, 460]])
        self.assertEqual(tb.routes[1].period_s, 520)
        self.assertTrue(all(t.in_service for t in two_trips))

    def test_assign_vehicles_explicit_and_by_id(self):
        r_a = RouteSpec("RA", (Leg(True, ("A", "B", "S")), Leg(False, ("S", "B", "A"))), 1)
        r_b = RouteSpec("RB", (Leg(True, ("A", "B", "S")), Leg(False, ("S", "B", "A"))), 1, ("V1",))
        out = assign_vehicles(cases.plan_spec([r_b, r_a]), cases.f1_vehicles(3))
        self.assertEqual([v.vehicle_id for v in out["RB"]], ["V1"])
        self.assertEqual([v.vehicle_id for v in out["RA"]], ["V2"])
        bad = RouteSpec("RC", r_a.legs, 1, ("V9",))
        with self.assertRaises(SimError):
            assign_vehicles(cases.plan_spec([bad]), cases.f1_vehicles(1))

    def test_provided_timetable_overlap_rejected(self):
        tb = build_timetable(cases.plan_spec([cases.f1_route(1)]), cases.f1_vehicles(1), cases.F1_WINDOW,
                             cases.f1_policy(), cases.f1_tt())
        from dataclasses import replace
        from busstrike.sim import StopTime
        trips = list(tb.trips)
        bad = trips[2]
        trips[2] = replace(bad, stop_times=tuple(StopTime(s.stop_seq, s.stop_id, s.sched_s - 400)
                                                 for s in bad.stop_times))
        duties = (replace(tb.duties[0]),)
        plan = replace(cases.plan_spec([cases.f1_route(1)]), trips=tuple(trips), duties=duties)
        with self.assertRaises(SimError) as cm:
            normalize_plan(plan, cases.f1_vehicles(1), cases.F1_WINDOW, cases.f1_policy(), cases.f1_tt())
        self.assertEqual(cm.exception.code, "PLAN_INFEASIBLE")


if __name__ == "__main__":
    unittest.main()
