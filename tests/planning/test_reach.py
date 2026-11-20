"""평가 규칙(reach): 지선 꼬리표, 목적 역 대체, 같은 정류장을 여러 역이 쓰는 경우, 역 주변 정류장 도착, 걷기 반경,
도보 시간 판정, 설계(덮는 키)와 평가(재배정)가 같은 규칙인지, 지자체안 같은 길 이음 규칙."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "routegen_lab"))

from build_station_lines import branch_tags  # noqa: E402
from eval_municipal import same_road  # noqa: E402

from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach, StationIndex, load_lines, served_with_walk  # noqa: E402

Flow = namedtuple("Flow", "kind stop_id station_id station_stop_id flow")


def tags_of(*pairs):
    return {sid: frozenset(branch_tags(sid, line)) for sid, line in pairs}


class BranchTagTest(unittest.TestCase):
    def test_line1(self):
        self.assertEqual(branch_tags("1801", "1"), ["1-경인"])          # 개봉(경인)
        self.assertEqual(branch_tags("1821", "1"), ["1-경인"])          # 온수
        self.assertEqual(branch_tags("1702", "1"), ["1-경부"])          # 가산디지털단지(경부)
        self.assertEqual(branch_tags("1403", "1"), ["1-경부"])          # 장항선
        for both in ("1006", "1007", "1701", "0150", "1903"):           # 영등포·신도림·구로·서울역·도봉
            self.assertEqual(branch_tags(both, "1"), ["1-경부", "1-경인"], both)

    def test_line2_5(self):
        for s in ("0247", "0248", "0249", "0200"):
            self.assertEqual(branch_tags(s, "2"), ["2-신정지선"])
        self.assertEqual(branch_tags("0234", "2"), ["2", "2-신정지선"])  # 신도림
        self.assertEqual(branch_tags("0233", "2"), ["2"])                # 대림
        self.assertEqual(branch_tags("0245", "2"), ["2-성수지선"])
        self.assertEqual(branch_tags("0211", "2"), ["2", "2-성수지선"])
        self.assertEqual(branch_tags("2558", "5"), ["5-마천지선"])
        self.assertEqual(branch_tags("2549", "5"), ["5", "5-마천지선"])
        self.assertEqual(branch_tags("2752", "7"), ["7"])

    def test_table_file(self):
        tags, _ = load_lines(ROOT / "experiments" / "routegen_lab" / "station_lines.csv")
        self.assertEqual(tags["1007"], frozenset({"1-경부", "1-경인"}))
        self.assertEqual(tags["0248"], frozenset({"2-신정지선"}))
        self.assertNotIn("4405", tags)                                    # 신림선은 자료에 없다(보고 대상)


def make_index(flows=()):
    """작은 구: 역 1801(경인)·1803(경인)·1702(경부)·1007(신도림 1호선)·0248(신정지선)·0233(2호선)·2752(7호선)·2751(7호선).
    1821(온수 1호선)과 2752(온수 7호선)는 지정 정류장 ON을 같이 쓴다."""
    designated = {}
    cand = {}
    for k, st in (("1801", "G1"), ("1803", "Y1"), ("1702", "KA"), ("1007", "SD"), ("0248", "YC"), ("0233", "DR"),
                  ("1821", "ON"), ("2752", "ON"), ("2751", "CW")):
        designated[(k, "access")] = st
        designated[(k, "egress")] = st
        cand[k] = {st, st + "x"}                                         # 지정 정류장 + 주변 정류장 하나
    tags = tags_of(("1801", "1"), ("1803", "1"), ("1702", "1"), ("1007", "1"), ("0248", "2"), ("0233", "2"),
                   ("1821", "1"), ("2752", "7"), ("2751", "7"))
    ix = StationIndex(designated, cand, tags)
    ix.add_flows(flows)
    return ix


class AcceptableTest(unittest.TestCase):
    def test_modes(self):
        ix = make_index()
        r = EvalRule(station_mode="same_line")
        self.assertEqual(sorted(ix.acceptable("1801", "access", r)), ["1007", "1801", "1803", "1821"])
        self.assertEqual(sorted(ix.acceptable("1702", "access", r)), ["1007", "1702"])
        self.assertEqual(sorted(ix.acceptable("0248", "access", r)), ["0248"])   # 신정지선: 대림(본선)은 아님
        self.assertEqual(ix.acceptable("1801", "access", EvalRule(station_mode="fixed")), ["1801"])
        self.assertEqual(len(ix.acceptable("1801", "access", EvalRule(station_mode="any"))), 9)

    def test_missing_station_only_itself(self):
        ix = StationIndex({("4405", "access"): "BR"}, {"4405": {"BR"}}, {})
        self.assertEqual(ix.missing_lines, ["4405"])
        self.assertEqual(ix.acceptable("4405", "access", EvalRule()), ["4405"])


class SharedStopTest(unittest.TestCase):
    """온수(1821 1호선·2752 7호선)가 지정 정류장 ON을 같이 쓴다. 노선 h → CW(천왕 7호선)·Y1(역곡 1호선)."""

    def reach(self, flows, shared):
        return Reach(make_index(flows), EvalRule(station_mode="same_line", walk_radius_m=0, shared_stop=shared), {})

    def test_flow_union_mixed(self):
        both = [Flow("access", "h", "1821", "ON", "3"), Flow("access", "h", "2752", "ON", "2")]
        r = self.reach(both, "flow_union")
        self.assertEqual(r.cover_keys(["h", "CW"]), (("h", "ON"),))       # 7호선 승객 몫
        self.assertEqual(r.cover_keys(["h", "Y1"]), (("h", "ON"),))       # 1호선 승객 몫
        s = self.reach(both, "strict")
        self.assertEqual(s.cover_keys(["h", "CW"]), ())                   # 두 역 모두 받아들여야 함
        self.assertEqual(s.cover_keys(["h", "ON"]), (("h", "ON"),))

    def test_flow_union_single_station(self):
        only7 = [Flow("access", "h", "2752", "ON", "2")]
        r = self.reach(only7, "flow_union")
        self.assertEqual(r.cover_keys(["h", "CW"]), (("h", "ON"),))
        self.assertEqual(r.cover_keys(["h", "Y1"]), ())                   # 이 키는 7호선 승객뿐
        ref = self.reach(only7, "stop_union")                             # 참고 구현: 정류장을 쓰는 모든 역
        self.assertEqual(ref.cover_keys(["h", "Y1"]), (("h", "ON"),))


class ArrivalWalkTest(unittest.TestCase):
    nb = {"h": {"h": 0.0, "w150": 150.0, "w350": 350.0}, "G1": {"G1": 0.0, "far": 300.0}}

    def test_candidates_arrival(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=0), {})
        self.assertEqual(r.cover_keys(["h", "G1x"]), (("h", "G1"),))      # 역 주변 정류장에 내리면 도착
        self.assertEqual(r.cover_keys(["h", "far"]), ())                   # 지정 정류장 걷기는 없다
        legacy = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=400, station_arrival="walk_designated"),
                       self.nb)
        self.assertEqual(legacy.cover_keys(["h", "far"]), (("h", "G1"),))  # 예전 규칙: 지정 정류장 400m 안
        self.assertEqual(legacy.cover_keys(["h", "G1x"]), ())

    def test_walk_radius_home_side(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1")])
        for R, want in ((0, ()), (200, (("h", "G1"),)), (400, (("h", "G1"),))):
            r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=R), self.nb)
            self.assertEqual(r.cover_keys(["w150", "G1"]), want, R)
        r2 = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=200), self.nb)
        self.assertEqual(r2.cover_keys(["w350", "G1"]), ())
        r4 = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=400), self.nb)
        self.assertEqual(r4.cover_keys(["w350", "G1"]), (("h", "G1"),))
        self.assertEqual(r4.cover_keys(["G1", "w350"]), ())                 # access는 동네 쪽이 앞

    def test_egress_order(self):
        ix = make_index([Flow("egress", "h", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=400), self.nb)
        self.assertEqual(r.cover_keys(["G1x", "w150"]), (("G1", "h"),))
        self.assertEqual(r.cover_keys(["w150", "G1x"]), ())


class ReassignTest(unittest.TestCase):
    nb = {"h": {"h": 0.0, "w150": 150.0, "w350": 350.0}}

    def rows(self):
        return [{"aggregate_id": "a1", "origin": "h", "destination": "G1", "bin_start_s": "21600", "bin_width_s": "3600",
                 "passenger_count": "4"},
                {"aggregate_id": "a2", "origin": "h", "destination": "G1", "bin_start_s": "25200", "bin_width_s": "3600",
                 "passenger_count": "2"},
                {"aggregate_id": "a3", "origin": "zz", "destination": "G1", "bin_start_s": "25200", "bin_width_s": "3600",
                 "passenger_count": "1"}]

    def test_order_and_same_as_design(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1"), Flow("access", "zz", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(station_mode="same_line", walk_radius_m=400), self.nb)
        # 노선 1: 걸어서(350m) 원래 역 주변 정류장, 노선 2: 걸어서(150m) 대체 역(역곡 1803) → 원래 역 먼저
        routes = [["w350", "G1x"], ["w150", "Y1"]]
        new, per, st = r.reassign(self.rows(), routes)
        self.assertEqual((new[0]["origin"], new[0]["destination"]), ("w350", "G1x"))
        self.assertEqual(per["a1"], [350.0, "same_station"])
        self.assertEqual(new[0]["aggregate_id"], "a1")                     # 행을 합치지 않고 ID를 그대로
        self.assertEqual(st["none"], 1)
        self.assertEqual(st["reachable"], 6)
        # 원래 역이 없으면 대체 역, 그다음 동네 쪽 도보가 짧은 것
        _, per2, st2 = r.reassign(self.rows(), [["w350", "Y1"], ["w150", "Y1"]])
        self.assertEqual(per2["a1"], [150.0, "alt_station"])
        # 설계(덮는 키)와 평가(재배정)가 같은 승객을 탈 수 있다고 센다
        for rs in (routes, [["w350", "Y1"]], [["h", "G1"]], [["G1x", "h"]]):
            _, _, s = r.reassign(self.rows(), rs)
            cover = set().union(*(set(r.cover_keys(q)) for q in rs))
            design = sum(int(x["passenger_count"]) for x in self.rows() if (x["origin"], x["destination"]) in cover)
            self.assertEqual(design, s["reachable"], rs)

    def test_direct_kept(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(), self.nb)
        new, per, st = r.reassign(self.rows()[:1], [["w150", "h", "G1x", "G1"]])
        self.assertEqual((new[0]["origin"], new[0]["destination"]), ("h", "G1"))
        self.assertEqual(st["direct"], 4)

    def test_ride_tiebreak(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(walk_radius_m=0), {})
        rows = self.rows()[:1]
        new, _, _ = r.reassign(rows, [["h", "m", "G1", "G1x"]], ride=lambda seq, i, j: j - i)
        self.assertEqual(new[0]["destination"], "G1")        # 정류장 ID로는 G1 < G1x, 차내 시간도 짧다
        new, _, _ = r.reassign(rows, [["h", "G1x", "m", "G1"]], ride=lambda seq, i, j: j - i)
        self.assertEqual(new[0]["destination"], "G1")        # 그대로 탈 수 있으면 그대로(차내 시간보다 먼저)
        eg = Reach(make_index([Flow("egress", "h", "1801", "G1", "1")]), EvalRule(walk_radius_m=0), {})
        row = [{**rows[0], "origin": "G1", "destination": "h"}]
        # egress: 원래 지정 정류장 G1에서는 h로 못 가고, 주변 정류장 G1x에서 타는 노선이 둘 → 차내 시간이 짧은 쪽
        new, _, _ = eg.reassign(row, [["G1x", "m", "m2", "h"], ["G1x", "h"], ["h", "G1"]], ride=lambda seq, i, j: j - i)
        self.assertEqual((new[0]["origin"], new[0]["destination"]), ("G1x", "h"))
        b = eg.best_pair(("G1", "h"), [["G1x", "m", "m2", "h"], ["G1x", "h"]], ride=lambda seq, i, j: j - i)
        self.assertEqual(b[0][3], 1.0)


class WalkTimeTest(unittest.TestCase):
    def test_served_with_walk(self):
        rule = EvalRule()                                     # 분당 75m, 한도 1,200초
        self.assertEqual(rule.walk_s(400), 320)
        rows = [{"passenger_id": json.dumps(["a1", 0]), "outcome": "served", "time_s": "0", "boarding_s": "900"},   # 15분 + 320초
                {"passenger_id": json.dumps(["a1", 1]), "outcome": "served", "time_s": "0", "boarding_s": "840"},   # 14분 + 320초
                {"passenger_id": json.dumps(["a2", 0]), "outcome": "served", "time_s": "0", "boarding_s": "1200"},  # 걷지 않음
                {"passenger_id": json.dumps(["a3", 0]), "outcome": "unserved", "time_s": "0", "boarding_s": ""}]
        got = served_with_walk(rows, {"a1": 400.0}, rule)
        self.assertEqual(got, {"served": 3, "served_walk": 2, "dropped_by_walk_time": 1, "dropped_walkers": 1})
        self.assertEqual(served_with_walk(rows, {"a1": 400.0}, EvalRule(walk_speed_m_min=None))["served_walk"], 3)


class SameRoadTest(unittest.TestCase):
    def test_one_percent(self):
        self.assertTrue(same_road(573, 573, 0.01, False))     # 보람아파트 → 구로디지털단지역
        self.assertTrue(same_road(316, 315, 0.01, False))     # 0.3%
        self.assertFalse(same_road(158, 708, 0.01, False))    # 더 짧은 다른 길 → 속도 규칙
        self.assertFalse(same_road(1209, 1209, 0.01, True))   # 임시 정류장 경유
        self.assertFalse(same_road(573, 573, -1, False))      # 끔
        self.assertFalse(same_road(580, 573, 0.01, False))    # 1.2%


class ConfigTest(unittest.TestCase):
    def test_eval_section(self):
        cfg = gu_config.load(ROOT / "experiments" / "routegen_lab" / "gu_params.json", "guro")
        self.assertEqual(cfg.eval_rule(), EvalRule())
        self.assertEqual([c.label() for c in cfg.eval_combos()][4], "same_line-w200")
        self.assertEqual(len(cfg.eval_combos()), 9)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p.json"
            p.write_text(json.dumps({"default": {"eval": {"station_mod": "fixed"}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                gu_config.load(p, "guro")

    def test_from_root(self):
        with tempfile.TemporaryDirectory() as d:
            net = Path(d) / "network"
            net.mkdir()
            with open(net / "station_stops.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["station_id", "station_name", "kind", "station_stop_id", "rule", "transfers_exp5_0609",
                            "dist_m", "candidates", "status"])
                w.writerow(["1801", "개봉", "access", "A", "x", "1", "10", "A B", "ok"])
                w.writerow(["1801", "개봉", "egress", "B", "x", "1", "10", "A B", "ok"])
                w.writerow(["0230", "신림", "access", "C", "x", "1", "449", "C", "too_far"])
            ix = StationIndex.from_root(d, ROOT / "experiments" / "routegen_lab" / "station_lines.csv")
            self.assertEqual(ix.candidates["1801"], frozenset({"A", "B"}))
            self.assertEqual(ix.ok["access"], ["1801"])
            self.assertEqual(ix.key_info(("h", "A")), ("access", frozenset({"1801"})))


if __name__ == "__main__":
    unittest.main()


class GeneratorUsesRuleTest(unittest.TestCase):
    def test_builder_cover(self):
        from busstrike.planning.routegen import generate as G
        from busstrike.planning.routegen.data import Demand
        from busstrike.planning.routegen.graph import Graph
        from tests.planning.test_routegen import line_network
        net = line_network(6)
        od = {("S5", "H"): [1.0, 1.0, 1.0, 1.0]}
        d = Demand("affected", ("20261026",), {}, od)
        ix = StationIndex({("K", "access"): "H", ("K", "egress"): "H"}, {"K": {"H", "S6"}}, {})
        ix.add_flows([Flow("access", "S5", "K", "H", "4")])
        reach = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=400), {"S5": {"S5": 0.0, "S4": 353.0}}, list(od))
        old = G.Builder(net, Graph(net, 10, 3600), d, G.Params())
        new = G.Builder(net, Graph(net, 10, 3600), d, G.Params(), reach)
        self.assertEqual(old.cover_of(("S4", "H")), 0.0)
        self.assertEqual(new.cover_of(("S4", "H")), 4.0)          # 걸어서 S4, 역에 도착
        self.assertEqual(new.cover_keys_of(("S4", "S6")), (("S5", "H"),))   # 역 주변 정류장 S6에 내려도 도착
        self.assertEqual(old.cover_keys_of(("S4", "S6")), ())
