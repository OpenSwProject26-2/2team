"""R5 -> C under C's definition: Demand.arrival_s is an exact, already generated time
(C ADR-001, PR #16). The realization is converted into C demands before C runs."""
from dataclasses import replace
from pathlib import Path
import unittest
from sim.arrivals import BinRow, generate
from sim.models import Config
from sim.adapters.opt_adapter import (c_demands, check_c_demands, prepare_inputs, compile_schedule,
                                      evaluation_port, service_plan)
from opt.models import load_inputs, make_plan, InputError
from opt.integration import checked_assessment
from tests.sim.test_arrivals import retime

ROOT = Path(__file__).resolve().parents[2]


class CDemandConversionTests(unittest.TestCase):
    def setUp(self):
        self.x = load_inputs(ROOT / 'C.Part/opt/examples/synthetic.json')
        # Synthetic OD x 10-minute rows built from the C example; routes mapped explicitly.
        self.rows = tuple(BinRow(d.demand_id, d.origin, d.destination, d.arrival_s, 600, d.passenger_count)
                          for d in self.x.demands)
        self.route = {d.demand_id: d.original_route_id for d in self.x.demands}
        self.route_of = lambda row: self.route[row.aggregate_id]
        self.real = generate(self.rows, 'adapter')
        self.cx = replace(self.x, demands=c_demands(self.real, self.route_of), demand_version='r5-adapter')
        self.run = prepare_inputs(self.cx, seed=self.real.seed)
        self.config = Config(self.x.policy.max_wait_s, self.x.policy.max_travel_s, 2000, 'fifo', 'fixed_segments')

    def test_conversion_preserves_people_od_route_and_bins(self):
        demands = c_demands(self.real, self.route_of)
        self.assertEqual(sum(d.passenger_count for d in demands), self.x.total_demand)
        self.assertEqual(len({d.demand_id for d in demands}), len(demands))
        rows = {r.aggregate_id: r for r in self.rows}
        for d in demands:
            row = rows[d.demand_id.rsplit('@', 1)[0]]
            self.assertEqual((d.origin, d.destination, d.original_route_id),
                             (row.origin, row.destination, self.route[row.aggregate_id]))
            self.assertTrue(row.bin_start_s <= d.arrival_s < row.bin_start_s + row.bin_width_s)
        self.assertEqual(demands, c_demands(self.real, self.route_of))

    def test_c_inputs_evaluate_in_exact_mode(self):
        self.assertEqual(self.cx.total_demand, self.x.total_demand)
        check_c_demands(self.cx, self.real, self.route_of)
        plan = make_plan(self.cx, {'r1': 1}, 'm05-test')
        result = checked_assessment(evaluation_port(self.run, self.config), plan, self.cx)
        self.assertEqual(result.total, self.x.total_demand)
        self.assertFalse(result.independently_verified)
        self.assertEqual(service_plan(plan, self.cx, self.run)['schedule_status'], 'compiled')

    def test_mismatch_rejected(self):
        with self.assertRaises(InputError): check_c_demands(self.cx, generate(self.rows, 'other'), self.route_of)
        with self.assertRaises(InputError): check_c_demands(self.x, self.real, self.route_of)  # original exact demands
        with self.assertRaises(InputError):
            check_c_demands(self.cx, retime(self.real, 0, 10 ** 6), self.route_of)  # tampered realization
        with self.assertRaises(Exception): c_demands(self.real, lambda row: None)

    def test_seed_changes_c_input_and_plan_identity(self):
        # Under C's definition each realization is a different C input bundle, so the same
        # allocation gets a different plan_id; paired seed comparison must re-mint plans.
        other = replace(self.x, demands=c_demands(generate(self.rows, 'other'), self.route_of))
        self.assertNotEqual(self.cx.checksum, other.checksum)
        self.assertNotEqual(make_plan(self.cx, {'r1': 1}, 's').plan_id, make_plan(other, {'r1': 1}, 's').plan_id)

    def test_exact_mode_unchanged(self):
        exact = prepare_inputs(self.x, seed='fixed')
        self.assertEqual([p.time_s for p in exact.passengers],
                         [d.arrival_s for d in sorted(self.x.demands, key=lambda d: d.demand_id)
                          for _ in range(d.passenger_count)])


if __name__ == '__main__': unittest.main()
