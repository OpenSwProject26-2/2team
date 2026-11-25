"""export/<plan id>.json 형식(시민 길찾기·지도용): 정류장 이름·좌표·kind, 고리를 닫는 도로 모양, 카드 ID 없음."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from busstrike.pipeline import export as EX

STOPS = "stop_id,stop_name,stop_lat,stop_lon,gu\nS1,역앞,37.50,126.88,구로구\nS2,시장,37.51,126.89,구로구\nS3,학교,37.52,126.88,구로구\n"


class FakeRoad:
    """RoadNet 대신: S1→S2만 도로 경로(중간 점 하나), 나머지는 직선."""

    def __init__(self):
        self.stops = {"S1": (37.50, 126.88), "S2": (37.51, 126.89), "S3": (37.52, 126.88)}

    def path_any(self, a, b):
        la, lo = self.stops[a]
        lb, ob = self.stops[b]
        if (a, b) == ("S1", "S2"):
            return [[lo, la], [126.885, 37.505], [ob, lb]]
        return [[round(lo, 6), round(la, 6)], [round(ob, 6), round(lb, 6)]]


class ExportTest(unittest.TestCase):
    def test_format(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            (root / "network").mkdir(parents=True)
            (root / "network" / "stops.csv").write_text(STOPS, encoding="utf-8")
            plan = Path(d) / "plan.json"
            plan.write_text(json.dumps({"plan_id": "p", "routes": [{"route_id": "r01", "stops": ["S1", "S2", "S3"],
                                                                     "vehicles": 2}]}), encoding="utf-8")
            rep = Path(d) / "plan.plan_report.json"
            rep.write_text(json.dumps({"routes": [{"candidate": "c1", "cycle_s": 2400}]}), encoding="utf-8")
            ex = EX.Exporter(root, None, roadnet=FakeRoad(), station_stops={"S1"})
            doc = EX.write(ex, "feeder_lg0_fill0__floor_none", plan, rep, Path(d) / "out" / "x.json")
            again = json.loads((Path(d) / "out" / "x.json").read_text(encoding="utf-8"))
        self.assertEqual(doc, again)
        self.assertEqual(set(doc), {"plan_id", "routes"})
        r = doc["routes"][0]
        self.assertEqual(set(r), {"route_id", "name", "vehicles", "cycle_min", "headway_min", "stops", "shape", "shape_straight"})
        self.assertEqual((r["vehicles"], r["cycle_min"], r["headway_min"]), (2, 40.0, 20.0))
        self.assertEqual([s["kind"] for s in r["stops"]], ["station", "stop", "stop"])
        self.assertEqual([s["seq"] for s in r["stops"]], [1, 2, 3])
        self.assertEqual(r["stops"][1]["name"], "시장")
        self.assertEqual(r["shape"][0], [37.50, 126.88])               # [lat, lon]
        self.assertIn([37.505, 126.885], r["shape"])                   # 도로 중간 점
        self.assertEqual(r["shape"][-1], [37.50, 126.88])              # 고리를 닫는 S3 → S1 포함
        self.assertEqual(r["shape_straight"], 2)                       # S2→S3, S3→S1은 직선
        self.assertNotIn("TRCR", json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
