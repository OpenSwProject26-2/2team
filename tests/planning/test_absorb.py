"""정밀 흡수(routegen.absorb): 정차 조건과 남는 정원. 합성 자료만 쓴다."""

import csv
import gzip
import io
import tempfile
import unittest
from pathlib import Path

from busstrike.planning.routegen import data as D
from busstrike.planning.routegen.absorb import Absorber, AbsorbParams

FLOW_HEAD = ["date", "hour", "kind", "stop_id", "station_id", "station_stop_id", "source", "flow_total", "flow",
             "flow_unknown", "walkable", "maeul_reach", "gg_reach", "maeul_routes", "gg_routes", "dist_km",
             "in_network", "gu", "station_name", "stop_name"]


def write_csv(path: Path, head: list[str], rows: list[list], gz: bool = False) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
    else:
        path.write_text(buf.getvalue(), encoding="utf-8")


def make_root(d: Path, maeul_boardings: float = 0.0) -> None:
    """역 K(정류장 KS, 주변 KS2). 마을 M1: A → B → KS(시간당 운행 1회). 경기 G1: A → KS2, 경기 G2: B → C(역에 안 섬)."""
    write_csv(d / "network" / "absorbing_routes.csv", ["route_id", "route_name", "mode", "order_known", "stops", "trips_0610"],
              [["M1", "마을1", "maeul", "True", "A B KS", "4"], ["G1", "경기1", "gg", "True", "A X KS2", "8"],
               ["G2", "경기2", "gg", "True", "A B C", "8"]])
    write_csv(d / "network" / "station_stops.csv",
              ["station_id", "station_name", "kind", "station_stop_id", "rule", "transfers", "dist_m", "candidates", "status"],
              [["K", "역", "access", "KS", "r", "1", "10", "KS KS2", "ok"], ["K", "역", "egress", "KS", "r", "1", "10", "KS KS2", "ok"]])
    write_csv(d / "demand" / "stop_route_boardings.csv.gz", ["date", "hour", "stop_id", "route_id", "boardings", "strike_state"],
              [["20261026", "7", "A", "M1", str(maeul_boardings), "unaffected"]], gz=True)
    rows = []
    # A → K: 50명(마을 M1·경기 G1·G2가 기존 판정상 후보), B → K: 30명(마을 M1, 경기 G2)
    for stop, flow, mr, gr in (("A", 50, "M1", "G1 G2"), ("B", 30, "M1", "G2")):
        rows.append(["20261026", "7", "access", stop, "K", "KS", "observed", flow, flow, 0, "False", "True", "True",
                     mr, gr, 1.0, "True", "구", "역", stop])
    write_csv(d / "demand" / "demand_flows.csv.gz", FLOW_HEAD, rows, gz=True)


class AbsorbTest(unittest.TestCase):
    def load(self, d: Path, **kw):
        rows = D.load_flows(d)
        return rows, Absorber(d, AbsorbParams(**kw))

    def test_gg_requires_stop_and_station(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d)
            rows, ab = self.load(d)
            a, b = rows
            self.assertEqual(ab.candidates(a, "gg"), ["G1"])     # G2는 역 정류장에 서지 않는다
            self.assertEqual(ab.candidates(b, "gg"), [])         # B에 서는 경기 노선 중 역에 가는 것 없음
            self.assertEqual(ab.candidates(a, "maeul"), ["M1"])  # 마을은 기본값이 기존 판정(정차 조건 없음)

    def test_capacity_limits_maeul(self):
        """마을 M1: 시간당 1회 × 정원 35 − 기존 탑승(승차 10 × 0.6 = 6) = 남는 29석. 요청 80명 → 29명만."""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d, maeul_boardings=10)
            rows, ab = self.load(d)
            res, s = ab.absorb(rows, True, False)
            self.assertAlmostEqual(s["maeul_req"], 80.0)
            self.assertAlmostEqual(s["maeul"], 29.0)
            left = {r.stop_id: f for r, f in res}
            self.assertAlmostEqual(left["A"], 50 - 29 * 50 / 80)   # 요청 비율대로 나눔
            self.assertAlmostEqual(left["B"], 30 - 29 * 30 / 80)

    def test_maeul_then_gg(self):
        """둘 다: 마을이 먼저 채우고 남은 몫을 경기가. 경기 G1: 시간당 2회 × 60 × (1 − 0.6) = 48석."""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_root(d)
            rows, ab = self.load(d)
            res, s = ab.absorb(rows, True, True)
            self.assertAlmostEqual(s["maeul"], 35.0)              # 기존 탑승 0 → 정원 35
            left_a_after_maeul = 50 - 35 * 50 / 80
            self.assertAlmostEqual(s["gg"], left_a_after_maeul)    # A의 남은 몫(약 28명) < 48석 → 전부
            self.assertAlmostEqual({r.stop_id: f for r, f in res}["A"], 0.0)


if __name__ == "__main__":
    unittest.main()
