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


if __name__ == "__main__":
    unittest.main()
