"""통합(2026-11-21): 필수 시설 규칙, 필수 정류장 덮음(첫·끝 제외, 배분 하드 조건), 파업 비율 수요, 상대 기준 규칙.

v2(feat/routegen-v2-must)의 테스트를 main 생성기·배분기 구조에 맞춰 옮겼다. 합성 자료만 쓴다.
"""

from __future__ import annotations

import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_routegen import line_network  # noqa: E402

from busstrike.planning.routegen import allocate as A  # noqa: E402
from busstrike.planning.routegen import must_stops as MS  # noqa: E402
from busstrike.planning.routegen import rules as RU  # noqa: E402
from busstrike.planning.routegen import strike as K  # noqa: E402
from busstrike.planning.routegen.generate import Params, _insert_must, hubs_of  # noqa: E402
from busstrike.planning.routegen.graph import Graph  # noqa: E402

LAT = 37.5
STEP = 0.003      # 남북 약 333m


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def must_bundle(tmp: Path) -> Path:
    """정류장(116 구로 · 117 다른 구), GTFS R1(affected) · R2(unaffected) · R3(affected) · M1(마을), 경기 G1."""
    root = tmp / "b"
    pts = {"116000001": 0.0, "116000002": 1.0, "116000003": 2.0, "116000004": 3.0, "117000001": 4.0,
           "116000005": 5.0, "116000006": 6.0}
    _write(root / "network" / "stops.csv", ["stop_id", "stop_name", "stop_lat", "stop_lon", "gu"],
           [[s, s, LAT + y * STEP, 126.85, "다른구" if s.startswith("117") else "구로구"] for s, y in pts.items()])
    _write(root / "gtfs" / "routes.txt", ["route_id", "route_short_name", "route_type", "route_desc"],
           [["R1", "1", 3, "서울지선버스"], ["R2", "2", 3, "서울지선버스"], ["R3", "3", 3, "서울간선버스"],
            ["M1", "구로01", 3, "서울마을버스"]])
    _write(root / "gtfs" / "trips.txt", ["route_id", "service_id", "trip_id"],
           [["R1", "W", "t1"], ["R2", "W", "t2"], ["R3", "W", "t3"], ["M1", "W", "t4"]])
    _write(root / "gtfs" / "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
           [["t1", "07:00:00", "07:00:00", "116000001", 1], ["t1", "07:05:00", "07:05:00", "116000002", 2],
            ["t2", "07:00:00", "07:00:00", "116000003", 1], ["t2", "07:05:00", "07:05:00", "116000002", 2],
            ["t3", "07:00:00", "07:00:00", "116000004", 1], ["t3", "07:05:00", "07:05:00", "116000002", 2],
            ["t4", "07:00:00", "07:00:00", "116000005", 1], ["t4", "07:05:00", "07:05:00", "116000002", 2]])
    _write(root / "registry" / "strike_route_registry.csv", ["route_id", "strike_state"],
           [["R1", "affected"], ["R2", "unaffected"], ["R3", "affected"], ["M1", "unaffected"]])
    _write(root / "network" / "absorbing_routes.csv", ["route_id", "route_name", "mode", "order_known", "stops",
                                                       "trips_0610"],
           [["G1", "경기", "gg", "True", "116000006", "10"]])
    head = ("date,hour,kind,stop_id,station_id,station_stop_id,source,flow_total,flow,flow_unknown,walkable,"
            "maeul_reach,gg_reach\n")
    body = ("20261026,7,access,116000001,K,116000002,observed,10,8,0,False,False,False\n"
            "20261026,7,access,116000003,K,116000002,observed,10,6,0,False,False,False\n"
            "20261026,7,access,116000005,K,116000002,observed,10,5,0,False,True,False\n"
            "20261026,7,access,116000004,K,116000002,observed,10,4,0,True,False,False\n")
    (root / "demand").mkdir(parents=True, exist_ok=True)
    with gzip.open(root / "demand" / "demand_flows.csv.gz", "wt", encoding="utf-8") as f:
        f.write(head + body)
    with gzip.open(root / "demand" / "stop_route_boardings.csv.gz", "wt", encoding="utf-8") as f:
        f.write("date,hour,stop_id,route_id,boardings,strike_state\n"
                "20261026,7,116000001,R1,30,affected\n20261026,7,116000001,R3,10,affected\n")
    return root


