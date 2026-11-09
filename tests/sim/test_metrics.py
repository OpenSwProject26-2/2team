"""Hand-calculated M1-M6 / R12 fixture. Expected values are derived in the comments,
not from the engine."""
import unittest
from sim.engine import simulate
from sim.metrics import summarize, check_max_headway, MetricsAccumulator, WaitStats
from sim.models import Config, Trip, SimulationError
from sim.run_inputs import RunInputs, expand
from sim.adapters.opt_adapter import route_of_trips

# Stops A,B,C,D. No trip visits D, and none visits C before A.
TRIPS = (
    Trip('t1', 'v1', 2, ('A', 'B', 'C'), (100, 200, 300), (110, 210, 300), 400, 1000),
    Trip('t2', 'v1', 2, ('A', 'B', 'C'), (700, 800, 900), (710, 810, 900), 1000, 1000),
    Trip('t3', 'v2', 5, ('A', 'B', 'C'), (400, 500, 1000), (410, 510, 1000), 1050, 500),
)
ROWS = [('a', 'A', 'C', 0, 3),    # a0,a1 board t1@100 -> served (wait 100); a2 refused, wait-expires @300
        ('b', 'B', 'C', 150, 1),  # t1 full @200 -> refused; wait-expires @450 (t3 reaches B @500)
        ('c', 'A', 'D', 0, 1),    # D never served -> no_connection
        ('d', 'C', 'A', 50, 1),   # no C->A visit order -> no_connection
        ('e', 'A', 'B', 750, 1),  # last A visit is 700 < 750 -> service_end (expires @1050 <= drain)
        ('f', 'B', 'C', 820, 1),  # deadlines 1120/1420 > drain 1100 -> censored, still waiting
        ('g', 'A', 'C', 380, 1)]  # boards t3@400 (wait 20), journey deadline 980 < arrival 1000
CONFIG = Config(300, 600, 1100, 'fifo', 'fixed_segments')
AREA = {'A': 'dong-1', 'B': 'dong-1', 'C': 'dong-2', 'D': 'dong-2'}


def run(config=CONFIG, rows=ROWS, trips=TRIPS):
    inputs = RunInputs('hc', 'hc', 'hc', '2026-11-09', expand(rows), (), 'hc')
    return simulate('p', 1, inputs, trips, config)


class HandCalculatedMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run()
        cls.m = summarize(cls.result, wait_threshold_s=60, stop_area=AREA,
                          headway=check_max_headway(TRIPS, {'t1': 'r1', 't2': 'r1', 't3': 'r2'}, 600))
        cls.by_id = {s.passenger.passenger_id: s for s in cls.result.passengers}

    def test_outcomes_and_reasons_per_passenger(self):
        expected = {'["a",0]': ('served', None), '["a",1]': ('served', None), '["a",2]': ('unserved', 'wait'),
                    '["b",0]': ('unserved', 'wait'), '["c",0]': ('unserved', 'no_connection'),
                    '["d",0]': ('unserved', 'no_connection'), '["e",0]': ('unserved', 'service_end'),
                    '["f",0]': ('censored', None), '["g",0]': ('unserved', 'journey')}
        self.assertEqual({k: (s.outcome, s.reason) for k, s in self.by_id.items()}, expected)
        self.assertEqual((self.by_id['["g",0]'].physical, self.by_id['["g",0]'].arrival_s), ('arrived', 1000))
        self.assertEqual({k for k, s in self.by_id.items() if s.refused}, {'["a",2]', '["b",0]'})

    def test_m1_bounds_with_censored(self):
        m1 = self.m['M1']
        self.assertEqual((m1['served'], m1['unserved'], m1['censored'], m1['total']), (2, 6, 1, 9))
        self.assertIsNone(m1['rate'])
        self.assertAlmostEqual(m1['lower'], 2 / 9)
        self.assertAlmostEqual(m1['upper'], 3 / 9)

    def test_m2(self):
        m2 = self.m['M2']
        self.assertEqual(m2['unserved_by_reason'], {'no_connection': 2, 'service_end': 1, 'journey': 1, 'wait': 2})
        self.assertEqual(m2['refusal_events'], 2)
        self.assertEqual(m2['passengers_refused_by_outcome'], {'served': 0, 'unserved': 2, 'censored': 0})
        self.assertEqual(m2['censored_by_physical'], {'waiting': 1})

    def test_m3_populations(self):
        served, boarded = self.m['M3']['served'], self.m['M3']['boarded']
        self.assertEqual((served['n'], served['mean_s'], served['p90_s']), (2, 100, 100))
        self.assertEqual((served['over_threshold_n'], served['over_threshold_rate']), (2, 1.0))
        # boarded waits: a0 100, a1 100, g 20 -> nearest-rank P90 = 3rd of [20,100,100]
        self.assertEqual((boarded['n'], boarded['p90_s'], boarded['over_threshold_n']), (3, 100, 2))
        self.assertAlmostEqual(boarded['mean_s'], 220 / 3)
        self.assertEqual(self.m['M3']['not_boarded_unserved_by_reason'],
                         {'no_connection': 2, 'service_end': 1, 'journey': 0, 'wait': 2})
        self.assertEqual(self.result.p90_wait_s, 100)

    def test_m5_areas(self):
        m5 = self.m['M5']
        self.assertEqual(m5['by_origin_area']['dong-1'],
                         {'total': 8, 'served': 2, 'unserved': 5, 'censored': 1, 'served_rate': 0.25})
        self.assertEqual(m5['by_origin_area']['dong-2'],
                         {'total': 1, 'served': 0, 'unserved': 1, 'censored': 0, 'served_rate': 0.0})
        self.assertEqual(m5['min_served_rate'], {'area': 'dong-2', 'rate': 0.0})
        unmapped = summarize(self.result, wait_threshold_s=60, stop_area={'A': 'x'})['M5']['by_origin_area']
        self.assertEqual(unmapped['unknown']['total'], 3)  # b, d, f start at unmapped B/C
        self.assertIsNone(summarize(self.result, wait_threshold_s=60)['M5'])

    def test_m6_vehicles(self):
        v1, v2 = self.m['M6']['vehicles']
        # v1 empty: t1 dead-head 100 + t2 legs 90+90 + t2 dead-head 100; t3 dead-head 50
        self.assertEqual(v1, {'vehicle_id': 'v1', 'trips_completed': 2, 'boarded': 2, 'served_alighted': 2,
                              'service_time_s': 600, 'distance_m': 2000, 'empty_time_s': 380})
        self.assertEqual(v2, {'vehicle_id': 'v2', 'trips_completed': 1, 'boarded': 1, 'served_alighted': 0,
                              'service_time_s': 650, 'distance_m': 500, 'empty_time_s': 50})
        self.assertIsNone(self.m['M6']['empty_distance_m'])
        self.assertEqual(sum(v['distance_m'] for v in self.m['M6']['vehicles']), self.result.distance_m)

    def test_r12(self):
        self.assertTrue(self.m['G']['R12']['pass'])
        self.assertEqual(self.m['G']['R12']['max_gap_by_route_s'], {'r1': 600})
        bad = check_max_headway(TRIPS, {'t1': 'r1', 't2': 'r1', 't3': 'r2'}, 599)
        self.assertFalse(bad['pass'])
        self.assertEqual([(v['stop'], v['gap_s']) for v in bad['violations']], [('A', 600), ('B', 600), ('C', 600)])
        with self.assertRaises(SimulationError): check_max_headway(TRIPS, {'t1': 'r1'}, 600)

    def test_no_censored_rate_and_zero_demand(self):
        m1 = summarize(run(Config(300, 600, 2000, 'fifo', 'fixed_segments')), wait_threshold_s=60)['M1']
        self.assertEqual(m1['censored'], 0)
        self.assertAlmostEqual(m1['rate'], 2 / 9)
        empty = summarize(run(rows=[]), wait_threshold_s=60)
        self.assertIsNone(empty['M1']['rate'])
        self.assertIsNone(empty['M1']['lower'])
        self.assertIsNone(empty['M3']['served']['p90_s'])


