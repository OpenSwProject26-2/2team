import io
import json
from collections import Counter
from dataclasses import replace
import unittest
from sim.arrivals import (BinRow, GENERATOR, generate, validate_realization, write_realization,
                          read_realization, realization_checksum)
from sim.models import Config, Passenger, SimulationError, Trip
from sim.run_inputs import RunInputs
from sim.engine import simulate
from sim.events import JsonlSink

ROWS = (BinRow('d1', 'A', 'C', 25200, 600, 50), BinRow('d2', 'B', 'C', 25800, 600, 30),
        BinRow('d0', 'C', 'A', 25200, 600, 0))
# Pinned with generator uniform_bin_sha256_v1; a change means the realization changed.
PINNED = '7803c4e1a75801e41d26e61f09fd3468bf406877279d63eff70a9851179f5ba4'


def retime(realization, index, time_s):
    passengers = list(realization.passengers)
    passengers[index] = replace(passengers[index], time_s=time_s)
    return reseal(realization, passengers)


def reseal(realization, passengers):
    passengers = tuple(passengers)
    return replace(realization, passengers=passengers, checksum=realization_checksum(
        realization.generator, realization.seed, realization.source_checksum, passengers))


class GeneratorTests(unittest.TestCase):
    def test_same_seed_same_realization_and_pinned_checksum(self):
        a, b = generate(ROWS, 'fixture'), generate(tuple(reversed(ROWS)), 'fixture')
        self.assertEqual(a, b)
        self.assertEqual(a.checksum, PINNED)
        self.assertEqual(a.generator, GENERATOR)

    def test_selected_other_seed_differs(self):
        # Holds for this selected fixture only; seeds are not guaranteed to differ in general.
        a, b = generate(ROWS, 'fixture'), generate(ROWS, 'fixture-2')
        self.assertNotEqual(a.checksum, b.checksum)
        self.assertNotEqual([p.time_s for p in a.passengers], [p.time_s for p in b.passengers])
        self.assertEqual([p.passenger_id for p in a.passengers], [p.passenger_id for p in b.passengers])

    def test_counts_od_and_bin_bounds_preserved(self):
        r = generate(ROWS, 'fixture')
        self.assertEqual(r.total, 80)
        per_row = Counter(json.loads(p.passenger_id)[0] for p in r.passengers)
        self.assertEqual(per_row, {'d1': 50, 'd2': 30})
        rows = {x.aggregate_id: x for x in ROWS}
        for p in r.passengers:
            row = rows[json.loads(p.passenger_id)[0]]
            self.assertEqual((p.origin, p.destination), (row.origin, row.destination))
            self.assertTrue(row.bin_start_s <= p.time_s < row.bin_start_s + row.bin_width_s)
            self.assertIs(type(p.time_s), int)

    def test_width_one_and_zero_demand(self):
        rows = (BinRow('x', 'A', 'B', 100, 1, 5), BinRow('z', 'A', 'B', 0, 600, 0))
        a, b = generate(rows, 's1'), generate(rows, 's2')
        self.assertEqual([p.time_s for p in a.passengers], [100] * 5)
        self.assertEqual([p.time_s for p in a.passengers], [p.time_s for p in b.passengers])
        self.assertEqual(generate((), 's').total, 0)

    def test_uniform_within_bin(self):
        # Deterministic for the fixed seed. 10 sub-bins of 60 s, chi-square 9 df, 0.999 crit 27.88.
        r = generate((BinRow('u', 'A', 'B', 0, 600, 6000),), 'uniformity')
        counts = Counter(p.time_s // 60 for p in r.passengers)
        chi2 = sum((counts[i] - 600) ** 2 / 600 for i in range(10))
        self.assertLess(chi2, 27.88)
        self.assertEqual(sum(counts.values()), 6000)
        self.assertGreaterEqual(min(p.time_s for p in r.passengers), 0)
        self.assertLess(max(p.time_s for p in r.passengers), 600)

    def test_invalid_rows(self):
        for rows in ((BinRow('a', 'A', 'A', 0, 600, 1),), (BinRow('a', 'A', 'B', 0, 0, 1),),
                     (BinRow('a', 'A', 'B', 0, 600, -1),), (BinRow('a', 'A', 'B', 0.5, 600, 1),),
                     (BinRow('a', 'A', 'B', 0, 600, 1), BinRow('a', 'B', 'C', 0, 600, 1))):
            with self.assertRaises(SimulationError): generate(rows, 's')
        with self.assertRaises(SimulationError): generate(ROWS, '')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.r = generate(ROWS, 'fixture')

    def test_accepts_generated_and_regenerated(self):
        validate_realization(self.r, ROWS, expected_checksum=PINNED, regenerate=True)

    def test_accepts_external_realization_inside_bins_but_not_regeneration(self):
        external = retime(self.r, 0, 25200)  # e.g. a front-loaded sensitivity realization
        validate_realization(external, ROWS)
        with self.assertRaises(SimulationError): validate_realization(external, ROWS, regenerate=True)
        with self.assertRaises(SimulationError): validate_realization(external, ROWS, expected_checksum=PINNED)

    def test_rejects_tampering(self):
        p0 = self.r.passengers[0]
        cases = (
            retime(self.r, 0, 25800),  # outside [25200, 25800)
            reseal(self.r, self.r.passengers[1:]),  # lost passenger
            reseal(self.r, self.r.passengers + (self.r.passengers[0],)),  # duplicate
            reseal(self.r, (replace(p0, destination='B'),) + self.r.passengers[1:]),  # OD changed
            reseal(self.r, (replace(p0, passenger_id='["d1",50]'),) + self.r.passengers[1:]),
            reseal(self.r, (replace(p0, passenger_id='["zz",0]'),) + self.r.passengers[1:]),
            replace(self.r, checksum='0' * 64),
            replace(self.r, seed='other'),  # checksum covers seed
            replace(self.r, source_checksum='wrong'),
        )
        for case in cases:
            with self.assertRaises(SimulationError): validate_realization(case)
        with self.assertRaises(SimulationError):
            validate_realization(self.r, ROWS[:2] + (replace(ROWS[2], passenger_count=1),))

    def test_file_roundtrip(self):
        stream = io.StringIO()
        write_realization(self.r, stream)
        stream.seek(0)
        self.assertEqual(read_realization(stream, ROWS, expected_checksum=PINNED), self.r)
        lines = stream.getvalue().splitlines()
        lines[1] = json.dumps([self.r.passengers[0].passenger_id, 'A', 'C', 99999])
        with self.assertRaises(SimulationError): read_realization(io.StringIO('\n'.join(lines)))

    def test_same_realization_same_log(self):
        run = RunInputs('b', 's', 'src', '2026-11-09', self.r.passengers, (('x', 1),), self.r.seed)
        trips = tuple(Trip(f't{j}', f'v{j}', 10, ('A', 'B', 'C'), (25200 + 300 * j, 25260 + 300 * j, 25320 + 300 * j),
                           (25230 + 300 * j, 25290 + 300 * j, 25320 + 300 * j), 25500 + 300 * j, 100) for j in range(4))
        config = Config(600, 1800, 30000, 'fifo', 'fixed_segments')
        logs = []
        for _ in range(2):
            s = io.StringIO()
            simulate('p', 1, run, trips, config, JsonlSink(s))
            logs.append(s.getvalue())
        self.assertEqual(logs[0], logs[1])
        self.assertTrue(logs[0])


if __name__ == '__main__': unittest.main()
