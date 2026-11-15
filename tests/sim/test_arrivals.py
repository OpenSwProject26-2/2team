"""승객 발생 실현기: simulator.md §2, §9.6 T5."""

import random
import tempfile
import unittest
from pathlib import Path

from busstrike.sim import DemandFlow, SimError, Window, realize_arrivals, realize_od, u_micro
from busstrike.sim.arrivals import MICRO, arrivals_bytes, to_micro

from tests.sim import cases

BINS = [0, 600, 1200]
FLOWS = [2_500_000, 0, 1_200_000]


class T5RealizeOdTests(unittest.TestCase):
    def test_table(self):
        table = {
            0: [0, 240, 480, 1450],
            500_000: [120, 360, 1200, 1700],
            699_999: [167, 407, 1299, 1799],
            700_000: [168, 408, 1300],
            800_000: [192, 432, 1350],
        }
        for u, expected in table.items():
            with self.subTest(u=u):
                self.assertEqual(realize_od(FLOWS, BINS, 600, u), expected)

    def test_count_is_floor_or_ceil_of_total(self):
        for u in range(0, MICRO, 50_000):
            n = len(realize_od(FLOWS, BINS, 600, u))
            self.assertEqual(n, 4 if u < 700_000 else 3)

    def test_fixed_u_vectors(self):
        self.assertEqual(u_micro(42, "access", ["A"], ["S1", "S2"]), 699417)
        self.assertEqual(u_micro(42, "egress", ["S1", "S2"], ["A"]), 480388)
        self.assertEqual(u_micro(7, "access", ["B"], ["S"]), 658692)
        self.assertEqual(u_micro("42", "access", ["A"], ["S2", "S1"]), 699417)

    def test_decimal_flow_has_no_float_error(self):
        self.assertEqual(sum(to_micro("0.1") for _ in range(10)), 1_000_000)
        self.assertEqual(to_micro(0.1), 100_000)
        self.assertEqual(to_micro("0.0000005"), 0)       # ROUND_HALF_EVEN
        self.assertEqual(to_micro("0.0000015"), 2)
        with self.assertRaises(SimError):
            to_micro("-1")


class RealizeArrivalsTests(unittest.TestCase):
    STATIONS = {"ST1": ["S1", "S2"]}

    def rows(self):
        return [
            DemandFlow("access", "A", "ST1", 0, "0.1"),
            DemandFlow("access", "A", "ST1", 900, "1.7"),
            DemandFlow("egress", "A", "ST1", 0, "2.25"),
            DemandFlow("access", "B", "ST1", 900, "0.3"),
            DemandFlow("access", "A", "ST1", 900, "0.2"),      # 같은 OD·시간대 행은 합친다
        ] + [DemandFlow("access", "C", "ST1", b * 900, "0.1") for b in range(10)]

    def realize(self, rows, seed=42):
        return realize_arrivals(rows, self.STATIONS, cases.f1_policy(), Window(0, 9000), seed, 900)

    def test_fields_and_ids(self):
        out = self.realize(self.rows())
        self.assertEqual([p.arrival_s for p in out], sorted(p.arrival_s for p in out))
        access_a = [p for p in out if p.kind == "access" and p.origin_stop_ids == ("A",)]
        self.assertTrue(all(p.dest_stop_ids == ("S1", "S2") for p in access_a))
        egress = [p for p in out if p.kind == "egress"]
        self.assertTrue(egress and all(p.origin_stop_ids == ("S1", "S2") and p.dest_stop_ids == ("A",)
                                       for p in egress))
        self.assertEqual(len(egress), 2)  # 2.25명 → 2 또는 3, u=480388 ≥ 0.25M 이므로 2
        self.assertEqual(len(access_a), 2)  # 0.1 + 1.9 = 2.0명
        self.assertEqual(len([p for p in out if p.origin_stop_ids == ("C",)]), 1)  # 0.1 × 10 = 정확히 1명
        self.assertTrue(all(p.max_wait_s == 600 for p in out))
        ids = [p.passenger_id for p in out]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(i) == len("p000001-00001") for i in ids))

    def test_row_order_does_not_change_bytes(self):
        rows = self.rows()
        base = arrivals_bytes(self.realize(rows))
        rng = random.Random(3)
        for _ in range(5):
            shuffled = rows[:]
            rng.shuffle(shuffled)
            self.assertEqual(arrivals_bytes(self.realize(shuffled)), base)
        with tempfile.TemporaryDirectory() as d:
            from busstrike.sim import write_arrivals
            sha = write_arrivals(self.realize(rows), Path(d) / "arrivals.jsonl")
            self.assertEqual((Path(d) / "arrivals.jsonl").read_bytes(), base)
            self.assertEqual(len(sha), 64)

    def test_seed_changes_phase_not_totals(self):
        a = self.realize(self.rows(), seed=1)
        b = self.realize(self.rows(), seed=2)
        self.assertLessEqual(abs(len(a) - len(b)), 4)

    def test_errors(self):
        with self.assertRaises(SimError) as cm:
            realize_arrivals([DemandFlow("access", "S1", "ST1", 0, "1")], self.STATIONS, cases.f1_policy(),
                             Window(0, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "INPUT_INVALID")  # 출발 = 목적 집합 겹침
        with self.assertRaises(SimError) as cm:
            realize_arrivals([DemandFlow("access", "A", "ST1", 0, "1")], self.STATIONS, cases.f1_policy(),
                             Window(1000, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "INPUT_INVALID")  # 창 밖 도착
        from dataclasses import replace
        poisson = replace(cases.f1_policy(), arrival_method="poisson")
        with self.assertRaises(SimError) as cm:
            realize_arrivals([], self.STATIONS, poisson, Window(0, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "OUT_OF_SCOPE")


if __name__ == "__main__":
    unittest.main()
