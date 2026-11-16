"""routegen 단위 테스트: 병합, 삽입 제약 위반, 덮개 판정, 주기 = 시뮬레이터, 차량 합.

합성 망만 쓴다(실제 묶음 자료는 저장소에 넣지 않는다).
"""

from __future__ import annotations

import math
import unittest

from busstrike.planning.routegen import allocate as A
from busstrike.planning.routegen import generate as G
from busstrike.planning.routegen.cycle import route_cycle, single_route_plan
from busstrike.planning.routegen.data import Demand, Network, Station, Stop, haversine_m
from busstrike.planning.routegen.graph import Graph, Limits
from busstrike.sim import io as sio
from busstrike.sim.adapters.comparison_plan import to_engine_plan
from busstrike.sim.timetable import normalize_plan
from busstrike.sim.traveltime import TravelTimeTable

LAT = 37.5
DLON = 0.004          # 약 353m


def _stop(sid: str, x: float, y: float = 0.0, gu: str = "구로구") -> Stop:
    return Stop(sid, sid, LAT + y * DLON, 126.85 + x * DLON, gu)


def _segments(stops: dict[str, Stop], pairs: list[tuple[str, str]], prof=((21600, 60), (25200, 90))) -> list[dict]:
    segs = []
    for a, b in sorted(set(pairs)):
        d = round(haversine_m(stops[a].lat, stops[a].lon, stops[b].lat, stops[b].lon))
        segs.append({"from_stop_id": a, "to_stop_id": b, "distance_m": d, "profile": [list(x) for x in prof]})
    return segs


def line_network(n: int = 6, prof=((21600, 60), (25200, 90))) -> Network:
    """S1 – S2 – … – Sn – H (양방향). H가 역 K의 access·egress 지정 정류장."""
    stops = {f"S{i}": _stop(f"S{i}", i) for i in range(1, n + 1)}
    stops["H"] = _stop("H", n + 1)
    order = [f"S{i}" for i in range(1, n + 1)] + ["H"]
    pairs = [(a, b) for a, b in zip(order, order[1:])] + [(b, a) for a, b in zip(order, order[1:])]
    tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                    "valid_until_s": 43200, "segments": _segments(stops, pairs, prof)})
    st = {"K": Station("K", "역", stops["H"].lat, stops["H"].lon + 0.0005, "H", "H")}
    return Network(tt, stops, st)


def scenario(n_vehicles: int = 3):
    return sio.scenario_from_dict({
        "scenario_id": "sc-syn", "service_date": "2026-10-26", "timezone": "Asia/Seoul",
        "network_snapshot_id": "syn-1", "demand_profile_id": "d", "strike_route_ids": [],
        "operating_window": {"start_s": 21600, "end_s": 36000},
        "fleet": {"authorized_count": n_vehicles, "vehicles": [
            {"vehicle_id": f"V{i:03d}", "vehicle_class": "c45", "seat_capacity": 45, "standing_capacity": 0,
             "available_start_s": 21600, "available_end_s": 39600} for i in range(1, n_vehicles + 1)]},
        "policy": {"max_wait_s": 1200, "max_journey_s": 3600, "allowed_transfers": 0, "arrival_method": "systematic",
                   "dwell_base_s": 10, "dwell_per_boarding_s": 0, "dwell_per_alighting_s": 0, "turnaround_s": 180,
                   "cycle_rest_s": 0, "unknown_link": "error"}})


