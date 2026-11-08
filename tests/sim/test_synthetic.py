import unittest
from sim import synthetic
from sim.schedule import validate_schedule


class SyntheticNetworkTests(unittest.TestCase):
    def test_deterministic_and_valid(self):
        a = synthetic.build_network(synthetic.COMPLEX, 'net-v1')
        b = synthetic.build_network(synthetic.COMPLEX, 'net-v1')
        self.assertEqual(a, b)
        validate_schedule(a['trips'])
        self.assertNotEqual(a['trips'], synthetic.build_network(synthetic.COMPLEX, 'net-v2')['trips'])

    def test_complex_shape(self):
        n = synthetic.build_network(synthetic.COMPLEX, 'net-v1')
        spec = synthetic.COMPLEX
        self.assertEqual(len(n['routes']), 2 * spec.corridors)
        self.assertEqual(len(n['stops']), spec.corridors * (spec.stops_per_route - spec.shared_stops) + spec.shared_stops)
        served_by = {}
        for rid, seq in n['routes'].items():
            for s in seq: served_by.setdefault(s, set()).add(rid[:-1])
        self.assertTrue(any(len(c) > 1 for c in served_by.values()), 'corridors must share stops')
        self.assertEqual(set(n['route_of']), {t.trip_id for t in n['trips']})
        for t in n['trips']:
            self.assertEqual(t.stops, n['routes'][n['route_of'][t.trip_id]])
            self.assertLessEqual(t.return_s, spec.service_end_s)

    def test_simple_shape(self):
        n = synthetic.build_network(synthetic.SIMPLE, 'net-v1')
        self.assertEqual((len(n['stops']), len(n['routes'])), (2, 1))
        self.assertEqual({t.vehicle_id for t in n['trips']}.__len__(), synthetic.SIMPLE.vehicles_per_route)

    def test_demand_exact_total_and_bins(self):
        n = synthetic.build_network(synthetic.COMPLEX, 'net-v1')
        spec = synthetic.DemandSpec(5003, 25200, 32400, 600, 2, 50)
        rows = synthetic.build_demand(n, spec, 'd1')
        self.assertEqual(sum(r.passenger_count for r in rows), 5003)
        self.assertEqual(rows, synthetic.build_demand(n, spec, 'd1'))
        self.assertEqual(len({r.aggregate_id for r in rows}), len(rows))
        pairs = synthetic.served_pairs(n['routes'])
        unconnected = sum(r.passenger_count for r in rows if (r.origin, r.destination) not in pairs)
        self.assertGreater(unconnected, 0)
        self.assertLess(unconnected, 5003)
        for r in rows:
            self.assertEqual((r.bin_start_s - 25200) % 600, 0)
            self.assertTrue(25200 <= r.bin_start_s < 32400)
            self.assertGreater(r.passenger_count, 0)
        self.assertEqual(synthetic.build_demand(n, synthetic.DemandSpec(0, 0, 600, 600, 2, 0), 'd'), ())

    def test_invalid_specs(self):
        from sim.models import SimulationError
        with self.assertRaises(SimulationError): synthetic.DemandSpec(10, 0, 700, 600, 2, 0)
        with self.assertRaises(SimulationError):
            synthetic.NetworkSpec('x', 1, 2, 2, False, 1, 1, 1, 0, 0, 0, 0, 0, 10, 1)


if __name__ == '__main__': unittest.main()
