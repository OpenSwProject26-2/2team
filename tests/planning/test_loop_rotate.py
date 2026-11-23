"""순환 시작점 최적화(loop_rotate): 같은 고리를 덮는 수요가 가장 큰 시작점으로 돌린다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from busstrike.planning.routegen.loop import best_rotation  # noqa: E402


def cover_of(od):
    """od = {(o, d): 수요}. 정차열에서 o가 d보다 앞이면 덮는다."""
    def f(seq):
        pos = {s: i for i, s in enumerate(seq)}
        return sum(v for (o, d), v in od.items() if o in pos and d in pos and pos[o] < pos[d])
    return f


class BestRotationTest(unittest.TestCase):
    def test_rotates_to_station_end(self):
        # 고리 H(동네) - S(역) - R(동네). 지금 시작 H: H→S만 덮음. R에서 시작하면 R→H→S로 R·H 둘 다 역에 닿는다
        seq = ("H", "S", "R")
        od = {("H", "S"): 10.0, ("R", "S"): 7.0}
        self.assertEqual(best_rotation(seq, cover_of(od), lambda a, b: True), 2)

    def test_keeps_zero_when_no_gain_or_tie(self):
        seq = ("S", "A", "B")
        od = {("S", "A"): 3.0, ("S", "B"): 2.0}
        self.assertEqual(best_rotation(seq, cover_of(od), lambda a, b: True), 0)

    def test_respects_closure_rule(self):
        seq = ("H", "S", "R")
        od = {("H", "S"): 10.0, ("R", "S"): 7.0}
        # 시작 2는 새 고리 닫기 (S → R) 구간을 쓴다. 그 구간이 안 되면 못 돌린다
        self.assertEqual(best_rotation(seq, cover_of(od), lambda a, b: (a, b) != ("S", "R")), 0)

    def test_keeps_must_stops_inside(self):
        seq = ("H", "S", "R")
        od = {("H", "S"): 10.0, ("R", "S"): 7.0}
        # 시작 2면 R이 첫 정류장 → 필수 정류장 R은 첫·끝이 아닌 정차여야 하므로 막는다
        self.assertEqual(best_rotation(seq, cover_of(od), lambda a, b: True, keep_inside=frozenset({"R"})), 0)

    def test_tie_prefers_smallest_index(self):
        seq = ("A", "B", "C", "D")
        od = {("D", "A"): 5.0}
        # 시작 1(B,C,D,A)·2(C,D,A,B)·3(D,A,B,C) 모두 D가 A 앞 → 같은 값이면 가장 작은 시작점 1
        self.assertEqual(best_rotation(seq, cover_of(od), lambda a, b: True), 1)


if __name__ == "__main__":
    unittest.main()