def fac(fid: str, lat_off: float) -> MS.Facility:
    return MS.Facility(fid, "medical", "병원", fid, "구로구", LAT + lat_off, 126.85)


class MustRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = must_bundle(Path(self.tmp.name))
        self.stops = MS.load_stops(self.root)

    def rule(self, facilities, include_gg=True, override=None):
        run = MS.running_stops(self.root, include_gg, override)["stops"]
        return MS.compute(facilities, self.stops, run, "116", MS.MustConfig(include_gg=include_gg))

    def test_running_route_nearby_means_not_must(self):
        self.assertEqual(self.rule([fac("f1", 2 * STEP)])["by_facility"]["f1"]["reason"], "served_by_running")

    def test_no_stop_in_radius(self):
        far = MS.Facility("f2", "market", "상설장", "f2", "구로구", LAT, 126.86)
        self.assertEqual(self.rule([far])["by_facility"]["f2"]["reason"], "out_of_network")

    def test_nearest_and_tie_by_id(self):
        self.assertEqual(self.rule([fac("f3", 0.0004)])["by_facility"]["f3"]["stop_id"], "116000001")
        everything = {"R1", "R2", "R3", "M1"}                 # 모든 GTFS 노선 파업 → 116000001·116000002 둘 다 후보
        self.assertEqual(self.rule([fac("f4", STEP / 2)], override=everything)["by_facility"]["f4"]["stop_id"],
                         "116000001")                          # 같은 거리면 정류장 ID 사전순

    def test_other_gu(self):
        self.assertEqual(self.rule([fac("f5", 4 * STEP)])["by_facility"]["f5"]["reason"], "other_gu")

    def test_gg_exclusion(self):
        f = [fac("f6", 6 * STEP)]
        self.assertEqual(self.rule(f)["by_facility"]["f6"]["reason"], "served_by_running")
        self.assertEqual(self.rule(f, include_gg=False)["by_facility"]["f6"]["reason"], "must")

    def test_partial_strike_override_changes_running_routes(self):
        # R1만 파업이면 R3·R2가 계속 운행 → 116000004(R3 정차) 근처 시설은 필수가 아니다
        f = [fac("f7", 3 * STEP)]
        self.assertEqual(self.rule(f)["by_facility"]["f7"]["reason"], "must")              # 전면 파업
        self.assertEqual(self.rule(f, override={"R1"})["by_facility"]["f7"]["reason"], "served_by_running")


class StrikeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = must_bundle(Path(self.tmp.name))

    def test_draw_deterministic(self):
        a, b = K.draw_strike(self.root, 0.5, 2), K.draw_strike(self.root, 0.5, 2)
        self.assertEqual(a.routes, b.routes)
        self.assertEqual(len(a.routes), 1)
        self.assertEqual(K.draw_strike(self.root, 1.0, 0).routes, {"R1", "R3"})

    def test_full_strike_equals_bundle_and_keeps_flags(self):
        rows, st = K.strike_rows(self.root, K.full_strike(self.root))
        got = {(r.stop_id, r.station_stop_id): (float(r.flow), r.maeul_reach) for r in rows}
        self.assertEqual(got[("116000001", "116000002")], (8.0, False))
        self.assertNotIn(("116000003", "116000002"), got)       # R2(파업 안 한 시내버스)가 간다 → 흡수
        self.assertEqual(got[("116000005", "116000002")], (5.0, True))   # 마을버스 흡수는 정밀 흡수에 맡긴다
        self.assertEqual(st["flow_bundle"], 19.0)

    def test_partial_strike_share_and_running_city(self):
        only_r1 = K.StrikeSet(frozenset({"R1"}), ("R1", "R3"), 0.5)
        rows, _ = K.strike_rows(self.root, only_r1)
        got = {(r.stop_id, r.station_stop_id): float(r.flow) for r in rows}
        self.assertAlmostEqual(got[("116000001", "116000002")], 8 * 30 / 40)

    def test_all_bus_no_absorption(self):
        rows, st = K.strike_rows(self.root, None, "all")
        self.assertEqual(st["mode"], "all_bus_no_absorption")
        self.assertEqual(st["flow_out"], 30.0)
        self.assertTrue(all(not r.maeul_reach and not r.gg_reach for r in rows))