class MergeTest(unittest.TestCase):
    def test_overlap_formula(self):
        K = {"a": {"s1": 10.0, "s2": 5.0}, "b": {"s2": 3.0, "s3": 7.0}}
        F = {"a": 15.0, "b": 10.0}
        self.assertAlmostEqual(G.overlap("a", "b", K, F), (5.0 + 3.0) / 10.0)

    def test_union_find_threshold(self):
        O = {("a", "b"): 0.25, ("a", "c"): 0.1, ("b", "c"): 0.19}
        cl = G.merge_hubs(["a", "b", "c"], O, 0.2, 0.05, 3)
        self.assertEqual([c["hubs"] for c in cl], [["a", "b"], ["c"]])
        self.assertTrue(all(c["tau"] == 0.2 for c in cl))

    def test_transitive_merge(self):
        O = {("a", "b"): 0.3, ("a", "c"): 0.0, ("b", "c"): 0.3}
        self.assertEqual([c["hubs"] for c in G.merge_hubs(["a", "b", "c"], O, 0.2, 0.05, 3)], [["a", "b", "c"]])

    def test_cluster_over_cap_raises_tau_only_there(self):
        hubs = ["a", "b", "c", "d", "x", "y"]
        O = {(p, q): 0.0 for p in hubs for q in hubs if p < q}
        O.update({("a", "b"): 0.9, ("b", "c"): 0.5, ("c", "d"): 0.3, ("x", "y"): 0.21})
        cl = G.merge_hubs(hubs, O, 0.2, 0.05, 3)
        got = {tuple(c["hubs"]): c["tau"] for c in cl}
        # a-b-c-d(4개)는 τ를 올려 0.35에서 c-d가 끊긴다. x-y는 τ=0.2 그대로.
        self.assertEqual(got[("x", "y")], 0.2)
        self.assertIn(("a", "b", "c"), got)
        self.assertIn(("d",), got)
        self.assertAlmostEqual(got[("a", "b", "c")], 0.35)
        self.assertTrue(all(len(c["hubs"]) <= 3 for c in cl))


class CoverTest(unittest.TestCase):
    def test_access_and_egress_order(self):
        od = {("S1", "H"): [1, 0, 0, 0], ("H", "S1"): [0, 2, 0, 0], ("S2", "H"): [0, 0, 3, 0]}
        self.assertEqual(G.covered_keys(("S1", "S2", "H"), od), [("S1", "H"), ("S2", "H")])   # access: 역 정류장보다 앞
        self.assertEqual(G.covered_keys(("H", "S2", "S1"), od), [("H", "S1")])               # egress: 역 정류장이 앞
        self.assertEqual(G.covered_keys(("H", "S1"), {("S1", "H"): [1, 1, 1, 1]}), [])       # 순서가 반대면 못 덮음

    def test_connect_covers_both_kinds(self):
        od = {("Ei", "S1"): [1, 0, 0, 0], ("S1", "Aj"): [1, 0, 0, 0], ("S1", "Ei"): [5, 0, 0, 0]}
        self.assertEqual(sorted(G.covered_keys(("Ei", "S1", "Aj"), od)), [("Ei", "S1"), ("S1", "Aj")])
        self.assertAlmostEqual(G.cover_value(("Ei", "S1", "Aj"), {k: sum(v) for k, v in od.items()}), 2.0)


