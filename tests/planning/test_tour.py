"""닫힌 순회(tour) 후보: 역 E에서 출발해 역 A에서 끝나고 짧은 공차(A → E)로 고리를 닫는다(작업 2026-11-24).

합성 망(구간마다 60~90초, U턴 없음):

    KE → N1 → N2 → N3 → N4 → KA        역 K(1호선): egress 후보 KE, access 후보 KA, 고리 닫기 KA → KE
          ↓↑    ↓    ↑                  역 Q(2호선): N2 → QA → QE → N3
          M1 ─→ N2   QA → QE           역 R(1호선): N1 → RA → RE → N2 (K와 같은 호선)
          N1 → RA → RE → N2

검사(지시문 6절): (a) 첫 정류장 = 역 egress 후보, 끝 = access 후보 (b) 같은 정류장 반복 없음 (c) 고리 닫기 ≤ 300초
(d) U턴 없음(고리 닫는 이음 포함) (e) 덮는 수요 = Reach 재계산값 (f) 같은 입력 두 번 → 같은 결과.
③-T: 같은 호선 역 쌍(K–R)은 후보에 나오지 않는다. tour=None이면 tour 후보가 없다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import _segments, _stop, scenario  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402
from busstrike.planning.routegen.data import Demand, Network, Station  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.loop import cyclic_seq  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach, StationIndex  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

EDGES = [("KE", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "N4"), ("N4", "KA"), ("KA", "KE"),
         ("N1", "M1"), ("M1", "N2"),
         ("N2", "QA"), ("QA", "QE"), ("QE", "N3"),
         ("N1", "RA"), ("RA", "RE"), ("RE", "N2")]
POS = {"KE": (0, 0), "N1": (1, 0), "N2": (2, 0), "N3": (2, 0.3), "N4": (1, 0.3), "KA": (0, 0.3),
       "M1": (1.5, -0.4), "QA": (3, 0), "QE": (3, 0.3), "RA": (1.3, -0.8), "RE": (1.7, -0.8)}
ENDS = {"K": (("KE",), ("KA",)), "Q": (("QE",), ("QA",)), "R": (("RE",), ("RA",))}
LINES = {"K": ("역K", frozenset({"1"})), "Q": ("역Q", frozenset({"2"})), "R": ("역R", frozenset({"1"}))}
OD = {("N1", "KA"): ("access", "K", 20.0), ("KE", "N3"): ("egress", "K", 15.0), ("M1", "KA"): ("access", "K", 10.0),
      ("KE", "N4"): ("egress", "K", 8.0), ("N1", "QA"): ("access", "Q", 12.0), ("QE", "N4"): ("egress", "Q", 9.0),
      ("RE", "N3"): ("egress", "R", 6.0), ("N1", "RA"): ("access", "R", 5.0)}


def tour_network(uturn_closure: bool = False) -> Network:
    stops = {s: _stop(s, x, y) for s, (x, y) in POS.items()}
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, EDGES)})
    turns: dict = {"_meta": {"format": "turns-v2"}}
    for a, b in EDGES:
        turns[f"{a}>{b}"] = {"s": f"n{a}", "e": f"n{b}", "tail": [[f"{a}{b}t", f"{a}{b}u"]],
                             "head": [[f"{a}{b}h", f"{a}{b}i"]]}
    if uturn_closure:                     # KE → N1 앞부분이 KA → KE 끝부분을 반대로 되짚는다(고리 닫는 이음 U턴)
        turns["KA>KE"]["tail"] = [["p", "q"]]
        turns["KE>N1"]["head"] = [["q", "p"]]
    st = {k: Station(k, LINES[k][0], stops[a[0]].lat, stops[a[0]].lon, a[0], e[0]) for k, (e, a) in ENDS.items()}
    return Network(tt, stops, st, turns)


def demand() -> Demand:
    d = Demand("v", ("20261026",))
    for (o, t), (kind, k, v) in OD.items():
        d.od[(o, t)] = [v / 4] * 4
        s = o if kind == "access" else t
        d.pair[(kind, s, k)] = [v / 4] * 4
    return d


def params(**kw) -> G.Params:
    base = dict(distance_basis="network", loop="demand", l_max_min=(15,), sectors=(2,), hub_min=1.0,
                radius_m=10000.0, max_stops=None, tour=("sector", "feeder", "pair"))
    base.update(kw)
    return G.Params(**base)


def reach_rule(net: Network) -> Reach:
    designated = {}
    for k, (e, a) in ENDS.items():
        designated[(k, "access")] = a[0]
        designated[(k, "egress")] = e[0]
    ix = StationIndex(designated, {k: set(e) | set(a) for k, (e, a) in ENDS.items()},
                      {k: frozenset(v[1]) for k, v in LINES.items()})
    return Reach(ix, EvalRule(walk_radius_m=0.0, station_mode="fixed"), {}, sorted(OD))


def run(net: Network | None = None, **kw):
    net = net or tour_network()
    return G.generate(net, demand(), scenario(), params(**kw), reach=reach_rule(net),
                      tour_inputs=T.TourInputs(ENDS, LINES))


def tours(res) -> list:
    return [c for c in res.candidates if c.origin.startswith("tour_")]


class EndPairTest(unittest.TestCase):
    def test_shortest_closure_distinct_stops(self):
        g = Graph(tour_network(), 10, 3600, "network")
        got = T.end_pair(g, ("KE", "N1"), ("KA", "N4"), 300)
        self.assertEqual(got[:2], ("KE", "KA"))           # KA → KE 한 구간(90초)이 가장 짧다
        self.assertEqual(got[2], ("KA", "KE"))

    def test_no_pair_when_closure_too_long_or_same_stop(self):
        g = Graph(tour_network(), 10, 3600, "network")
        self.assertIsNone(T.end_pair(g, ("KE",), ("KA",), 60))      # 90초 > 60초
        self.assertIsNone(T.end_pair(g, ("KE",), ("KE",), 300))     # 같은 정류장은 양 끝이 될 수 없다


class TourShapeTest(unittest.TestCase):
    def setUp(self):
        self.net = tour_network()
        self.res = run(self.net)
        self.T = tours(self.res)

    def test_all_three_generators_make_tours(self):
        self.assertEqual({c.origin for c in self.T}, {"tour_sector", "tour_feeder", "tour_pair"})

    def test_ends_are_station_candidates(self):
        for c in self.T:
            first_station = c.hubs[0]
            self.assertIn(c.stops[0], ENDS[first_station][0], c.stops)    # (a) 첫 정류장 = egress 후보
            self.assertIn(c.stops[-1], ENDS[first_station][1], c.stops)   # 끝 = 같은 역 access 후보

    def test_no_repeat_and_closure_and_no_uturn(self):
        g = Graph(self.net, 10, 3600, "network")
        for c in self.T:
            self.assertEqual(len(set(c.stops)), len(c.stops), c.stops)             # (b)
            self.assertLessEqual(c.closure_s, 300)                                 # (c)
            self.assertFalse(g.has_uturn(cyclic_seq(c.stops, c.return_stops)))     # (d)
            self.assertEqual(c.loop, "tour")
            self.assertEqual(c.kind, "tour")

    def test_cover_equals_reach(self):
        rr = reach_rule(self.net)
        tot = {k: sum(v) for k, v in demand().od.items()}
        for c in self.T:
            self.assertAlmostEqual(c.cover, sum(tot[k] for k in rr.cover_keys(c.stops)))    # (e)
            self.assertGreater(c.cover, 0)

    def test_tour_covers_both_directions_of_one_station(self):
        # 역이 양 끝이라 같은 정류장에서 역으로 가는 승객과 역에서 오는 승객을 다 태운다
        best = max((c for c in self.T if c.origin == "tour_sector"), key=lambda c: c.cover)
        keys = set(best.cover_keys)
        self.assertIn(("N1", "KA"), keys)
        self.assertIn(("KE", "N3"), keys)

    def test_cycle_limit_two_l(self):
        for c in self.T:
            self.assertLessEqual(c.cycle_s, 2 * 15 * 60)

    def test_deterministic(self):
        again = tours(run(self.net))                                                # (f)
        self.assertEqual([c.record() for c in self.T], [c.record() for c in again])


class TourPairTest(unittest.TestCase):
    def test_same_line_pair_excluded(self):
        res = run()
        pairs = {frozenset(c.hubs) for c in tours(res) if c.origin == "tour_pair"}
        self.assertIn(frozenset({"K", "Q"}), pairs)
        self.assertNotIn(frozenset({"K", "R"}), pairs)        # 둘 다 1호선
        self.assertEqual(T.line_pairs({"K": 1.0, "Q": 1.0, "R": 1.0}, LINES),
                         [("K", "Q"), ("Q", "R")])

    def test_pair_tour_passes_through_second_station(self):
        res = run()
        for c in tours(res):
            if c.origin != "tour_pair":
                continue
            p, q = c.hubs
            S = c.stops
            self.assertIn(ENDS[q][1][0], S)                      # Q.A
            self.assertIn(ENDS[q][0][0], S)                      # Q.E
            self.assertLess(S.index(ENDS[q][1][0]), S.index(ENDS[q][0][0]))


class PairSeedTest(unittest.TestCase):
    """③-T 씨앗도 1절처럼 그 L의 순회 제약을 지키는 정류장 중에서 고른다."""

    def setUp(self):
        net = tour_network()
        g = Graph(net, 10, 3600, "network")
        b = G.Builder(net, g, demand(), params(), reach_rule(net))
        self.tb = T.TourBuilder(b, scenario().policy, T.TourInputs(ENDS, LINES), 300)

    def test_picks_best_feasible(self):
        lim = T.TourLimits(1800)
        v = {"N1": 1.0, "M1": 5.0}
        got = self.tb.pair_seed(v, lambda s: ["KE", s, "QA", "QE", "KA"], lim, frozenset())
        self.assertEqual(got, "M1")

    def test_none_when_no_seed_fits(self):
        lim = T.TourLimits(300)          # 5분: 어떤 순회도 못 맞춘다
        got = self.tb.pair_seed({"N1": 1.0, "M1": 5.0}, lambda s: ["KE", s, "QA", "QE", "KA"], lim, frozenset())
        self.assertIsNone(got)


class TourOptionTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(G.Params().tour)
        res = G.generate(tour_network(), demand(), scenario(), params(tour=None), reach=reach_rule(tour_network()))
        self.assertEqual(tours(res), [])

    def test_closure_uturn_blocks_station(self):
        res = run(tour_network(uturn_closure=True))
        self.assertEqual([c for c in tours(res) if c.hubs[0] == "K"], [])
        self.assertGreater(sum(v for k, v in res.reasons.items() if k.startswith("tour/") and "uturn" in k), 0)

    def test_rotation_skips_tours(self):
        res = run(loop_rotate=True)
        self.assertTrue(tours(res))
        for c in tours(res):
            self.assertFalse(any(t.startswith("rot") for t in c.made_by))

    def test_must_stop_inserted_into_tour_keeps_origin(self):
        res = run(must_stops=("M1",), must_insert_radius_m=5000.0)
        got = [c for c in tours(res) if any(t == "must:M1" for t in c.made_by)]
        self.assertTrue(got)
        for c in got:
            self.assertIn("M1", c.stops[1:-1])
            self.assertTrue(c.origin.startswith("tour_"))
            self.assertLessEqual(c.cycle_s, 2 * 15 * 60)

    def test_pool_selection(self):
        res = run()
        cs = res.candidates
        ours = G.select_pool(cs, "ours")
        self.assertTrue(ours and all(not c.origin.startswith("tour_") for c in ours))
        self.assertEqual({c.origin for c in G.select_pool(cs, "tour")}, {"tour_sector", "tour_feeder", "tour_pair"})
        self.assertEqual({c.origin for c in G.select_pool(cs, "tour_pair")}, {"tour_pair"})
        self.assertEqual(len(G.select_pool(cs, "all")), len(cs))


if __name__ == "__main__":
    unittest.main()
