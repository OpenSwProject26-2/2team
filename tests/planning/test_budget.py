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



TAGS = {"K": frozenset({"1"}), "Q": frozenset({"2"}), "R": frozenset({"1"})}


def lg_run(**kw):
    net = tour_network()
    return G.generate(net, demand(), scenario(), params(**budget_params(tour=("feeder",), **kw)),
                      reach=reach_rule(net), tour_inputs=T.TourInputs(ENDS, LINES, TAGS))


class LineGroupTest(unittest.TestCase):
    """2단계 호선 단위 묶기: 끝 역 = 씨앗에서 망 거리로 가장 가까운 같은 꼬리표 역."""

    def test_line_groups(self):
        from busstrike.planning.routegen.budget import line_groups
        self.assertEqual(line_groups(TAGS, ["K"], ["K", "Q", "R"]), {"1": ["K", "R"]})
        self.assertEqual(line_groups(TAGS, ["K", "Q"], ["K", "Q", "R"]), {"1": ["K", "R"], "2": ["Q"]})

    def test_end_station_has_group_tag(self):
        res = lg_run(line_group=True)
        got = [c for c in res.candidates if c.origin == "tour_feeder"]
        self.assertTrue(got)
        for c in got:
            tg = [t.split(":", 1)[1] for t in c.made_by if t.startswith("lg:")]
            self.assertTrue(tg, c.made_by)
            self.assertTrue(any(t in TAGS[c.hubs[0]] for t in tg))

    def test_nearer_same_line_station_becomes_end(self):
        # N3의 수요는 K(egress 15)가 R(egress 6)보다 크지만 망 거리로는 R이 가깝다 → 끝 역 R, 원래 역 K 기록
        res = lg_run(line_group=True)
        got = [c for c in res.candidates if c.origin == "tour_feeder" and "tsp:N3" in c.made_by and "lg:1" in c.made_by]
        self.assertTrue(got)
        self.assertTrue(all(c.hubs == ("R",) and "orig:K" in c.made_by for c in got), [(c.hubs, c.made_by) for c in got])

    def test_off_keeps_hub_ends(self):
        res = lg_run()
        self.assertFalse(any(t.startswith(("lg:", "orig:")) for c in res.candidates for t in c.made_by))

    def test_load_inputs_reads_tags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "network").mkdir()
            (Path(d) / "network" / "station_stops.csv").write_text(
                "station_id,station_name,kind,station_stop_id,status,candidates\n0001,가,access,s1,ok,s1 s2\n"
                "0001,가,egress,s2,ok,s1 s2\n", encoding="utf-8")
            (Path(d) / "lines.csv").write_text("station_id,station_name,lines,tags,complex\n0001,가,1,1-경부 1-경인,가\n",
                                               encoding="utf-8")
            got = T.load_inputs(d, Path(d) / "lines.csv")
            self.assertEqual(got.tags, {"0001": frozenset({"1-경부", "1-경인"})})



# 병합 망: 역 K(KE → a → KA)와 역 Q(QE → b → QA). KA → QE, QA → KE가 한 구간이라 두 순회를 한 고리로 이을 수 있다.
M_EDGES = [("KE", "a"), ("a", "KA"), ("KA", "KE"), ("QE", "b"), ("b", "QA"), ("QA", "QE"), ("KA", "QE"), ("QA", "KE")]
M_POS = {"KE": (0, 0), "a": (1, 0), "KA": (2, 0), "QE": (3, 0), "b": (4, 0), "QA": (3, 0.4)}
M_OD = {("a", "KA"): ("K", 30.0), ("b", "QA"): ("Q", 30.0), ("a", "QA"): ("Q", 20.0)}


def merge_run(merge: bool):
    stops = {s: _stop(s, x, y) for s, (x, y) in M_POS.items()}
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, M_EDGES)})
    turns: dict = {"_meta": {"format": "turns-v2"}}
    for a, b in M_EDGES:
        turns[f"{a}>{b}"] = {"s": f"n{a}", "e": f"n{b}", "tail": [[f"{a}{b}t", f"{a}{b}u"]], "head": [[f"{a}{b}h", f"{a}{b}i"]]}
    st = {"K": Station("K", "역K", stops["KA"].lat, stops["KA"].lon, "KA", "KE"),
          "Q": Station("Q", "역Q", stops["QA"].lat, stops["QA"].lon, "QA", "QE")}
    net = Network(tt, stops, st, turns)
    d = Demand("v", ("20261026",))
    for (o, t), (k, v) in M_OD.items():
        d.od[(o, t)] = [v / 4] * 4
        d.pair[("access", o, k)] = [v / 4] * 4
    ix = StationIndex({("K", "access"): "KA", ("K", "egress"): "KE", ("Q", "access"): "QA", ("Q", "egress"): "QE"},
                      {"K": {"KA", "KE"}, "Q": {"QA", "QE"}}, {"K": frozenset({"1"}), "Q": frozenset({"2"})})
    reach = Reach(ix, EvalRule(walk_radius_m=0.0, station_mode="fixed"), {}, sorted(M_OD))
    p = G.Params(distance_basis="network", loop="demand", l_max_min=(15,), hub_min=1.0, radius_m=10000.0, max_stops=None,
                 tour=("feeder",), tour_only=True, budget_h_s=600, budget_ns=(1, 2), merge=merge)
    ends = {"K": (("KE",), ("KA",)), "Q": (("QE",), ("QA",))}
    return G.generate(net, d, scenario(), p, reach=reach,
                      tour_inputs=T.TourInputs(ends, {"K": ("역K", frozenset({"1"})), "Q": ("역Q", frozenset({"2"}))}))


class MergeTest(unittest.TestCase):
    """5단계 역 쌍 병합: 끝점이 붙은 두 순회를 (n₁ + n₂) × H 안에서 합칠 때만, 근사가 나아질 때만."""

    def test_merge_makes_two_station_tour(self):
        got = [c for c in merge_run(True).candidates if c.origin == "tour_merge"]
        self.assertTrue(got)
        for c in got:
            self.assertEqual(c.vehicles, 2)
            self.assertLessEqual(c.cycle_s, 2 * 600)
            self.assertTrue({"KE", "KA", "QE", "QA"} <= set(c.stops))
            self.assertEqual(len(set(c.stops)), len(c.stops))
            self.assertIn(("a", "QA"), c.cover_keys)

    def test_merge_off_by_default(self):
        self.assertFalse(G.Params().merge)
        self.assertEqual([c for c in merge_run(False).candidates if c.origin == "tour_merge"], [])


if __name__ == "__main__":
    unittest.main()
