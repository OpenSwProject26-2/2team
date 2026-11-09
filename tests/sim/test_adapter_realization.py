from dataclasses import replace
from pathlib import Path
import unittest
from sim.arrivals import generate
from sim.models import Config
from sim.adapters.opt_adapter import (demand_bins, realized_inputs, prepare_inputs, compile_schedule,
                                      evaluation_port, service_plan)
from opt.models import load_inputs, make_plan, InputError
from opt.integration import checked_assessment
from tests.sim.test_arrivals import retime

ROOT = Path(__file__).resolve().parents[2]


class AdapterRealizationTests(unittest.TestCase):
    def setUp(self):
        self.x = load_inputs(ROOT / 'C.Part/opt/examples/synthetic.json')
        self.p = make_plan(self.x, {'r1': 1}, 'm05-test')
        self.real = generate(demand_bins(self.x, 600), 'adapter')
        self.run = realized_inputs(self.x, self.real)
        self.config = Config(self.x.policy.max_wait_s, self.x.policy.max_travel_s, 2000, 'fifo', 'fixed_segments')

    def test_accepts_and_evaluates(self):
        self.assertEqual(len(self.run.passengers), self.x.total_demand)
        self.assertEqual(self.run.source_checksum, self.x.checksum)
        self.assertTrue(compile_schedule(self.p, self.x, self.run, self.real))
        port = evaluation_port(self.run, self.config, realization=self.real)
        result = checked_assessment(port, self.p, self.x)
        self.assertEqual(result.total, self.x.total_demand)
        self.assertFalse(result.independently_verified)
        self.assertEqual(service_plan(self.p, self.x, self.run, self.real)['schedule_status'], 'compiled')

    def test_rejects_mismatch(self):
        other = generate(demand_bins(self.x, 600), 'other-seed')
        with self.assertRaises(InputError): compile_schedule(self.p, self.x, self.run, other)
        with self.assertRaises(InputError): compile_schedule(self.p, self.x, self.run)  # exact mode differs
        with self.assertRaises(InputError): realized_inputs(self.x, retime(self.real, 0, 10**6))
        with self.assertRaises(InputError):  # bins of a different width are a different input
            compile_schedule(self.p, self.x, self.run, generate(demand_bins(self.x, 300), 'adapter'))
        with self.assertRaises(InputError):
            realized_inputs(self.x, generate(demand_bins(self.x, 600)[1:], 'adapter'))

    def test_exact_mode_unchanged(self):
        exact = prepare_inputs(self.x, seed='fixed')
        self.assertEqual([p.time_s for p in exact.passengers],
                         [d.arrival_s for d in sorted(self.x.demands, key=lambda d: d.demand_id)
                          for _ in range(d.passenger_count)])


if __name__ == '__main__': unittest.main()
