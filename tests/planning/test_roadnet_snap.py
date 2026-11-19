"""정류장 붙이기(선분 투영 + 진행 방향별 정차 노드)와 U턴 판정(turns-v2). 검증 항목 나1·나4.

합성 OSM만 쓴다(실제 OSM·묶음 자료는 저장소에 넣지 않는다). 좌표 단위 1 = 약 100m.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "routegen_lab"))

from roadnet import RoadNet  # noqa: E402
from walknet import walk_neighbors  # noqa: E402

from busstrike.planning.routegen.data import Network, Stop  # noqa: E402
from busstrike.planning.routegen.graph import Graph, parse_turns  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

LAT, LON = 37.5, 126.9
DLAT, DLON = 0.0009, 0.00113          # 약 100m
PROF = [[21600, 60], [25200, 60], [28800, 60], [32400, 60]]


def node(i, x, y):
    return {"type": "node", "id": i, "lat": LAT + y * DLAT, "lon": LON + x * DLON}


def make(d: Path, ways: list[tuple[list[int], dict]], nodes: dict[int, tuple[float, float]],
         stops: dict[str, tuple[float, float]], segs: list[tuple[str, str]]) -> None:
    els = [node(i, x, y) for i, (x, y) in sorted(nodes.items())]
    els += [{"type": "way", "id": 100 + k, "nodes": ns, "tags": tags} for k, (ns, tags) in enumerate(ways)]
    (d / "osm.json").write_text(json.dumps({"elements": els}), encoding="utf-8")
    (d / "network").mkdir()
    rows = ["stop_id,stop_name,stop_lat,stop_lon,gu"]
    for k, (x, y) in stops.items():
        rows.append(f"{k},{k},{LAT + y * DLAT},{LON + x * DLON},테스트구")
    (d / "network" / "stops.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    ss = [{"from_stop_id": a, "to_stop_id": b, "distance_m": 100, "profile": PROF} for a, b in segs]
    (d / "network" / "travel_times.json").write_text(
        json.dumps({"network_snapshot_id": "syn", "schema_version": 1, "valid_from_s": 21600, "valid_until_s": 43200,
                    "segments": ss}), encoding="utf-8")


MAIN = {i: (i - 1, 0.0) for i in range(1, 7)}         # 동서 양방향 도로: 노드 1..6, x = 0..5
TWO_WAY = {"highway": "secondary"}


def graph_of(rn: RoadNet) -> Graph:
    stops = {s: Stop(s, s, la, lo, "테스트구") for s, (la, lo) in rn.stops.items()}
    net = Network(TravelTimeTable.from_dict(rn.road_doc), stops, {}, rn.turn_edges())
    return Graph(net, 10, 3600, "network")


class OppositeSidesTest(unittest.TestCase):
    """(1) 길 양쪽 정류장은 서로 다른 정차 노드에 붙는다."""

    def build(self, segs):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        stops = {"WA": (0.5, -0.15), "P": (2.5, -0.15), "EA": (4.5, -0.15),     # 남쪽(동쪽 방향 오른쪽)
                 "WB": (0.5, 0.15), "Q": (2.5, 0.15), "EB": (4.5, 0.15)}        # 북쪽(서쪽 방향 오른쪽)
        make(d, [(list(MAIN), TWO_WAY)], MAIN, stops, segs)
        rn = RoadNet(d / "osm.json", d)
        rn.build()
        return rn

    def test_bus_segments_decide_direction(self):
        rn = self.build([("WA", "P"), ("P", "EA"), ("EB", "Q"), ("Q", "WB")])
        self.assertNotEqual(rn.stop_nodes["P"], rn.stop_nodes["Q"])
        self.assertEqual(rn.proj["P"]["how"], "a")
        self.assertEqual(rn.proj["P"]["dirs"], [[3, 4]])        # 동쪽(3→4) 유향 간선 위
        self.assertEqual(rn.proj["Q"]["dirs"], [[4, 3]])        # 서쪽(4→3)
        keys = {(s["from_stop_id"], s["to_stop_id"]) for s in rn.road_doc["segments"]}
        self.assertNotIn(("WA", "Q"), keys)       # 맞은편 정류장에 그대로 서는 구간이 생기지 않는다
        self.assertNotIn(("P", "Q"), keys)        # 정류장에서 바로 맞은편으로 되돌아가는 구간도 없다
        self.assertIn(("WA", "P"), keys)

    def test_right_hand_rule_without_bus_segments(self):
        rn = self.build([])
        self.assertEqual(rn.proj["P"]["how"], "b")
        self.assertEqual(rn.proj["P"]["dirs"], [[3, 4]])        # 진행 방향 오른쪽에 정류장
        self.assertEqual(rn.proj["Q"]["dirs"], [[4, 3]])

    def test_walk_crosses_the_road(self):
        rn = self.build([("WA", "P"), ("P", "EA"), ("EB", "Q"), ("Q", "WB")])
        nb = walk_neighbors(rn, 400.0)
        # 투영점에서 잰다: 옆 거리 15m + 15m(진행 방향과 상관없이 길을 건넌다)
        self.assertAlmostEqual(nb["P"]["Q"], 30.0, delta=1.5)

    def test_old_node_snap_shares_node(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        make(d, [(list(MAIN), TWO_WAY)], MAIN, {"P": (2.05, -0.15), "Q": (2.05, 0.15)}, [])
        rn = RoadNet(d / "osm.json", d, snap_mode="node")
        self.assertEqual(rn.stop_nodes["P"], rn.stop_nodes["Q"])     # 예전 방식: 같은 노드(3)
        rn2 = RoadNet(d / "osm.json", d)
        self.assertNotEqual(rn2.stop_nodes["P"], rn2.stop_nodes["Q"])


class SideStreetStraightTest(unittest.TestCase):
    """(2) 곁길 노드 근처의 직진 정류장은 U턴이 아니다(예전 노드 붙이기에서는 U턴으로 잡혔다)."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = Path(t.name)
        nodes = dict(MAIN)
        nodes.update({7: (2.0, 0.3), 8: (2.5, 0.3)})            # 노드 3에서 북쪽으로 갔다가 동쪽으로 꺾이는 막다른 곁길
        ways = [(list(MAIN), TWO_WAY), ([3, 7, 8], {"highway": "service"})]
        # B는 곁길 끝 노드 8에서 18m, 큰길 노드 3·4에서 약 51m, 큰길 선분에서 12m
        stops = {"A": (0.5, 0.12), "B": (2.5, 0.12), "C": (4.5, 0.12)}
        make(self.d, ways, nodes, stops, [("A", "B"), ("B", "C")])

    def test_old_snap_reports_false_uturn(self):
        rn = RoadNet(self.d / "osm.json", self.d, snap_mode="node")
        rn.build()
        self.assertEqual(rn.stop_nodes["B"], (8,))
        self.assertTrue(graph_of(rn).is_uturn("A", "B", "C"))   # 곁길로 들어갔다 나오는 경로 → 오탐

    def test_edge_snap_is_straight(self):
        rn = RoadNet(self.d / "osm.json", self.d)
        rn.build()
        self.assertEqual(rn.proj["B"]["edge"], [3, 4])          # 큰길 선분에 투영
        self.assertEqual(rn.proj["B"]["dirs"], [[3, 4]])        # 기존 버스 구간 방위(동쪽)
        g = graph_of(rn)
        self.assertEqual(g.turns_format, "v2")
        self.assertFalse(g.is_uturn("A", "B", "C"))
        self.assertFalse(g.has_uturn(("A", "B", "C")))


