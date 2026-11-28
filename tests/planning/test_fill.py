"""소외 우선 삽입(Params.fill)과 두 옵션의 독립성(작업 2026-11-25, docs/design/pipeline.md 3·3.1절).

합성 망(소외 가산점): KE → A1 → A2 → KA(역 K), A1 → X → A2(역 가까운 정류장 X), A1 → F → A2(역 800m 밖 F). X와 F는 같은 자리를 다툰다.
X 수요 12 > F 수요 10이라 가산점이 없으면 X, 가산점(β = 1, F의 소외 수요 10)이 있으면 F가 끼워진다.
- 순회(피더 골격 [E, 씨앗, A], origin tour_fill)와 편도 노선 생성 후 순회(최단경로 피더 → 돌아오는 길 → 삽입, origin fill) 둘 다.
- 두 옵션(line_group, fill)을 끄면 예전과 같고, 세 후보 종류 × 4조합이 서로 다른 경로를 탄다(made_by 꼬리표·origin).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import _segments, _stop, scenario  # noqa: E402
from test_tour import ENDS, LINES, demand, params, reach_rule, tour_network  # noqa: E402

from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import tour as T  # noqa: E402
from busstrike.planning.routegen.data import Demand, Network, Station  # noqa: E402
from busstrike.planning.routegen.fill import FillInserter  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402
from busstrike.planning.routegen.loop import cyclic_seq  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach, StationIndex  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

F_EDGES = [("KE", "A1"), ("A1", "A2"), ("A2", "KA"), ("KA", "KE"), ("A1", "X"), ("X", "A2"), ("A1", "F"), ("F", "A2")]
F_POS = {"KE": (0, 0), "A1": (1, 0), "A2": (2, 0), "KA": (0, 0.3), "X": (1.5, 0.3), "F": (1.5, -0.3)}
F_OD = {("A1", "KA"): 30.0, ("X", "KA"): 12.0, ("F", "KA"): 10.0}
TAGS = {"K": frozenset({"1"}), "Q": frozenset({"2"}), "R": frozenset({"1"})}


def fill_net() -> Network:
    stops = {s: _stop(s, x, y) for s, (x, y) in F_POS.items()}
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, F_EDGES)})
    turns: dict = {"_meta": {"format": "turns-v2"}}
    for a, b in F_EDGES:
        turns[f"{a}>{b}"] = {"s": f"n{a}", "e": f"n{b}", "tail": [[f"{a}{b}t", f"{a}{b}u"]], "head": [[f"{a}{b}h", f"{a}{b}i"]]}
    st = {"K": Station("K", "역K", stops["KA"].lat, stops["KA"].lon, "KA", "KE")}
    return Network(tt, stops, st, turns)


def fill_run(bonus: float, tour: bool, fill: bool = True):
    net = fill_net()
    d = Demand("v", ("20261026",))
    for (o, t), v in F_OD.items():
        d.od[(o, t)] = [v / 4] * 4
        d.pair[("access", o, "K")] = [v / 4] * 4
    ix = StationIndex({("K", "access"): "KA", ("K", "egress"): "KE"}, {"K": {"KA", "KE"}}, {"K": frozenset({"1"})})
    reach = Reach(ix, EvalRule(walk_radius_m=0.0, station_mode="fixed"), {}, sorted(F_OD))
    p = G.Params(distance_basis="network", loop="demand", l_max_min=(15,), hub_min=1.0, radius_m=10000.0, max_stops=None,
                 tour=("feeder",) if tour else None, fill=fill, fill_bonus=bonus, fill_top=1)
    return G.generate(net, d, scenario(), p, reach=reach,
                      tour_inputs=T.TourInputs({"K": (("KE",), ("KA",))}, {"K": ("역K", frozenset({"1"}))}),
                      far_stops=frozenset({"F"}))


class FillTourTest(unittest.TestCase):
    """순회 소외 삽입: 피더 골격에서 2L까지, 점수 = (탑승 근사 증가 + β × 소외 수요 증가) ÷ 순환 증가."""

    def test_bonus_zero_picks_near_stop(self):
        got = [c for c in fill_run(0.0, tour=True).candidates if c.origin == "tour_fill"]
        self.assertTrue(got)
        self.assertTrue(all("X" in c.stops and "F" not in c.stops for c in got), [c.stops for c in got])

    def test_bonus_picks_isolated_stop(self):
        got = [c for c in fill_run(1.0, tour=True).candidates if c.origin == "tour_fill"]
        self.assertTrue(got)
        self.assertTrue(all("F" in c.stops for c in got), [c.stops for c in got])

    def test_within_tour_limit_no_uturn(self):
        res = fill_run(1.0, tour=True)
        g = Graph(fill_net(), 10, 3600, "network")
        for c in res.candidates:
            if c.origin == "tour_fill":
                self.assertLessEqual(c.cycle_s, 2 * 15 * 60)
                self.assertFalse(g.has_uturn(cyclic_seq(c.stops, c.return_stops)))

    def test_pool_includes_fill(self):
        res = fill_run(1.0, tour=True)
        self.assertEqual({c.origin for c in G.select_pool(res.candidates, "tour_feeder")}, {"tour_feeder", "tour_fill"})


class FillOnewayLoopTest(unittest.TestCase):
    """편도 노선 생성 후 순회 소외 삽입(3.1절): 최단경로 피더 → 수요 돌아오는 길 → 삽입. 꺾는 정류장(역)은 그대로."""

    def test_isolated_stop_inserted(self):
        got = [c for c in fill_run(1.0, tour=False).candidates if c.origin == "fill"]
        self.assertTrue(got)
        for c in got:
            self.assertIn("F", c.stops)
            self.assertEqual(c.loop, "demand")
            self.assertEqual(c.stops[c.turn], "KA")               # access 패턴: 꺾는 정류장 = 역 access 정류장

    def test_bonus_zero_picks_near_stop(self):
        got = [c for c in fill_run(0.0, tour=False).candidates if c.origin == "fill"]
        self.assertTrue(got)
        self.assertTrue(all("X" in c.stops and "F" not in c.stops for c in got), [c.stops for c in got])

    def test_fill_in_ours_pool(self):
        res = fill_run(1.0, tour=False)
        self.assertIn("fill", {c.origin for c in G.select_pool(res.candidates, "ours")})

    def test_off_has_no_fill(self):
        res = fill_run(1.0, tour=False, fill=False)
        self.assertFalse(any(c.origin in ("fill", "tour_fill") for c in res.candidates))


class InserterTest(unittest.TestCase):
    def test_n_is_allocator_opening_unit(self):
        fi = FillInserter(None, None, None, None, 0.0, 1200)
        self.assertEqual([fi.n_of(x) for x in (600, 1200, 1201, 2400, 2401)], [1, 1, 2, 2, 3])


def variant_run(base: str, line_group: bool, fill: bool):
    net = tour_network()
    tour = {"sector": ("sector",), "feeder": ("feeder",), "oneway_loop": None}[base]
    kw = dict(tour=tour, line_group=line_group, fill=fill, fill_bonus=1.0 if fill else 0.0, fill_top=2)
    return G.generate(net, demand(), scenario(), params(**kw), reach=reach_rule(net),
                      tour_inputs=T.TourInputs(ENDS, LINES, TAGS), far_stops=frozenset({"N4", "M1"}))


POOL = {"sector": "tour_sector", "feeder": "tour_feeder", "oneway_loop": "ours"}


class IndependenceTest(unittest.TestCase):
    """세 후보 종류 각각 4조합이 서로 다른 경로를 탄다: 호선 묶기 = lg: 꼬리표, 소외 삽입 = fill 후보(origin)."""

    def test_four_combos_take_different_paths(self):
        for base in POOL:
            seen = {}
            for lg in (False, True):
                for fl in (False, True):
                    pool = G.select_pool(variant_run(base, lg, fl).candidates, POOL[base])
                    has_lg = any(t.startswith("lg:") for c in pool for t in c.made_by)
                    has_fill = any(c.origin in ("fill", "tour_fill") for c in pool)
                    self.assertEqual((has_lg, has_fill), (lg, fl), (base, lg, fl))
                    seen[(lg, fl)] = tuple(sorted((c.origin, c.stops, c.made_by) for c in pool))
            # 작은 망에서는 정차열이 같을 수 있다(섹터 순회 + 호선). 생성 경로(origin·made_by 기록)는 4조합이 모두 다르다
            self.assertEqual(len(set(seen.values())), 4, base)

    def test_off_equals_default(self):
        for base in POOL:
            a = variant_run(base, False, False)
            net = tour_network()
            tour = {"sector": ("sector",), "feeder": ("feeder",), "oneway_loop": None}[base]
            b = G.generate(net, demand(), scenario(), params(tour=tour), reach=reach_rule(net),
                           tour_inputs=T.TourInputs(ENDS, LINES))
            self.assertEqual([c.record() for c in a.candidates], [c.record() for c in b.candidates], base)

    def test_deterministic(self):
        for base in POOL:
            a = variant_run(base, True, True)
            b = variant_run(base, True, True)
            self.assertEqual([c.record() for c in a.candidates], [c.record() for c in b.candidates], base)


if __name__ == "__main__":
    unittest.main()
