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

from test_routegen import _segments, _stop, scenario  # noqa: E402
from test_tour import ENDS, LINES, demand, params, reach_rule, run, tour_network, tours  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.data import Demand, Network, Station  # noqa: E402
from busstrike.planning.routegen.loop import cyclic_seq  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach, StationIndex  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402


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



# 소외 가산점 망: KE → A1 → A2 → KA(역 K), A1 → X → A2(역 가까운 정류장 X), A1 → F → A2(역 800m 밖 F). X와 F는 같은 자리를 다툰다.
F_EDGES = [("KE", "A1"), ("A1", "A2"), ("A2", "KA"), ("KA", "KE"), ("A1", "X"), ("X", "A2"), ("A1", "F"), ("F", "A2")]
F_POS = {"KE": (0, 0), "A1": (1, 0), "A2": (2, 0), "KA": (0, 0.3), "X": (1.5, 0.3), "F": (1.5, -0.3)}
F_OD = {("A1", "KA"): 30.0, ("X", "KA"): 12.0, ("F", "KA"): 10.0}


def fill_net() -> Network:
    stops = {s: _stop(s, x, y) for s, (x, y) in F_POS.items()}
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, F_EDGES)})
    turns: dict = {"_meta": {"format": "turns-v2"}}
    for a, b in F_EDGES:
        turns[f"{a}>{b}"] = {"s": f"n{a}", "e": f"n{b}", "tail": [[f"{a}{b}t", f"{a}{b}u"]], "head": [[f"{a}{b}h", f"{a}{b}i"]]}
    st = {"K": Station("K", "역K", stops["KA"].lat, stops["KA"].lon, "KA", "KE")}
    return Network(tt, stops, st, turns)


def fill_run(bonus: float):
    net = fill_net()
    d = Demand("v", ("20261026",))
    for (o, t), v in F_OD.items():
        d.od[(o, t)] = [v / 4] * 4
        d.pair[("access", o, "K")] = [v / 4] * 4
    ix = StationIndex({("K", "access"): "KA", ("K", "egress"): "KE"}, {"K": {"KA", "KE"}}, {"K": frozenset({"1"})})
    reach = Reach(ix, EvalRule(walk_radius_m=0.0, station_mode="fixed"), {}, sorted(F_OD))
    p = G.Params(distance_basis="network", loop="demand", l_max_min=(15,), hub_min=1.0, radius_m=10000.0, max_stops=None,
                 tour=("feeder",), tour_only=True, budget_h_s=700, budget_ns=(1,), fill=True, fill_bonus=bonus,
                 fill_top=1)
    return G.generate(net, d, scenario(), p, reach=reach, tour_inputs=T.TourInputs({"K": (("KE",), ("KA",))},
                                                                                   {"K": ("역K", frozenset({"1"}))}),
                      far_stops=frozenset({"F"}))


class FillTest(unittest.TestCase):
    """4단계 소외 우선 삽입: 골격(②-T)에서 n × H까지 삽입, 점수 = (탑승 근사 증가 + β × 소외 수요 증가) ÷ 순환 증가."""

    def test_bonus_zero_picks_near_stop(self):
        got = [c for c in fill_run(0.0).candidates if c.origin == "tour_fill"]
        self.assertTrue(got)
        self.assertTrue(all("X" in c.stops and "F" not in c.stops for c in got), [c.stops for c in got])

    def test_bonus_picks_isolated_stop(self):
        got = [c for c in fill_run(1.0).candidates if c.origin == "tour_fill"]
        self.assertTrue(got)
        self.assertTrue(all("F" in c.stops for c in got), [c.stops for c in got])

    def test_fill_within_budget_and_not_worse_than_skeleton(self):
        res = run(**budget_params(tour=("feeder",), fill=True, fill_top=2))
        fills = [c for c in res.candidates if c.origin == "tour_fill"]
        feeders = {(t.split(":", 1)[1], c.vehicles): c for c in res.candidates if c.origin == "tour_feeder"
                   for t in c.made_by if t.startswith("tsp:")}
        self.assertTrue(fills)
        g = Graph(tour_network(), 10, 3600, "network")
        for c in fills:
            self.assertLessEqual(c.cycle_s, c.vehicles * 600)
            self.assertFalse(g.has_uturn(cyclic_seq(c.stops, c.return_stops)))
            seed = next(t.split(":", 1)[1] for t in c.made_by if t.startswith("fill:"))
            sk = feeders.get((seed, c.vehicles))
            if sk is not None:
                self.assertGreaterEqual(c.cover, sk.cover - 1e-9)

    def test_fill_off_by_default(self):
        self.assertFalse(G.Params().fill)
        self.assertEqual(G.Params().fill_bonus, 0.0)


if __name__ == "__main__":
    unittest.main()
