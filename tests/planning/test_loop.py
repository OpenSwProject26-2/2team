"""3b단계: 순환 후보(돌아오는 길·고리 닫기 ≤ 5분·U턴 이음 없음), 양방향 덮는 수요, 순환의 구간별 정원 항, 방향별 정차 상한.

합성 망(길 양쪽 정류장 ID가 다르다):

    C ─────→ A1 → A2 → A3 → H (역 K access 지정)
    ↑         ↑(U턴)            ↓
    B1 ←──── B2 ←──── B3 ←──── E (역 K egress 지정)

- B1 → A1 직접 이음은 A1에서 A2로 나갈 때 U턴이다(turns-v2 조각 되짚기). 그래서 고리는 B1 → C → A1(블록 돌기)로 닫아야 한다.
- 변환기 공차 복귀(최단 경로)는 H → E → B3 → B2 → B1 → A1이라 "공차를 영업으로" 바꾸면 A1에서 U턴이 생긴다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_reach import Flow, make_index  # noqa: E402
from test_routegen import _segments, _stop, scenario  # noqa: E402

from busstrike.planning.routegen import allocate as A  # noqa: E402
from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen.cycle import route_cycle  # noqa: E402
from busstrike.planning.routegen.data import Demand, Network, Station  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.loop import (LoopBuilder, LoopLimits, closure_s, cyclic_seq,  # noqa: E402
                                              designated_stops, leg_violations)
from busstrike.planning.routegen.reach import EvalRule, Reach  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

EDGES = [("A1", "A2"), ("A2", "A3"), ("A3", "H"), ("H", "E"), ("E", "B3"), ("B3", "B2"), ("B2", "B1"),
         ("B1", "A1"), ("B1", "C"), ("C", "A1")]


def loop_network(uturn_at_a1: bool = True) -> Network:
    pos = {"A1": (0, 0), "A2": (1, 0), "A3": (2, 0), "H": (3, 0), "E": (3, 0.3), "B3": (2, 0.3), "B2": (1, 0.3),
           "B1": (0, 0.3), "C": (-0.4, 0.15)}
    stops = {s: _stop(s, x, y) for s, (x, y) in pos.items()}
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, EDGES)})
    turns: dict = {"_meta": {"format": "turns-v2"}}
    for a, b in EDGES:                    # 정류장마다 노드 하나, 구간마다 서로 다른 도로 조각 → U턴 없음
        turns[f"{a}>{b}"] = {"s": f"n{a}", "e": f"n{b}", "tail": [[f"{a}{b}t", f"{a}{b}u"]],
                             "head": [[f"{a}{b}h", f"{a}{b}i"]]}
    if uturn_at_a1:                       # A1 → A2 앞부분이 B1 → A1 끝부분을 반대로 되짚는다
        turns["B1>A1"]["tail"] = [["p", "q"]]
        turns["A1>A2"]["head"] = [["q", "p"]]
    st = {"K": Station("K", "역", stops["H"].lat, stops["H"].lon, "H", "E")}
    return Network(tt, stops, st, turns)


def demand(od: dict[tuple[str, str], float]) -> Demand:
    d = Demand("v", ("20261026",))
    for (o, t), v in od.items():
        d.od[(o, t)] = [v, v, v, v]
        kind, s = ("access", o) if t == "H" else ("egress", t)
        d.pair[(kind, s, "K")] = [v, v, v, v]
    return d


def builder(net: Network, od: dict, p: G.Params = G.Params(distance_basis="network")):
    g = Graph(net, 10, 1800, distance_basis="network")
    return G.Builder(net, g, demand(od), p), g


OD = {("A1", "H"): 30.0, ("A2", "H"): 20.0, ("E", "B2"): 25.0, ("E", "B1"): 10.0}


class PathContinueThenTest(unittest.TestCase):
    def test_closing_hop_avoids_uturn_at_first_stop(self):
        g = Graph(loop_network(), 10, 1800)
        self.assertEqual(g.path_continue("B2", "B1", "A1"), ("B1", "A1"))
        self.assertEqual(g.path_continue("B2", "B1", "A1", then="A2"), ("B1", "C", "A1"))   # 블록을 돈다
        g2 = Graph(loop_network(uturn_at_a1=False), 10, 1800)
        self.assertEqual(g2.path_continue("B2", "B1", "A1", then="A2"), ("B1", "A1"))


class LoopCandidateTest(unittest.TestCase):
    def setUp(self):
        self.net = loop_network()
        self.b, self.g = builder(self.net, OD)
        self.lb = LoopBuilder(self.b, 3000.0)
        self.S = ("A1", "A2", "A3", "H")          # access 가는 길
        self.lim = LoopLimits(1800, 12, 3.0, 300)

    def test_demand_return_closes_short_without_uturn(self):
        ret = self.lb.demand_return("access", ("K",), self.S, self.lim)
        self.assertIsNotNone(ret)
        loop = self.S + ret[1:]
        self.assertEqual(loop, ("A1", "A2", "A3", "H", "E", "B3", "B2", "B1", "C"))   # 돌아오는 길이 생긴다
        cyc = route_cycle(loop, self.net.tt, scenario())
        self.assertEqual(cyc.return_stops, ("C", "A1"))                                  # 고리 닫기 = 짧은 공차 하나
        self.assertLessEqual(closure_s(self.g, cyc.return_stops), 300)
        self.assertFalse(self.g.has_uturn(cyclic_seq(loop, cyc.return_stops)))           # 닫는 이음 포함 U턴 없음

    def test_deadhead_baseline_respects_uturn(self):
        cyc = route_cycle(self.S, self.net.tt, scenario())
        self.assertEqual(cyc.return_stops, ("H", "E", "B3", "B2", "B1", "A1"))         # 지금의 공차 복귀
        self.assertIsNone(self.lb.deadhead_return("access", self.S, cyc.return_stops, self.lim))
        self.assertEqual(self.lb.reasons.get(("deadhead", "access", "uturn")), 1)
        lb2 = LoopBuilder(builder(loop_network(False), OD)[0], 3000.0)
        ret = lb2.deadhead_return("access", self.S, cyc.return_stops, self.lim)
        self.assertEqual(ret, ("H", "E", "B3", "B2", "B1"))                             # U턴이 없으면 공차 정류장이 영업

    def test_generate_all_candidates_are_loops(self):
        net = loop_network()
        b, g = builder(net, OD)
        p = G.Params(distance_basis="network", loop="demand", hub_min=1, radius_m=5000, l_max_min=(30,),
                     sectors=(1,), max_detour=3.0)
        res = G.generate(net, b.demand, scenario(), p)
        self.assertTrue(res.candidates)
        for c in res.candidates:
            self.assertEqual(c.loop, "demand")
            self.assertLessEqual(c.closure_s, 300)
            self.assertFalse(g.has_uturn(cyclic_seq(c.stops, c.return_stops)))
            self.assertGreater(c.ret_n_stops, 0)                                         # 편도 + 긴 공차가 아니다
            self.assertEqual(c.stops[c.turn], "H" if c.kind == "access" else c.stops[c.turn])
        acc = [c for c in res.candidates if c.kind == "access"]
        self.assertIn(("E", "B2"), acc[0].cover_keys)                                    # 돌아오는 길이 역 → 동네를 덮는다

    def test_closure_limit(self):
        lim = LoopLimits(1800, 12, 3.0, 30)                     # 30초: 어떤 이음도 못 넘는다
        self.assertIsNone(self.lb.demand_return("access", ("K",), self.S, lim))
        self.assertIn(("demand", "access", "base_closure"), self.lb.reasons)


class PerDirectionStopsTest(unittest.TestCase):
    def test_return_leg_cap(self):
        net = loop_network()
        g = Graph(net, 10, 1800, distance_basis="network")
        dz = designated_stops(net)
        self.assertEqual(dz, frozenset({"H", "E"}))
        leg = ("H", "E", "B3", "B2", "B1", "C")                 # 역 지정 정류장(E) 빼고 4곳
        self.assertNotIn("max_stops", leg_violations(g, leg, "A1", dz, LoopLimits(1800, 4, 9.0, 300)))
        self.assertIn("max_stops", leg_violations(g, leg, "A1", dz, LoopLimits(1800, 3, 9.0, 300)))

    def test_outbound_cap_applies_to_loop(self):
        net = loop_network()
        b, _ = builder(net, OD)
        p = G.Params(distance_basis="network", loop="demand", loop_max_stops=2)
        G._LOOP_STATE.update(lb=LoopBuilder(b, 3000.0), p=p, scenario=scenario(), modes=("demand",))
        c = G.Candidate("", "access", ("A1", "A2", "A3", "H"), "generated", ("K",), 0, ("m1_L30",))
        loops, reasons = G._loop_one(c)                          # 가는 길 A1·A2·A3 = 3곳 > 2
        G._LOOP_STATE.clear()
        self.assertEqual(loops, [])
        self.assertEqual(reasons, {("demand", "access", "outbound_max_stops"): 1})

    def test_loop_limits_from_tags(self):
        p = G.Params(l_max_min=(20, 30), multi_l_max_min=40, max_detour=1.6, loop_ret_detour=2.0)
        mk = lambda kind, tags: G.Candidate("", kind, ("a", "b"), "generated", ("K",), 0, tags)
        self.assertEqual(G.loop_limits(mk("access", ("m2_L20", "m3_L30")), p).l_max_s, 1200)
        self.assertEqual(G.loop_limits(mk("access", ("sp:x",)), p).l_max_s, 1800)
        self.assertEqual(G.loop_limits(mk("access", ("sp:x",)), p).max_detour, 2.0)
        self.assertEqual(G.loop_limits(mk("multi", ("multi:x",)), p).l_max_s, 2400)
        self.assertEqual(G.loop_limits(mk("multi", ("multi:x",)), G.Params()).max_detour, 3.0)


class BidirectionalCoverTest(unittest.TestCase):
    """역 1801(지정 정류장 G1, 주변 G1x). 동네 h(access), h2(egress)."""

    def setUp(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1"), Flow("egress", "h2", "1801", "G1", "1"),
                         Flow("egress", "h", "1801", "G1", "1")])
        self.r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=0), {})

    def test_return_leg_covers_station_to_home(self):
        loop = ["h", "G1x", "G1", "h2"]                          # 가는 길 h → 역, 돌아오는 길 역 → h2
        self.assertEqual(self.r.cover_keys(loop), (("G1", "h2"), ("h", "G1")))
        self.assertEqual(self.r.cover_keys(["h", "G1x"]), (("h", "G1"),))   # 편도는 access만
        self.assertEqual(G.covered_keys(tuple(loop), {("G1", "h2"): 1, ("h", "G1"): 1}),
                         [("h", "G1"), ("G1", "h2")])

    def test_wraparound_not_counted(self):
        """역 → h(egress)는 h가 고리 앞쪽이라 다음 바퀴에 내려야 한다. 시뮬레이터는 한 영업 leg 안에서만 태우므로 세지 않는다."""
        self.assertNotIn(("G1", "h"), self.r.cover_keys(["h", "G1x", "G1", "h2"]))


class LoopSegmentCapacityTest(unittest.TestCase):
    def test_alight_on_outbound_board_on_return(self):
        """한 바퀴 h → G1 → h2, 1대·주기 3,600초(시간당 좌석 45). access 45명(h → G1), egress 45명(G1 → h2).
        G1에서 가는 길 승객이 내리고 돌아오는 길 승객이 탄다 → 구간별 정원은 90명을 모두 태운다(좌석 재사용)."""
        ix = make_index([Flow("access", "h", "1801", "G1", "1"), Flow("egress", "h2", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=0), {})
        loop = ["h", "G1", "h2"]
        ks, sp = r.spans(loop)
        self.assertEqual(ks, (("G1", "h2"), ("h", "G1")))
        self.assertEqual(sp, ((1, 2), (0, 1)))
        od = {("h", "G1"): [45.0, 0, 0, 0], ("G1", "h2"): [45.0, 0, 0, 0]}
        seg = A.Model([("r", 3600, ks, sp)], od, A.AllocParams(wait_s=3600)).detail({"r": 1})
        old = A.Model([("r", 3600, ks)], od, A.AllocParams(wait_s=3600)).detail({"r": 1})
        self.assertAlmostEqual(seg["total"], 90.0)
        self.assertAlmostEqual(old["total"], 45.0)                # 노선 전체 좌석 × 1회면 반만

    def test_through_passenger_shares_bottleneck(self):
        """h → h2를 지나는 승객(역 밖 OD가 아니라 egress 키 G1 → h2를 h에서 타는 경우는 없다)이 아닌, 같은 구간 G1 → h2에
        두 키가 겹치면 그 구간만 자른다."""
        od = {("h", "G1"): [45.0, 0, 0, 0], ("G1", "h2"): [45.0, 0, 0, 0], ("G1", "h3"): [45.0, 0, 0, 0]}
        routes = [("r", 3600, (("G1", "h2"), ("G1", "h3"), ("h", "G1")), ((1, 2), (1, 3), (0, 1)))]
        det = A.Model(routes, od, A.AllocParams(wait_s=3600)).detail({"r": 1})
        self.assertAlmostEqual(det["total"], 45.0 + 22.5 + 22.5)  # 가는 길 45명은 그대로, 돌아오는 길 90명 → 반


if __name__ == "__main__":
    unittest.main()
