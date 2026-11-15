"""승객 발생: simulator.md §2, §9.6 T5, m07-output-contract.md §4.1.

1. 시간대별 인원(체계적 반올림, `bin_counts`·`realize_bins`).
2. 시간대 안 시각(M07 생성기 `uniform_bin_sha256_v1`, `generate`). 검증기의 독립 구현과 같은 값이 나오는지
   테스트에서만 대조한다(엔진은 검증기를 import하지 않는다).
"""

import random
import unittest
from dataclasses import replace

from busstrike.sim import BinRow, DemandFlow, SimError, Window, bin_counts, generate, realize_bins, realize_od, u_micro
from busstrike.sim import io
from busstrike.sim.arrivals import (
    GENERATOR, MICRO, bin_offset, passenger_id, realization_bytes, realization_checksum, source_checksum, to_micro,
)

from tests.sim import cases

BINS = [0, 600, 1200]
FLOWS = [2_500_000, 0, 1_200_000]


class T5RealizeOdTests(unittest.TestCase):
    TABLE = {
        0: [0, 240, 480, 1450],
        500_000: [120, 360, 1200, 1700],
        699_999: [167, 407, 1299, 1799],
        700_000: [168, 408, 1300],
        800_000: [192, 432, 1350],
    }

    def test_table(self):
        for u, expected in self.TABLE.items():
            with self.subTest(u=u):
                self.assertEqual(realize_od(FLOWS, BINS, 600, u), expected)

    def test_bin_counts_follow_the_table(self):
        """시간대별 인원 = 표의 문턱 교차 시각이 그 시간대에 드는 개수(손계산: u=0 → [3,0,1])."""
        expected = {0: [3, 0, 1], 500_000: [2, 0, 2], 699_999: [2, 0, 2], 700_000: [2, 0, 1], 800_000: [2, 0, 1]}
        for u, times in self.TABLE.items():
            with self.subTest(u=u):
                self.assertEqual(bin_counts(FLOWS, u), expected[u])
                self.assertEqual(bin_counts(FLOWS, u), [sum(1 for t in times if b <= t < b + 600) for b in BINS])

    def test_count_is_floor_or_ceil_of_total(self):
        for u in range(0, MICRO, 50_000):
            self.assertEqual(sum(bin_counts(FLOWS, u)), 4 if u < 700_000 else 3)

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