class TrueReversalTest(unittest.TestCase):
    """(3) 실제로 되돌아가는 경로는 U턴이다."""

    def test_terminus_side_switch(self):
        # 막다른 큰길 끝의 종점 T: X(동쪽 방향) → T → Y(서쪽 방향, 맞은편). T는 근거가 엇갈려 양방향 정차.
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        stops = {"X": (1.5, -0.15), "T": (4.5, -0.1), "Y": (1.5, 0.15)}
        make(d, [(list(MAIN), TWO_WAY)], MAIN, stops, [("X", "T"), ("T", "Y")])
        rn = RoadNet(d / "osm.json", d)
        rn.build()
        self.assertEqual(rn.proj["T"]["how"], "c_conflict")    # 한 방향으로는 T→Y를 이을 수 없어 양방향 그대로
        self.assertEqual(len(rn.stop_nodes["T"]), 2)
        g = graph_of(rn)
        self.assertTrue(g.is_uturn("X", "T", "Y"))              # 도착 노드 ≠ 출발 노드(길 건너편으로)

    def loop_net(self, stops, segs):
        # 큰길에서 일방 곁길(4→7→8→5)로 들어갔다 나오는 길. 종점 T는 일방 곁길 위(한 방향 정류장).
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        nodes = dict(MAIN)
        nodes.update({7: (3.0, 0.6), 8: (4.0, 0.6)})
        ways = [(list(MAIN), TWO_WAY), ([4, 7, 8, 5], {"highway": "residential", "oneway": "yes"})]
        make(d, ways, nodes, stops, segs)
        rn = RoadNet(d / "osm.json", d)
        rn.build()
        return rn

    def test_loop_back_retrace(self):
        # X(큰길 동쪽 방향) → T(곁길 종점) → Y(큰길 서쪽 방향, 맞은편): T→Y는 곁길을 돌아 나와 큰길을 되짚는다.
        rn = self.loop_net({"X": (1.5, -0.15), "T": (3.5, 0.65), "Y": (1.5, 0.15)}, [("X", "T"), ("T", "Y")])
        self.assertEqual(rn.proj["T"]["how"], "oneway")
        rec = rn.turn_edges()
        self.assertEqual(rec["T>Y"]["s"], rec["X>T"]["e"])     # 같은 정차 노드에서 이어진다(건너편 전환 아님)
        g = graph_of(rn)
        self.assertTrue(g.is_uturn("X", "T", "Y"))              # 도착 전 300m 안의 큰길(3→4)을 반대로(4→3) 지난다

    def test_loop_through_not_uturn(self):
        rn = self.loop_net({"X": (1.5, -0.15), "T": (3.5, 0.65), "Z": (4.5, -0.15)}, [("X", "T"), ("T", "Z")])
        self.assertFalse(graph_of(rn).is_uturn("X", "T", "Z"))

    def test_through_route_not_uturn(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        stops = {"X": (0.5, -0.15), "T": (2.5, -0.1), "Z": (4.5, -0.15)}
        make(d, [(list(MAIN), TWO_WAY)], MAIN, stops, [("X", "T"), ("T", "Z")])
        rn = RoadNet(d / "osm.json", d)
        rn.build()
        self.assertFalse(graph_of(rn).is_uturn("X", "T", "Z"))


class UnsnappedTest(unittest.TestCase):
    """(4) 60m 안에 도로 선분이 없는 정류장은 붙이지 않고 목록에 남긴다."""

    def test_unsnapped_listed(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        stops = {"A": (0.5, -0.15), "B": (2.5, -0.15), "FAR": (2.5, 2.0)}       # FAR: 도로에서 약 200m
        make(d, [(list(MAIN), TWO_WAY)], MAIN, stops, [("A", "B"), ("B", "FAR")])
        rn = RoadNet(d / "osm.json", d)
        stats = rn.build()
        self.assertEqual(rn.unsnapped, ["FAR"])
        self.assertNotIn("FAR", rn.stop_nodes)
        self.assertEqual(stats["stops_snapped"], 2)
        self.assertEqual(stats["bus_segments_unmatched"], 1)
        self.assertEqual(rn.unmatched_segments, [{"from": "B", "to": "FAR", "why": "unsnapped"}])
        self.assertEqual(walk_neighbors(rn, 400.0)["FAR"], {"FAR": 0.0})

    def test_motorway_not_used_for_stops(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        nodes = dict(MAIN)
        nodes.update({11: (0.0, 0.3), 12: (5.0, 0.3)})
        ways = [(list(MAIN), TWO_WAY), ([11, 12], {"highway": "motorway"})]
        make(d, ways, nodes, {"S": (2.5, 0.25)}, [])            # 고속도로에서 5m, 큰길에서 25m
        rn = RoadNet(d / "osm.json", d)
        self.assertEqual(rn.proj["S"]["edge"], [3, 4])


class TurnsFormatTest(unittest.TestCase):
    def net(self, turns):
        tt = TravelTimeTable.from_dict({"schema_version": 1, "network_snapshot_id": "s", "valid_from_s": 21600,
                                        "valid_until_s": 43200, "segments": []})
        return Network(tt, {}, {}, turns)

    def test_v2_rules(self):
        turns = {"_meta": {"format": "turns-v2"},
                 "a>b": {"s": 1, "e": 10, "tail": [[1, 2], [2, 3]], "head": [[1, 2], [2, 3]]},
                 "b>c": {"s": 10, "e": 20, "tail": [[3, 4]], "head": [[3, 4], [4, 5]]},     # 직진
                 "b>d": {"s": 10, "e": 30, "tail": [[3, 2]], "head": [[3, 7], [7, 3], [3, 2]]},  # 돌아 나와 되짚기
                 "b>e": {"s": 11, "e": 40, "tail": [[3, 9]], "head": [[3, 9]]}}             # 건너편 노드에서 출발
        g = Graph(self.net(turns), 10, 3600)
        self.assertEqual(g.turns_format, "v2")
        self.assertFalse(g.is_uturn("a", "b", "c"))
        self.assertTrue(g.is_uturn("a", "b", "d"))
        self.assertTrue(g.is_uturn("a", "b", "e"))
        self.assertFalse(g.is_uturn("a", "b", "x"))             # 기록이 없으면 판단하지 않는다

    def test_old_format_still_read(self):
        fmt, _ = parse_turns({"S1>S2": [1, 2, 2, 3]})
        self.assertEqual(fmt, "v1")
        self.assertEqual(parse_turns(None)[0], None)
        with self.assertRaises(ValueError):
            parse_turns({"_meta": {"format": "turns-v9"}})
        with self.assertRaises(ValueError):
            parse_turns({"S1>S2": {"s": 1}})


class LongMatchTest(unittest.TestCase):
    """(5) 거리 상한보다 긴 U턴 없는 경로만 있는 기존 버스 구간 = 긴 경로 매칭.

    동쪽 일방 큰길(노드 1..11, x = 0..10)과 북쪽 일방 고리(x=7에서 북으로 200m → 서쪽으로 400m → x=3에서 남으로).
    A(x=6.5) → B(x=4.5)는 직선 200m, 상한 max(2×200, 200+400) = 600m인데 U턴 없는 경로는 고리를 도는 1,000m뿐이다.
    """

    def build(self, **kw):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        nodes = {i: (i - 1, 0.0) for i in range(1, 12)}
        nodes.update({21: (7.0, 2.0), 22: (3.0, 2.0)})
        ow = {"highway": "secondary", "oneway": "yes"}
        ways = [(list(range(1, 12)), ow), ([8, 21], ow), ([21, 22], ow), ([22, 4], ow)]
        stops = {"W": (1.5, -0.15), "B": (4.5, -0.15), "A": (6.5, -0.15)}
        make(d, ways, nodes, stops, [("W", "B"), ("A", "B")])
        rn = RoadNet(d / "osm.json", d, **kw)
        stats = rn.build()
        return d, rn, stats

    def test_long_match_records_turns_only(self):
        d, rn, stats = self.build()
        self.assertEqual(stats["bus_segments_matched"], 1)
        self.assertEqual(stats["bus_segments_long_match"], 1)
        self.assertEqual(stats["bus_segments_matched_with_uturn"], 0)
        self.assertEqual(stats["bus_segments_unmatched"], 0)
        info = stats["long_match"][0]
        self.assertEqual((info["from"], info["to"]), ("A", "B"))
        self.assertAlmostEqual(info["road_m"], 1000, delta=15)
        self.assertGreater(info["road_m"], 600)
        self.assertLessEqual(info["road_m"], info["cutoff_m"])
        turns = rn.turn_edges()
        self.assertIn("A>B", turns)                             # 조각 기록은 남는다
        self.assertEqual(turns["A>B"].get("l"), 1)
        self.assertIn([8, 21], turns["A>B"]["head"])
        seg = next(x for x in rn.road_doc["segments"] if (x["from_stop_id"], x["to_stop_id"]) == ("A", "B"))
        self.assertEqual(seg["distance_m"], 100)                # 실측 구간: 거리·시간 그대로
        self.assertEqual(seg["profile"], PROF)
        for e in ((8, 21), (21, 22), (22, 4)):                  # 고리는 버스 도로가 아니다
            self.assertNotIn(e, rn.bus_set)
        loop = {21, 22}
        for a, b in rn.added:                                   # 고리로 생긴 추가 구간 없음
            self.assertFalse(loop & set(rn.seg_path[(a, b)]))
        # 속도 표본에 안 들어간다: 중앙값은 W→B(300m/60s = 18km/h)만으로 정해진다
        self.assertEqual(stats["median_bus_speed_kmh"]["21600"], round(3.6 * seg_len(rn, "W", "B") / 60, 1))

    def test_long_match_off_leaves_unmatched(self):
        _, rn, stats = self.build(long_match=False)
        self.assertEqual(stats["bus_segments_long_match"], 0)
        self.assertEqual(stats["bus_segments_unmatched"], 1)    # 일방 고리라 도중 U턴 경로도 없다
        self.assertNotIn("A>B", rn.turn_edges())
        self.assertEqual(rn.unmatched_segments[0]["why"], "no_path")

    def test_write_net_marks_long_match(self):
        from roadnet import write_net
        d, rn, stats = self.build()
        write_net(rn, d / "net", stats)
        snap = json.loads((d / "net" / "snap.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["A"]["long_match"], ["A>B"])
        self.assertEqual(snap["B"]["long_match"], ["A>B"])
        self.assertNotIn("long_match", snap["W"])
        st = json.loads((d / "net" / "roadnet_stats.json").read_text(encoding="utf-8"))
        self.assertEqual(st["bus_segments_long_match"], 1)
        self.assertEqual(json.loads((d / "net" / "turns.json").read_text(encoding="utf-8"))["A>B"]["l"], 1)


    def test_long_match_too_fast_is_not_judged(self):
        """(1c 결정 5) 도로 경로 속도(경로 길이 ÷ 실측 최소 시간)가 상한을 넘는 긴 경로는 판단하지 않음."""
        _, rn, stats = self.build(long_max_kmh=30.0)          # 1,000m / 60초 = 약 60km/h > 30
        self.assertEqual(stats["bus_segments_long_match"], 0)
        self.assertEqual(stats["bus_segments_long_rejected"], 1)
        self.assertEqual(stats["long_rejected"][0]["from"], "A")
        self.assertGreater(stats["long_rejected"][0]["road_speed_kmh"], 30.0)
        self.assertEqual(stats["unmatched_by_reason"], {"long_too_fast": 1})
        self.assertEqual(stats["bus_segments_matched_with_uturn"], 0)
        self.assertNotIn("A>B", rn.turn_edges())               # 조각 기록 없음 = 끝까지 없음과 같은 처리
        self.assertEqual(stats["long_match_rule"]["max_road_speed_kmh"], 30.0)
        _, rn2, stats2 = self.build(long_max_kmh=None)          # 상한 끄기 = 1b 망
        self.assertEqual(stats2["bus_segments_long_match"], 1)
        self.assertEqual(stats2["bus_segments_long_rejected"], 0)


class RefineOnewayGuardTest(unittest.TestCase):
    """(1c 결정 7) 다듬기는 기존 버스 구간 방위와 반대 방향인 일방 선분으로 정류장을 옮기지 않는다(아현초등학교 사례).

    분리 차로: 남쪽 동행 일방(y=0)과 북쪽 서행 일방(y=0.25, 25m 북쪽), x = -4..9. 북→남 연결(x=0), 남→북 연결(x=4).
    S는 두 차로 사이(남쪽 10m, 북쪽 15m). S의 기존 버스 구간은 동쪽 P→S, S→Q(2개)와 서쪽 R→S(1개).
    S가 남쪽(동행)이면 R→S가 상한 안에서 안 이어지고, 북쪽(서행)이면 셋 다 이어진다. 그래서 규칙이 없으면 다듬기가
    S를 반대 차로(서행, 반대 표 2 > 같은 방향 표 1)로 옮긴다.
    """

    def build(self, **kw):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        nodes = {i: (i - 5, 0.0) for i in range(1, 15)}                 # 남쪽: id 1..14, x = -4..9
        nodes.update({i: (i - 25, 0.25) for i in range(21, 35)})       # 북쪽: id 21..34, x = -4..9
        ow = {"highway": "primary", "oneway": "yes"}
        ways = [(list(range(1, 15)), ow), (list(range(34, 20, -1)), ow), ([25, 5], ow), ([9, 29], ow)]
        stops = {"P": (-3.0, -0.1), "S": (2.5, 0.10), "Q": (8.0, -0.1), "R": (4.5, 0.35)}
        make(d, ways, nodes, stops, [("P", "S"), ("S", "Q"), ("R", "S")])
        rn = RoadNet(d / "osm.json", d, **kw)
        rn.build()
        return rn

    def test_without_guard_moves_to_opposite_carriageway(self):
        rn = self.build(refine_oneway_guard=False)
        self.assertEqual(rn.proj["S"]["how"], "refined")
        self.assertTrue(rn.proj["S"]["flipped"])
        self.assertEqual(rn.proj["S"]["highway"], "primary")
        self.assertGreater(rn.node[rn.stop_nodes["S"][0]][0], LAT + 0.2 * DLAT)   # 북쪽(서행) 차로

    def test_guard_keeps_bus_direction(self):
        rn = self.build()
        self.assertNotEqual(rn.proj["S"]["how"], "refined")
        self.assertLess(rn.node[rn.stop_nodes["S"][0]][0], LAT + 0.05 * DLAT)     # 남쪽(동행) 차로 그대로
        self.assertIn("S", rn.refine_guarded)
        self.assertGreaterEqual(rn.snap_stats["refined"]["oneway_against_skipped"], 1)


class OsmLinkTest(unittest.TestCase):
    """(1c 결정 1) 망 밖 이음: 원 OSM 도로 최단 경로(U턴만 금지, 버스 도로 여부 무관, 지나야 할 좌표)."""

    def build(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = Path(t.name)
        # 동서 양방향 큰길(노드 1..6, x=0..5, 버스 도로)과 남쪽 골목(7(x=1,y=-1) → 8(x=4,y=-1), 버스 안 다님)
        nodes = dict(MAIN)
        nodes.update({7: (1.0, -1.0), 8: (4.0, -1.0), 9: (2.5, -1.0)})
        ways = [(list(MAIN), TWO_WAY), ([2, 7, 9, 8, 5], {"highway": "residential"})]
        stops = {"A": (0.5, -0.15), "B": (4.5, -0.15), "C": (2.5, -0.15)}
        make(d, ways, nodes, stops, [("A", "C"), ("C", "B")])
        rn = RoadNet(d / "osm.json", d)
        rn.build()
        return rn

    def test_shortest_and_time(self):
        rn = self.build()
        got = rn.osm_link("A", "B")
        self.assertAlmostEqual(got["length_m"], 400, delta=10)             # 큰길 직진
        self.assertEqual([b for b, _ in got["profile"]], [b for b, _ in PROF])
        # 시간 = 도로 길이 ÷ 그 도로 버스 속도(실측 구간 A→C 200m/60s, C→B 200m/60s → 약 3.33m/s)
        self.assertAlmostEqual(got["profile"][0][1], 120, delta=4)

    def test_via_point_forces_side_road(self):
        rn = self.build()
        lat, lon = LAT - 1.0 * DLAT - 0.00005, LON + 2.5 * DLON               # 골목 위 좌표(약 5m 옆)
        got = rn.osm_link("A", "B", via=[(lat, lon)])
        self.assertAlmostEqual(got["length_m"], 600, delta=10)               # 골목으로 돌아간다(50+100+300+100+50)
        self.assertTrue({7, 9, 8} <= set(got["path"]))
        self.assertLess(got["via_offset_m"][0], 10)
        # 골목에는 버스 표본이 없다 → 그 시간대 전체 중앙값 속도. 시간은 길이에 비례해 는다
        self.assertGreater(got["profile"][0][1], rn.osm_link("A", "B")["profile"][0][1])

    def test_no_uturn(self):
        rn = self.build()
        # B(동행) → A(동행): 동쪽 끝(6)에서 돌아 나와야 하는데 U턴 금지라 막다른 큰길에서는 못 간다.
        # 골목 고리(2-7-9-8-5)를 돌면 된다: 그래도 경로가 있고, 방금 지난 조각을 되짚지 않는다.
        got = rn.osm_link("B", "A")
        if got is not None:
            p = got["path"]
            es = [rn.base(u, v) for u, v in zip(p, p[1:])]
            for x, y in zip(es, es[1:]):
                self.assertNotEqual(y, (x[1], x[0]))


def seg_len(rn: RoadNet, a: str, b: str) -> int:
    return rn.road_len[(a, b)]


if __name__ == "__main__":
    unittest.main()
