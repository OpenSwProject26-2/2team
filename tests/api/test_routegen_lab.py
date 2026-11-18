"""routegen 실험실: 도로망 모듈(합성 OSM)과 대시보드 서버 연결.

합성 자료만 쓴다(실제 묶음·OSM 자료는 저장소에 넣지 않는다).
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "routegen_lab"))

from roadnet import RoadNet  # noqa: E402

from busstrike.api.d_dashboard import Store, make_handler  # noqa: E402

LAT, LON = 37.5, 126.9
DLAT, DLON = 0.0009, 0.00113          # 약 100m


def node(i, x, y):
    return {"type": "node", "id": i, "lat": LAT + y * DLAT, "lon": LON + x * DLON}


def make_root(d: Path, oneway_ns: bool = False) -> None:
    """십자 도로: 동서 도로(노드 1..5, y=0)와 남북 도로(노드 6..10, x=2). 교차점은 노드 3.

    정류장: W(x=0)·E(x=4)는 동서 도로, S(y=-2)·N(y=2)는 남북 도로. 기존 버스 구간은 W→E(동서 노선), S→N(남북 노선)뿐.
    W→N처럼 교차로에서 꺾는 구간은 기존 망에 없다.
    """
    nodes = [node(1, 0, 0), node(2, 1, 0), node(3, 2, 0), node(4, 3, 0), node(5, 4, 0),
             node(6, 2, -2), node(7, 2, -1), node(8, 2, 1), node(9, 2, 2)]
    ways = [{"type": "way", "id": 100, "nodes": [1, 2, 3, 4, 5], "tags": {"highway": "secondary"}},
            {"type": "way", "id": 101, "nodes": [6, 7, 3, 8, 9],
             "tags": {"highway": "secondary", **({"oneway": "yes"} if oneway_ns else {})}}]
    (d / "osm.json").write_text(json.dumps({"elements": nodes + ways}), encoding="utf-8")
    (d / "network").mkdir()
    stops = {"W": (0, 0), "E": (4, 0), "S": (2, -2), "N": (2, 2)}
    rows = ["stop_id,stop_name,stop_lat,stop_lon,gu"]
    for k, (x, y) in stops.items():
        rows.append(f"{k},{k},{LAT + y * DLAT},{LON + x * DLON},테스트구")
    (d / "network" / "stops.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    prof = [[21600, 60], [25200, 60], [28800, 60], [32400, 60]]
    segs = [{"from_stop_id": "W", "to_stop_id": "E", "distance_m": 400, "profile": prof},
            {"from_stop_id": "S", "to_stop_id": "N", "distance_m": 400, "profile": prof}]
    (d / "network" / "travel_times.json").write_text(
        json.dumps({"network_snapshot_id": "test-buf2km-0610-v2", "schema_version": 1, "segments": segs}), encoding="utf-8")


class RoadNetTest(unittest.TestCase):
    def test_turn_at_junction_becomes_segment(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d)
            rn = RoadNet(d / "osm.json", d)
            stats = rn.build()
            self.assertEqual(stats["bus_segments_matched"], 2)
            keys = {(s["from_stop_id"], s["to_stop_id"]) for s in rn.road_doc["segments"]}
            self.assertIn(("W", "N"), keys)          # 교차로에서 꺾는 구간이 생긴다
            self.assertIn(("E", "W"), keys)          # 양방향 도로라 반대 방향도 생긴다
            seg = next(s for s in rn.road_doc["segments"] if (s["from_stop_id"], s["to_stop_id"]) == ("W", "N"))
            self.assertGreater(seg["distance_m"], 350)   # 도로 거리(약 400m) — 직선(약 280m)이 아니다
            # 시간 = 거리 ÷ 그 도로 버스 속도(400m를 60초 → 약 6.7m/s) → 약 60초
            self.assertTrue(all(50 <= t <= 70 for _, t in seg["profile"]))

    def test_oneway_is_respected(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d, oneway_ns=True)
            rn = RoadNet(d / "osm.json", d)
            rn.build()
            keys = {(s["from_stop_id"], s["to_stop_id"]) for s in rn.road_doc["segments"]}
            self.assertNotIn(("N", "S"), keys)       # 남북 도로가 일방(남→북)이면 역방향 구간이 없다
            self.assertNotIn(("N", "W"), keys)

    def test_no_skip_over_intermediate_stop(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d)
            rows = (d / "network" / "stops.csv").read_text(encoding="utf-8").rstrip("\n").split("\n")
            rows.append(f"M,M,{LAT},{LON + 2 * DLON},테스트구")      # 교차점(노드 3)에 정류장 M
            (d / "network" / "stops.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            rn = RoadNet(d / "osm.json", d)
            rn.build()
            keys = {(s["from_stop_id"], s["to_stop_id"]) for s in rn.road_doc["segments"]}
            self.assertNotIn(("W", "N"), keys)       # 중간 정류장 M을 건너뛰지 않는다
            self.assertIn(("W", "M"), keys)
            self.assertIn(("M", "N"), keys)


class DashboardLabRoutingTest(unittest.TestCase):
    def test_lab_without_data_returns_503(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(), None))
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/lab/meta"
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(ctx.exception.code, 503)
            ctx.exception.close()
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as r:
                self.assertEqual(r.status, 200)          # 기존 경로는 그대로
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
