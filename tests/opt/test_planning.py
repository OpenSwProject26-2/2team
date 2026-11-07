"""공개 합성 입력으로 검증하는 C 수락 조건."""

from dataclasses import replace
from pathlib import Path
import unittest

from opt.baselines import baseline
from opt.candidates import candidates, validate_allocation
from opt.evaluator import evaluate, route_timing
from opt.integration import Assessment, evaluate_holdout, rerank
from opt.models import Demand, InputError, load_inputs, make_plan
from opt.search import exhaustive, search

FIXTURE = Path(__file__).resolve().parents[2] / "opt/examples/synthetic.json"


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.x = load_inputs(FIXTURE)

    def plan(self, **allocation):
        return make_plan(self.x, allocation, "test")

    def test_baseline_rounding_and_small_fleet(self):
        self.assertEqual(dict(baseline(self.x).allocation), {'r1': 1, 'r2': 1})
        self.assertEqual(dict(baseline(self.x, True).allocation), {'r1': 2, 'r2': 0})
        x = replace(self.x, fleet=self.x.fleet[:1])
        self.assertEqual(dict(baseline(x).allocation), {'r1': 1, 'r2': 0})

    def test_zero_fleet_and_zero_demand(self):
        x = replace(self.x, fleet=())
        self.assertEqual(search(x).best.estimate.served, 0)
        x = replace(self.x, demands=())
        self.assertEqual(baseline(x).allocation, baseline(x, True).allocation)
        self.assertIsNone(search(x).best.estimate.fulfillment_rate)

    def test_hand_calculation_one_departure_capacity(self):
        x = replace(self.x, policy=replace(self.x.policy, end_s=240),
                    demands=(Demand('one', 'A', 'C', 0, 3, 'r1'),))
        metric = evaluate(x, make_plan(x, {'r1': 1}, 'test'))
        self.assertEqual((metric.served, metric.mean_wait_s, metric.distance_m), (2, 0, 1200))

    def test_waiting_for_second_departure_and_deadline(self):
        demand = (Demand('one', 'A', 'C', 0, 3, 'r1'),)
        x = replace(self.x, demands=demand, policy=replace(self.x.policy, max_wait_s=240))
        metric = evaluate(x, make_plan(x, {'r1': 1}, 'test'))
        self.assertEqual((metric.served, metric.mean_wait_s), (3, 80))
        x = replace(x, policy=replace(x.policy, max_wait_s=239))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 1}, 'test')).served, 2)

    def test_intermediate_segment_capacity(self):
        x = replace(self.x, policy=replace(self.x.policy, end_s=240), demands=(
            Demand('first', 'A', 'C', 0, 2, 'r1'), Demand('second', 'B', 'C', 60, 2, 'r1')))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 1}, 'test')).served, 2)

    def test_capacity_reused_after_alighting(self):
        x = replace(self.x, policy=replace(self.x.policy, end_s=240), demands=(
            Demand('first', 'A', 'B', 0, 2, 'r1'), Demand('second', 'B', 'C', 60, 2, 'r1')))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 1}, 'test')).served, 4)

    def test_duplicate_od_and_direction(self):
        x = replace(self.x, demands=(Demand('one', 'A', 'B', 0, 1, 'r1'),))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 1, 'r2': 1}, 'test')).served, 1)
        x = replace(x, demands=(Demand('reverse', 'C', 'A', 0, 3, 'r1'),))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 2}, 'test')).served, 0)
        x = replace(x, demands=(Demand('none', 'C', 'D', 0, 3, 'r1'),))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 2}, 'test')).served, 0)

    def test_turn_and_turnaround_rejection(self):
        x = replace(self.x, allowed_turns=tuple(t for t in self.x.allowed_turns if t != ('bc', 'cb')))
        self.assertIn('r1', candidates(x)[1])
        with self.assertRaises(InputError):
            baseline(x)
        x = replace(self.x, turnaround_stops=('A', 'B', 'D'))
        self.assertIn('r1', candidates(x)[1])

    def test_disconnected_or_unknown_segment(self):
        for outbound in (('ab', 'db'), ('unknown',)):
            route = replace(self.x.routes[0], outbound=outbound)
            x = replace(self.x, routes=(route,) + self.x.routes[1:])
            self.assertIn('r1', candidates(x)[1])

    def test_invalid_allocation(self):
        for allocation in ({'r1': -1}, {'r1': 1.5}, {'r1': True}, {'r1': 3}, {'unknown': 0}):
            with self.assertRaises(InputError):
                validate_allocation(self.x, make_plan(self.x, allocation, 'test'))

    def test_dwell_turnaround_return_and_end(self):
        x = replace(self.x, policy=replace(self.x.policy, dwell_s=10, turnaround_s=30))
        self.assertEqual(route_timing(x, x.routes[0])[2], 290)
        x = replace(x, policy=replace(x.policy, end_s=289))
        self.assertEqual(evaluate(x, make_plan(x, {'r1': 1}, 'test')).served, 0)

    def test_determinism_budget_and_exhaustive(self):
        self.assertEqual(search(self.x), search(self.x))
        exact = exhaustive(self.x)
        self.assertEqual(exact.evaluations, 10)
        self.assertGreaterEqual(exact.best.estimate.served, search(self.x).best.estimate.served)
        for budget in (1, 2, 3, 10):
            result = search(self.x, budget)
            self.assertLessEqual(result.evaluations, budget)
            self.assertLessEqual(result.best.estimate.served, self.x.total_demand)
        with self.assertRaises(InputError):
            exhaustive(self.x, max_combinations=9)

    def test_input_validation(self):
        with self.assertRaises(InputError):
            replace(self.x, demands=(replace(self.x.demands[0], passenger_count=-1),))
        with self.assertRaises(InputError):
            replace(self.x, fleet=(self.x.fleet[0], replace(self.x.fleet[1], capacity=3)))
        with self.assertRaises(InputError):
            replace(self.x, stops=('A', 'A'))
        with self.assertRaises(InputError):
            replace(self.x, policy=replace(self.x.policy, max_wait_s=True))

    def test_versions_and_export(self):
        p = baseline(self.x)
        output = p.to_service_plan(self.x)
        self.assertEqual(output['status'], 'draft')
        self.assertEqual(output['schedule_status'], 'pending_b')
        self.assertEqual(output['trips'], [])
        with self.assertRaises(InputError):
            evaluate(replace(self.x, demand_version='changed'), p)

    def assessment(self, p, x):
        # 테스트 대역일 뿐 실제 B 검증 결과가 아니다.
        return Assessment(p.plan_id, p.revision, x.input_bundle_id, x.checksum,
                          True, True, 1, x.total_demand, 10, 100)

    def test_b_port_invalid_plans_and_top_three(self):
        top = search(self.x).top
        calls = []
        def port(p, x):
            calls.append(p.plan_id)
            return replace(self.assessment(p, x), schedule_feasible=False)
        selection = rerank(self.x, top, port)
        self.assertIsNone(selection.recommended)
        self.assertLessEqual(len(calls), 3)
        self.assertIsNotNone(rerank(self.x, top, self.assessment).recommended)
        with self.assertRaises(InputError):
            rerank(self.x, top, lambda p, x: replace(self.assessment(p, x), input_checksum='bad'))
        with self.assertRaises(InputError):
            rerank(self.x, top, lambda p, x: replace(self.assessment(p, x), total=999))

    def test_holdout_cannot_search_or_select(self):
        x = replace(self.x, split='evaluation', input_bundle_id='holdout', demand_version='holdout')
        with self.assertRaises(InputError):
            search(x)
        with self.assertRaises(InputError):
            exhaustive(x)
        with self.assertRaises(InputError):
            rerank(x, search(self.x).top, self.assessment)
        result = evaluate_holdout(self.x, x, baseline(self.x), self.assessment)
        self.assertEqual(result.input_bundle_id, 'holdout')
        with self.assertRaises(InputError):
            evaluate_holdout(self.x, replace(x, policy=replace(x.policy, dwell_s=20)),
                             baseline(self.x), self.assessment)


if __name__ == '__main__':
    unittest.main()