class ReasonTieTests(unittest.TestCase):
    def test_equal_deadlines_journey_wins(self):
        trips = (Trip('0', 'v', 2, ('A', 'B'), (0, 5), (0, 5), 10, 100),
                 Trip('1', 'v', 2, ('A', 'B'), (10, 15), (10, 15), 20, 100))
        r = run(Config(9, 9, 20, 'fifo', 'fixed_segments'), [('d', 'A', 'B', 0, 3)], trips)
        self.assertEqual([(s.outcome, s.reason) for s in r.passengers][-1], ('unserved', 'journey'))
        self.assertEqual(r.refusal_count, 1)

    def test_full_on_arrival_counts_refusal(self):
        trips = (Trip('0', 'v', 1, ('A', 'B', 'C'), (0, 5, 9), (0, 5, 9), 12, 1),)
        r = run(Config(30, 30, 40, 'fifo', 'fixed_segments'), [('x', 'A', 'C', 0, 1), ('y', 'B', 'C', 0, 2)], trips)
        self.assertEqual(r.refusal_count, 2)  # vehicle arrives at B already full; y0, y1 left behind


class AccumulatorTests(unittest.TestCase):
    def test_wait_stats_exact(self):
        w = WaitStats(10)
        for x in (5, 11, 11, 0, 30, 10, 10, 10, 9, 1): w.add(x)
        s = w.summary('x')
        self.assertEqual((s['n'], s['p90_s'], s['over_threshold_n']), (10, 11, 3))
        self.assertEqual(s['mean_s'], 9.7)
        with self.assertRaises(SimulationError): w.add(-1)

    def test_rejects_inconsistent_state(self):
        r = run()
        a = MetricsAccumulator(wait_threshold_s=60)
        with self.assertRaises(SimulationError):
            from dataclasses import replace
            a.add(replace(r.passengers[0], outcome=None))
        with self.assertRaises(SimulationError):
            summarize(r, wait_threshold_s=60, accumulator=MetricsAccumulator(wait_threshold_s=60))

    def test_adapter_route_identity(self):
        self.assertEqual(route_of_trips((Trip('r1:v1:00000000', 'v1', 1, ('A', 'B'), (0, 1), (0, 1), 2, 1),)),
                         {'r1:v1:00000000': 'r1'})


if __name__ == '__main__': unittest.main()
