"""S(n) 곡선·클러스터(simulator.md §7.1·§7.2, §9.4 T3 클러스터 검사)."""

import unittest

from busstrike.sim import prepare
from busstrike.sim.curve import (
    CurvePoint, cluster_eval, eligible_passengers, fleet_curve, marginal_gains, overlap_ratios, route_clusters,
)
from busstrike.sim.models import SimError

from tests.sim import cases


def t3_ctx(**kw):
    sc, tt, plan, pas = cases.t3(**kw).objects()
    return plan, prepare(pas, sc.vehicles, sc.window, sc.policy, tt)


def f1_ctx(case):
    sc, tt, plan, pas = case.objects()
    return plan, prepare(pas, sc.vehicles, sc.window, sc.policy, tt)


class ClusterTests(unittest.TestCase):
    def test_t3_shared_passengers_join_routes(self):
        plan, ctx = t3_ctx()
        self.assertEqual(route_clusters(plan.routes, ctx), [["R1", "R2"]])
        self.assertEqual([p.passenger_id for p in eligible_passengers(plan.routes[0], ctx)], ["w1", "w2"])
        self.assertEqual(overlap_ratios(plan.routes, ctx), {"R1": 1.0, "R2": 1.0})

    def test_t3_split_destinations_are_independent_and_additive(self):
        plan, ctx = t3_ctx(w1_dest=("S1",), w2_dest=("S2",))
        self.assertEqual(route_clusters(plan.routes, ctx), [["R1"], ["R2"]])
        self.assertEqual(overlap_ratios(plan.routes, ctx), {"R1": 0.0, "R2": 0.0})
        r1, r2 = plan.routes
        together = cluster_eval(plan.routes, {"R1": 1, "R2": 1}, ctx)
        alone = [fleet_curve(r, 1, ctx)[1] for r in (r1, r2)]
        self.assertEqual(together.satisfied, sum(p.satisfied for p in alone))
        self.assertEqual(together.transported, sum(p.transported for p in alone))
        self.assertEqual(together.eligible, 2)

    def test_cluster_eval_rejects_unknown_route_and_oversized_fleet(self):
        plan, ctx = t3_ctx()
        with self.assertRaises(SimError):
            cluster_eval(plan.routes, {"R9": 1}, ctx)
        with self.assertRaises(SimError) as cm:
            cluster_eval(plan.routes, {"R1": 2, "R2": 1}, ctx)
        self.assertEqual(cm.exception.code, SimError.PLAN_INFEASIBLE)


class FleetCurveTests(unittest.TestCase):
    def test_t1_curve_matches_single_run(self):
        # T1(§9.2): 1대면 p1·p2·p4·p5 수송, p3는 5초 늦어 WAIT_EXCEEDED → 충족 4, 대기 P90 119.
        plan, ctx = f1_ctx(cases.t1())
        pts = fleet_curve(plan.routes[0], 1, ctx)
        self.assertEqual(pts[0], CurvePoint(0, 0, 0, 5, None, 0, 0, True))
        self.assertEqual(pts[1], CurvePoint(1, 4, 4, 5, 119, 10_000, 1, True))
        self.assertEqual(marginal_gains(pts), [None, 4])

    def test_t2_more_vehicles_relieve_full(self):
        # T2(§9.3): 좌석 2, 1대로는 만차 남겨짐이 생긴다. 차량을 늘려도 값은 그대로 보고한다.
        case = cases.t2()
        case.scenario["fleet"]["vehicles"].append(cases.vehicle_dict("V2", 2, 1500))
        plan, ctx = f1_ctx(case)
        pts = fleet_curve(plan.routes[0], 2, ctx, stop_when_saturated=False)
        self.assertEqual([p.n for p in pts], [0, 1, 2])
        self.assertTrue(all(p.simulated for p in pts))
        self.assertLessEqual(pts[1].satisfied, pts[2].satisfied)
        self.assertEqual(pts[2].eligible, 5)

    def test_saturated_points_are_filled_not_simulated(self):
        # T1에 2대: 둘째 차(오프셋 floor(680/2)=340)가 p3(도착 5, 한도 605)를 태워 5명 모두 충족.
        case = cases.t1()
        case.scenario["fleet"]["vehicles"] += [cases.vehicle_dict("V2", 3, 1500), cases.vehicle_dict("V3", 3, 1500)]
        plan, ctx = f1_ctx(case)
        pts = fleet_curve(plan.routes[0], 3, ctx)
        self.assertEqual((pts[2].satisfied, pts[2].simulated), (5, True))
        self.assertEqual(pts[3], CurvePoint(3, 5, 5, 5, None, None, None, False))
        self.assertEqual(marginal_gains(pts), [None, 4, 1, 0])
        full = fleet_curve(plan.routes[0], 3, ctx, stop_when_saturated=False)
        self.assertEqual([p.satisfied for p in full], [0, 4, 5, 5])
        self.assertTrue(full[3].simulated)

    def test_n_max_checks(self):
        plan, ctx = f1_ctx(cases.t1())
        with self.assertRaises(SimError):
            fleet_curve(plan.routes[0], 2, ctx)
        with self.assertRaises(SimError):
            fleet_curve(plan.routes[0], -1, ctx)


if __name__ == "__main__":
    unittest.main()