class ConstraintTest(unittest.TestCase):
    def setUp(self):
        self.net = line_network(10)
        self.g = Graph(self.net, dwell_s=10, cutoff_s=3600)

    def test_oneway_uses_max_profile_and_dwell(self):
        stops = ("S8", "S9", "S10", "H")
        self.assertEqual(self.g.oneway_s(stops), 3 * 90 + 3 * 10)

    def test_violations(self):
        ok = Limits(l_max_s=20 * 60, max_stops=8, max_detour=1.6)
        st = frozenset({"H"})
        self.assertEqual(self.g.violations(("S8", "S9", "S10", "H"), st, ok), [])
        long_route = tuple(f"S{i}" for i in range(2, 11)) + ("H",)       # 정차 9개(역 제외)
        self.assertIn("max_stops", self.g.violations(long_route, st, ok))
        self.assertEqual(self.g.violations(("S8", "S9", "S10", "H"), st, Limits(5 * 60)), [])   # 300초 = 상한
        self.assertIn("l_max", self.g.violations(("S8", "S9", "S10", "H"), st, Limits(5 * 60 - 1)))
        self.assertIn("repeat", self.g.violations(("S9", "S10", "S9"), st, ok))

    def test_detour(self):
        # S1 → S3 → S2 는 직선 S1–S2 대비 경로가 3배
        stops = {k: v for k, v in self.net.stops.items()}
        net = Network(self.net.tt, stops, self.net.stations)
        g = Graph(net, 10, 3600)
        self.assertGreater(g.detour(("S1", "S2", "S3", "S2")), 1.6)
        self.assertAlmostEqual(g.detour(("S1", "S2", "S3")), 1.0, places=2)

    def _builder(self, net, od):
        d = Demand("affected", ("20261026",), {}, od)
        return G.Builder(net, Graph(net, 10, 3600), d, G.Params())

    def test_insertion_never_breaks_constraints(self):
        od = {(f"S{i}", "H"): [1.0, 1.0, 1.0, 1.0] for i in range(1, 11)}
        b = self._builder(self.net, od)
        pat = G.Pattern("access", ("K",), None, "H", (LAT, 126.85))
        for lim in (Limits(20 * 60, 8, 1.6), Limits(30 * 60, 8, 1.6), Limits(6 * 60, 3, 1.6)):
            r = b.insertion(pat, "S9", [f"S{i}" for i in range(1, 11)], lim)
            self.assertIsNotNone(r)
            self.assertEqual(self.g.violations(r, frozenset({"H"}), lim), [])
            self.assertEqual(r[-1], "H")
        # 정차 3개 상한이면 S8–S10만 남는다(S7을 넣으면 4개)
        self.assertEqual(b.insertion(pat, "S9", [f"S{i}" for i in range(1, 11)], Limits(30 * 60, 3, 1.6)),
                         ("S8", "S9", "S10", "H"))

    def test_seed_that_violates_is_rejected(self):
        b = self._builder(self.net, {("S1", "H"): [1, 1, 1, 1]})
        pat = G.Pattern("access", ("K",), None, "H", (LAT, 126.85))
        self.assertIsNone(b.insertion(pat, "S1", ["S1"], Limits(30 * 60, 8, 1.6)))   # 정차 10개 필요

    def test_detour_violation_blocks_insertion(self):
        # S2에서 H로 가려면 S1로 되돌아가야 하는 망: S2 → S1 → H … 만 있음
        stops = {"S1": _stop("S1", 0), "S2": _stop("S2", 2), "H": _stop("H", 3)}
        segs = _segments(stops, [("S1", "H"), ("S2", "S1")])
        tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-2", "valid_from_s": 21600,
                                        "valid_until_s": 43200, "segments": segs})
        net = Network(tt, stops, {"K": Station("K", "역", stops["H"].lat, stops["H"].lon, "H", "H")})
        b = self._builder(net, {("S1", "H"): [1, 1, 1, 1], ("S2", "H"): [9, 9, 9, 9]})
        pat = G.Pattern("access", ("K",), None, "H", (LAT, 126.85))
        self.assertEqual(b.insertion(pat, "S1", ["S1", "S2"], Limits(30 * 60, 8, 1.6)), ("S1", "H"))
        self.assertGreater(Graph(net, 10, 3600).detour(("S2", "S1", "H")), 1.6)


class CycleTest(unittest.TestCase):
    def test_cycle_equals_simulator_period(self):
        """후보 주기 = 변환기 → 시간표 생성기의 period_s (시간대마다 다른 구간 시간 포함)."""
        net = line_network(6, prof=((21600, 60), (25200, 95), (28800, 130), (32400, 70)))
        sc = scenario(3)
        for stops in [("S4", "S5", "S6", "H"), ("H", "S6", "S5"), ("S1", "S2", "S3", "S4", "S5", "S6", "H")]:
            cyc = route_cycle(stops, net.tt, sc)
            self.assertIsNotNone(cyc)
            engine, _ = to_engine_plan(single_route_plan(stops, net.tt, sc), net.tt, sc)
            tt_ = normalize_plan(sio.plan_from_dict(engine), sc.vehicles, sc.window, sc.policy, net.tt)
            self.assertEqual(cyc.period_s, tt_.routes[0].period_s)
            self.assertEqual(cyc.return_stops, tuple(reversed(stops)))

    def test_cycle_hand_value(self):
        """구간 60초 일정: 영업 3구간 + 정차 3×10 + 회차 180 + 공차 3구간(정차 없음)."""
        net = line_network(6, prof=((21600, 60),))
        cyc = route_cycle(("S4", "S5", "S6", "H"), net.tt, scenario(1))
        self.assertEqual(cyc.period_s, 3 * 60 + 3 * 10 + 180 + 3 * 60)

    def test_no_return_path(self):
        stops = {"A": _stop("A", 0), "B": _stop("B", 1)}
        tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "syn-1", "valid_from_s": 21600,
                                        "valid_until_s": 43200, "segments": _segments(stops, [("A", "B")])})
        self.assertIsNone(route_cycle(("A", "B"), tt, scenario(1)))


