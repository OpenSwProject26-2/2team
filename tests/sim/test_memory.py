"""P3: lazy event streams, pending-key pruning, streamed outcomes and checksum must not
change any observable result."""
from collections import Counter
from dataclasses import asdict
import hashlib
import io
import json
import unittest
from sim.dispatcher import Dispatcher
from sim.engine import simulate
from sim.events import JsonlSink
from sim.metrics import MetricsAccumulator, summarize
from sim.models import Passenger, SimulationError
from sim.run_inputs import RunInputs, digest, expand
from tests.sim.golden_cases import GOLDEN, cases, outcome_digest
from tests.sim.test_metrics import AREA, CONFIG, TRIPS, ROWS


def outcome_key(s):
    return (s.passenger.passenger_id, s.outcome, s.reason, s.physical, s.vehicle_id, s.boarding_s,
            s.arrival_s, s.refused)


class StreamingEquivalenceTests(unittest.TestCase):
    def test_golden_cases_streaming(self):
        expected = json.loads((GOLDEN / 'golden.json').read_text())
        for name, run, trips, config in cases():
            with self.subTest(case=name):
                streamed, log = [], io.StringIO()
                r = simulate('golden', 1, run, trips, config, JsonlSink(log), outcome_sink=streamed.append)
                self.assertEqual(hashlib.sha256(log.getvalue().encode()).hexdigest(), expected[name]['log_sha256'])
                self.assertEqual(r.passengers, ())
                self.assertEqual((r.total, r.served, r.unserved, r.censored, r.p90_wait_s, r.event_count),
                                 tuple(expected[name][k] for k in ('passengers', 'served', 'unserved', 'censored',
                                                                    'p90_wait_s', 'event_count')))
                ids = [s.passenger.passenger_id for s in streamed]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(len(ids), r.total)
                batch = simulate('golden', 1, run, trips, config)
                self.assertEqual(sorted(map(outcome_key, streamed)), sorted(map(outcome_key, batch.passengers)))
                self.assertEqual(outcome_digest(batch), expected[name]['outcome_sha256'])

    def test_streamed_metrics_equal_batch(self):
        run = RunInputs('hc', 'hc', 'hc', '2026-11-09', expand(ROWS), (), 'hc')
        acc = MetricsAccumulator(wait_threshold_s=60, stop_area=AREA)
        streamed = simulate('p', 1, run, TRIPS, CONFIG, outcome_sink=acc.add)
        batch = simulate('p', 1, run, TRIPS, CONFIG)
        self.assertEqual(summarize(streamed, wait_threshold_s=60, accumulator=acc),
                         summarize(batch, wait_threshold_s=60, stop_area=AREA))

    def test_final_states_are_final(self):
        # A state passed to the sink must not change afterwards.
        run = RunInputs('hc', 'hc', 'hc', '2026-11-09', expand(ROWS), (), 'hc')
        snapshots, states = [], []
        def sink(s):
            states.append(s); snapshots.append(outcome_key(s))
        simulate('p', 1, run, TRIPS, CONFIG, outcome_sink=sink)
        self.assertEqual([outcome_key(s) for s in states], snapshots)
        self.assertTrue(all(s.outcome in ('served', 'unserved', 'censored') for s in states))


class DispatcherStreamTests(unittest.TestCase):
    def test_stream_order_equals_push_and_keys_pruned(self):
        keys = [(t, p, s) for t in range(3) for p in range(5) for s in 'ab']
        pushed, streamed = [], []
        d = Dispatcher()
        for k in keys: d.add(*k, lambda *k: pushed.append(k))
        d.run(10)
        d = Dispatcher()
        d.add_stream(((k, lambda *k: streamed.append(k)) for k in keys[::2]))
        d.add_stream(((k, lambda *k: streamed.append(k)) for k in keys[1::2]))
        self.assertLessEqual(len(d.queue), 2)
        d.run(10)
        self.assertEqual(pushed, keys)
        self.assertEqual(streamed, keys)
        self.assertEqual(d.keys, set())
        with self.assertRaises(SimulationError): d.add(0, 0, 'a', lambda *k: None)

    def test_unsorted_or_duplicate_stream_rejected(self):
        d = Dispatcher()
        d.add_stream((k, lambda *k: None) for k in [(1, 0, 'a'), (0, 0, 'b')])
        with self.assertRaises(SimulationError): d.run(10)
        d = Dispatcher()
        d.add(1, 0, 'a', lambda *k: None)
        with self.assertRaises(SimulationError): d.add_stream([((1, 0, 'a'), lambda *k: None)])

    def test_drain_stops_stream(self):
        seen, d = [], Dispatcher()
        d.add_stream(((t, 0, 'x'), lambda *k: seen.append(k[0])) for t in range(100))
        d.run(4)
        self.assertEqual(seen, [0, 1, 2, 3, 4])


class ChecksumTests(unittest.TestCase):
    def test_streamed_checksum_identical(self):
        runs = [RunInputs('b', 's', 'src', '2026-11-09', expand([('d', 'A', 'B', 0, 3)]), (('ab', 5), ('ba', 5)), 'f'),
                RunInputs('번들"\\', '시나리오', 'src ', '2026-11-09', (Passenger('p,"1"', '정류장\n', 'B', 7),), (), 's'),
                RunInputs('b', 's', 'src', '2026-11-09', (), (), 's')]
        for r in runs: self.assertEqual(r.checksum, digest(asdict(r)))


if __name__ == '__main__': unittest.main()