class RealizeBinsTests(unittest.TestCase):
    def rows(self):
        return [DemandFlow("access", "A", "S1", 0, "2.5"), DemandFlow("access", "A", "S1", 900, "0"),
                DemandFlow("access", "A", "S1", 1800, "1.2"), DemandFlow("access", "B", "S2", 0, "0.4"),
                DemandFlow("egress", "A", "S1", 0, "2.25")]

    def test_rows_ids_and_totals(self):
        out = realize_bins(self.rows(), cases.f1_policy(), Window(0, 9000), 42, 900)
        self.assertTrue(all(r.bin_width_s == 900 and r.passenger_count > 0 for r in out))
        by_od = {}
        for r in out:
            by_od.setdefault((r.origin, r.destination), []).append(r)
        u_a, u_e = u_micro(42, "access", ["A"], ["S1"]), u_micro(42, "egress", ["S1"], ["A"])
        # 합 3.7명: u < 0.7M이면 4명, 아니면 3명. 2.25명: u < 0.25M이면 3명, 아니면 2명
        self.assertEqual(sum(r.passenger_count for r in by_od[("A", "S1")]), 4 if u_a < 700_000 else 3)
        self.assertEqual(sum(r.passenger_count for r in by_od[("S1", "A")]), 3 if u_e < 250_000 else 2)
        self.assertEqual(len({r.aggregate_id for r in out}), len(out))
        # OD 순번은 [kind, origin, destination] JSON 순: access A→S1 = 1, access B→S2 = 2, egress S1→A = 3
        self.assertTrue(all(r.aggregate_id.startswith("od000001-") for r in by_od[("A", "S1")]))
        self.assertTrue(all(r.aggregate_id.startswith("od000003-") for r in by_od[("S1", "A")]))
        u_b = u_micro(42, "access", ["B"], ["S2"])
        self.assertEqual(sum(r.passenger_count for r in by_od.get(("B", "S2"), [])), 1 if u_b < 400_000 else 0)

    def test_row_order_does_not_change_bytes(self):
        rows = self.rows()
        ref = io.demand_bins_bytes(realize_bins(rows, cases.f1_policy(), Window(0, 9000), 42, 900))
        for seed in range(5):
            random.Random(seed).shuffle(rows)
            self.assertEqual(io.demand_bins_bytes(realize_bins(rows, cases.f1_policy(), Window(0, 9000), 42, 900)),
                             ref)

    def test_errors(self):
        policy = cases.f1_policy()
        with self.assertRaises(SimError) as cm:
            realize_bins([DemandFlow("access", "S1", "S1", 0, "1")], policy, Window(0, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "INPUT_INVALID")      # 출발 = 목적
        with self.assertRaises(SimError) as cm:
            realize_bins([DemandFlow("access", "A", "S1", 8500, "1")], policy, Window(0, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "INPUT_INVALID")      # 시간대가 창 밖으로 나감
        with self.assertRaises(SimError) as cm:
            realize_bins([], replace(policy, arrival_method="poisson"), Window(0, 9000), 1, 900)
        self.assertEqual(cm.exception.code, "OUT_OF_SCOPE")


class GeneratorTests(unittest.TestCase):
    """계약 §4.1. 기댓값은 검증기 구현(`busstrike.evaluation.arrivals_ref`)과 대조한다."""

    def rows(self):
        rng = random.Random(7)
        return [BinRow(f"r{i:03d}", "A", "B", rng.randint(0, 5000), rng.choice([1, 7, 60, 900, 3600]),
                       rng.randint(0, 5)) for i in range(60)]

    def test_same_as_m07_reference(self):
        from busstrike.evaluation import arrivals_ref as ref
        from busstrike.evaluation.inputs import BinRow as RefRow
        rows = self.rows()
        ref_rows = [RefRow(r.aggregate_id, r.origin, r.destination, r.bin_start_s, r.bin_width_s, r.passenger_count)
                    for r in rows]
        for seed in ("a1", 42, "none"):
            mine = [(p.passenger_id, p.origin, p.destination, p.arrival_s) for p in generate(rows, seed)]
            self.assertEqual(mine, ref.generate(ref_rows, seed))
            self.assertEqual(source_checksum(rows), ref.source_checksum(ref_rows))
            self.assertEqual(realization_checksum(seed, source_checksum(rows), generate(rows, seed)),
                             ref.realization_checksum(seed, ref.source_checksum(ref_rows), ref.generate(ref_rows, seed)))

    def test_offset_inside_bin_and_ids(self):
        for p, r in zip(generate([BinRow("x", "A", "B", 100, 60, 50)], "s"), range(50)):
            self.assertTrue(100 <= p.arrival_s < 160)
            self.assertEqual(p.passenger_id, passenger_id("x", r))
        self.assertEqual(passenger_id("d", 2), '["d",2]')
        self.assertEqual({bin_offset("any", "x", i, 1) for i in range(20)}, {0})

    def test_realization_file(self):
        rows = [BinRow("b", "A", "B", 0, 1, 2), BinRow("a", "B", "A", 5, 1, 1)]
        data, checksum = realization_bytes(rows, "f1", generate(rows, "f1"))
        lines = data.decode().splitlines()
        self.assertIn(f'"generator":"{GENERATOR}"', lines[0])
        self.assertIn(f'"checksum":"{checksum}"', lines[0])
        self.assertEqual(lines[1:], ['["[\\"a\\",0]","B","A",5]', '["[\\"b\\",0]","A","B",0]',
                                     '["[\\"b\\",1]","A","B",0]'])


if __name__ == "__main__":
    unittest.main()