class AllocateTest(unittest.TestCase):
    def model(self):
        od = {("a1", "H"): [100.0, 300.0, 300.0, 100.0], ("a2", "H"): [50.0, 80.0, 80.0, 40.0],
              ("b1", "H"): [20.0, 30.0, 30.0, 20.0], ("c1", "H"): [5.0, 5.0, 5.0, 5.0]}
        routes = [("ra", 2400, (("a1", "H"),)), ("rb", 1300, (("b1", "H"),)),
                  ("rab", 3000, (("a1", "H"), ("a2", "H"))), ("rc", 900, (("c1", "H"),))]
        return A.Model(routes, od, A.AllocParams())

    def test_served_formula(self):
        m = self.model()
        det = m.detail({"rc": 1})
        # n/C·W = 1200/900 ≥ 1 → 전부 충족, 한도 3600/900×45 = 180/시 미만
        self.assertAlmostEqual(det["total"], 20.0)
        det = m.detail({"ra": 1})
        cap = 3600 / 2400 * 45
        expect = sum(min(d * 0.5, cap) for d in (100, 300, 300, 100))
        self.assertAlmostEqual(det["total"], expect)

    def test_split_by_frequency(self):
        m = self.model()
        det = m.detail({"ra": 1, "rab": 1})
        fa, fab = 1 / 2400, 1 / 3000
        share_a = min(1, 1200 * (fa + fab)) * fa / (fa + fab)
        self.assertAlmostEqual(det["by_route"]["ra"][0], min(100 * share_a, 3600 / 2400 * 45))

    def test_vehicle_sum_exact_and_deterministic(self):
        m = self.model()
        for budget in (1, 2, 5, 11, 13):
            a1, a2 = A.allocate(m, budget), A.allocate(m, budget)
            self.assertEqual(sum(a1["n"].values()), budget)
            self.assertTrue(all(v >= 1 for v in a1["n"].values()))
            self.assertEqual(a1, a2)

    def test_open_with_n0(self):
        m = self.model()
        self.assertEqual(m.n0, {"ra": 2, "rb": 2, "rab": 3, "rc": 1})
        g = A.greedy(m, 2)
        self.assertTrue(all(v in (1, 2) for v in g.values()))

    def test_local_search_not_worse(self):
        m = self.model()
        a = A.allocate(m, 7)
        self.assertGreaterEqual(a["value"] + 1e-9, a["value_filled"])


class SectorTest(unittest.TestCase):
    def test_sectors_cover_all_and_balance(self):
        net = line_network(1)
        stops = {}
        for i in range(12):
            ang = 2 * math.pi * i / 12
            sid = f"P{i}"
            stops[sid] = Stop(sid, sid, LAT + 0.01 * math.sin(ang), 126.85 + 0.01 * math.cos(ang), "구로구")
        net = Network(net.tt, stops, {})
        vals = {s: 1.0 for s in stops}
        for m in (2, 3, 4):
            secs = G.sectors(sorted(stops), vals, (LAT, 126.85), m, net)
            self.assertEqual(sorted(s for sec in secs for s in sec), sorted(stops))
            self.assertEqual(len(secs), m)
            self.assertEqual({len(s) for s in secs}, {12 // m})


if __name__ == "__main__":
    unittest.main()
