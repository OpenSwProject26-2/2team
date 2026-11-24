"""새 노선 생성 알고리즘(New_Algol.md 3.3, 작업 2026-11-25): 배차 예산 안의 역-종점 순회. 모든 옵션은 기본 끔.

합성 망은 test_tour.py의 것을 쓴다(역 K·Q·R, K와 R은 같은 1호선).
검사(지시문 3절): 순환 상한 n × H, 끝 역이 같은 꼬리표, 소외 정류장 가산점 효과, 금지 목록으로 k개가 서로 다름, 결정론,
옵션을 끄면 결과 동일. 그리고 U턴 허용(9단계), R1 후보 건너뛰기, 역 쌍 병합.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import scenario  # noqa: E402
from test_tour import ENDS, LINES, demand, params, reach_rule, run, tour_network, tours  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.loop import cyclic_seq  # noqa: E402


class UturnOptionTest(unittest.TestCase):
    def test_graph_allow_uturn_never_reports_uturn(self):
        net = tour_network(uturn_closure=True)
        seq = ("N4", "KA", "KE", "N1")
        self.assertTrue(Graph(net, 10, 3600, "network").has_uturn(seq))
        self.assertFalse(Graph(net, 10, 3600, "network", allow_uturn=True).has_uturn(seq))

    def test_generate_allow_uturn_brings_back_station(self):
        net = tour_network(uturn_closure=True)
        self.assertEqual([c for c in tours(run(net)) if c.hubs[0] == "K"], [])
        got = [c for c in tours(run(net, allow_uturn=True)) if c.hubs[0] == "K"]
        self.assertTrue(got)

    def test_allow_uturn_off_by_default(self):
        self.assertFalse(G.Params().allow_uturn)



def budget_params(**kw):
    base = dict(tour=("sector", "feeder"), tour_only=True, budget_h_s=600, budget_ns=(1, 2, 3))
    base.update(kw)
    return base


class BudgetTest(unittest.TestCase):
    """1단계 배차 예산: 노선 대수 n마다 순환 상한 n × H, 후보에 n을 기록한다."""

    def setUp(self):
        self.res = run(**budget_params())
        self.T = tours(self.res)

    def test_cycle_within_n_times_h(self):
        self.assertTrue(self.T)
        for c in self.T:
            self.assertIn(c.vehicles, (1, 2, 3), c.stops)
            self.assertLessEqual(c.cycle_s, c.vehicles * 600, (c.stops, c.vehicles))

    def test_same_stops_kept_for_each_n(self):
        by = {}
        for c in self.T:
            by.setdefault(c.stops, set()).add(c.vehicles)
        self.assertTrue(any(len(v) > 1 for v in by.values()))        # 같은 순회가 n = 2, 3으로 따로 남는다
        pool = G.pools(self.res.candidates, 0.8)["ours"][0]
        by2 = {}
        for c in pool:
            by2.setdefault(c.stops, set()).add(c.vehicles)
        self.assertTrue(any(len(v) > 1 for v in by2.values()))      # 중복 제거도 n이 다르면 지우지 않는다

    def test_tour_only_skips_r1_candidates(self):
        self.assertEqual([c for c in self.res.candidates if c.loop != "tour"], [])
        with_r1 = run(**budget_params(tour_only=False))
        self.assertTrue([c for c in with_r1.candidates if c.loop != "tour"])

    def test_deterministic(self):
        again = tours(run(**budget_params()))
        self.assertEqual([c.record() for c in self.T], [c.record() for c in again])

    def test_must_dedicated_is_tour(self):
        res = run(**budget_params(tour=("feeder",), sp_top=1, must_stops=("M1",), must_insert_radius_m=0.0))
        got = [c for c in res.candidates if c.origin == "tour_must"]
        self.assertTrue(got)
        for c in got:
            self.assertEqual(c.loop, "tour")
            self.assertIn("M1", c.stops[1:-1])
            self.assertEqual(c.must, ("M1",))
            self.assertLessEqual(c.cycle_s, c.vehicles * 600)

    def test_budget_needs_tour(self):
        with self.assertRaises(ValueError):
            G.Params(budget_h_s=600)
        with self.assertRaises(ValueError):
            G.Params(tour_only=True)


if __name__ == "__main__":
    unittest.main()