def must_model(must, budget_cycles=None, keep_n0=True):
    cycles = budget_cycles or {"rA": 3600, "rB": 2400, "rC": 1200, "rD": 1200}
    keys = {"rA": (("a", "x"),), "rB": (("b", "x"),), "rC": (("c", "x"),), "rD": (("d", "x"),)}
    od = {("a", "x"): [5, 5, 5, 5], ("b", "x"): [5, 5, 5, 5], ("c", "x"): [200, 200, 200, 200],
          ("d", "x"): [150, 150, 150, 150]}
    return A.Model([(r, cycles[r], keys[r]) for r in sorted(cycles)], od, A.AllocParams(respect_n0=keep_n0),
                   must=must)


class MustAllocTest(unittest.TestCase):
    def test_hard_cover(self):
        free = A.allocate(must_model(None), 4)
        self.assertNotIn("rA", free["n"])
        held = A.allocate(must_model({"m1": ("rA",)}), 4)
        self.assertIn("rA", held["n"])
        self.assertTrue(held["must"]["stops"]["m1"]["covered"])
        for name, run in held["inits"].items():
            self.assertIn("rA", run["n"], name)                       # 국소 탐색이 덮음을 깨지 않는다

    def test_uncovered_reasons_and_rest_allocated(self):
        res = A.allocate(must_model({"m1": ("rA",), "m2": ("rB",), "m3": ()}), 2)
        got = {x["stop"]: x["reason"] for x in res["must"]["must_stop_uncovered"]}
        self.assertEqual(got, {"m1": "budget", "m3": "no_candidate"})
        self.assertIn("rB", res["n"])
        self.assertTrue(any("필수 정류장 못 덮음" in w for w in res["warnings"]))

    def test_must_before_equity_priority(self):
        """필수 정류장(valid, 하드)이 위반 인원(형평 등)보다 먼저: 덮음을 깨서 근사를 키우는 해는 고르지 않는다."""
        held = A.allocate(must_model({"m1": ("rA",)}), 4)
        free = A.allocate(must_model(None), 4)
        self.assertLess(held["value"], free["value"])

    def test_min_tph_option(self):
        held = A.allocate(must_model({"m1": ("rA",)}), 6)
        tph = A.allocate(A.Model([(r, c, k) for r, c, k in [("rA", 3600, (("a", "x"),)), ("rC", 1200, (("c", "x"),))]],
                                 {("a", "x"): [5] * 4, ("c", "x"): [200] * 4}, A.AllocParams(), must={"m1": ("rA",)},
                                 must_min_tph=4), 6)
        self.assertGreaterEqual(tph["n"]["rA"], 4)
        self.assertEqual(held["must"]["definition"], "개설 노선 중 하나 이상이 첫·끝이 아닌 정차로 지남")


class InsertMustTest(unittest.TestCase):
    def test_inserted_stop_is_interior(self):
        net = line_network(6)
        g = Graph(net, 10, 7200)
        got = _insert_must(g, ("S1", "S3"), 1, "S2")
        self.assertIsNotNone(got)
        stops, turn = got
        self.assertEqual(stops, ("S1", "S2", "S3"))
        self.assertIn("S2", stops[1:-1])


class RulesTest(unittest.TestCase):
    def test_weighted_quantile(self):
        self.assertEqual(RU.weighted_quantile([(1, 1), (2, 1), (3, 2)], 0.5), 2)
        self.assertEqual(RU.weighted_quantile([(1, 1), (2, 1), (3, 2)], 0.999), 3)
        self.assertIsNone(RU.weighted_quantile([], 0.5))

    def test_constants_from_dict(self):
        c = RU.RuleConstants.from_dict({"_설명": "x", "length_mult": [1.0, 2.0]})
        self.assertEqual(c.length_mult, (1.0, 2.0))

    def test_hub_min_count_fills_from_largest(self):
        from types import SimpleNamespace
        totals = {"A": 1000.0, "B": 50.0, "C": 40.0, "D": 5.0}
        net = SimpleNamespace(stations={k: SimpleNamespace(access_stop=f"a{k}", egress_stop=None) for k in totals})
        dem = SimpleNamespace(station_total=lambda: dict(totals))
        self.assertEqual(sorted(hubs_of(dem, net, Params(hub_min=500))), ["A"])
        self.assertEqual(sorted(hubs_of(dem, net, Params(hub_min=500, hub_min_count=3))), ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
