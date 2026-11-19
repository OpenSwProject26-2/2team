"""3단계: 형평 지표(탈 수 있는 수요 비율 두 값), 배분기 형평 하한(만족 해 선택, 불가능 시 경고), 노선 대당 하한,
구간별 최대 재차 인원 정원 항(좌석 재사용), 운행 시작 시각."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "planning"))

from test_reach import Flow, make_index  # noqa: E402

from busstrike.planning.routegen import allocate as A  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen.equity import EquityIndex, far_stops, home_end  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach  # noqa: E402


class EquityMetricTest(unittest.TestCase):
    """동네 정류장 h(역 800m 안), f1·f2(역 800m 밖), 다른 구 정류장 o. 역 1801(개봉, 지정 정류장 G1)."""

    def setUp(self):
        self.st = {"G1", "Y1"}
        self.od = {("h", "G1"): 100.0, ("f1", "G1"): 50.0, ("G1", "f2"): 30.0, ("o", "G1"): 999.0}
        self.nb800 = {"h": {"h": 0.0, "G1": 700.0}, "f1": {"f1": 0.0, "x": 300.0}, "f2": {"f2": 0.0, "G1": 900.0}}
        self.eq = EquityIndex.build(self.od, self.st, {"h", "f1", "f2"}, self.nb800, 800.0)

    def test_home_end_and_far(self):
        self.assertEqual(home_end(("h", "G1"), self.st), "h")
        self.assertEqual(home_end(("G1", "f2"), self.st), "f2")           # egress: 도착 쪽이 동네
        self.assertEqual(home_end(("Y1", "G1"), self.st), "Y1")           # 둘 다 역이면 출발 쪽
        self.assertEqual(far_stops({"h", "f1", "f2", "G1"}, self.nb800, self.st), {"f1", "f2"})  # 900m는 밖
        self.assertAlmostEqual(self.eq.den_total, 180.0)                  # 다른 구(o)는 분모에서 뺀다
        self.assertAlmostEqual(self.eq.den_far, 80.0)

    def test_two_values(self):
        r = self.eq.ratios({("h", "G1"), ("G1", "f2"), ("o", "G1")})
        self.assertAlmostEqual(r["total"], 130 / 180)
        self.assertAlmostEqual(r["far"], 30 / 80)
        self.assertEqual(self.eq.ratios(set())["total"], 0.0)

    def test_numerator_is_reach_rule(self):
        """분자 = 평가 규칙으로 자기 역(같은 호선 대체 포함)까지 갈 수 있는 수요. 방향이 틀린 노선은 세지 않는다."""
        ix = make_index([Flow("access", "h", "1801", "G1", "1"), Flow("access", "f1", "1801", "G1", "1")])
        od = {("h", "G1"): 100.0, ("f1", "G1"): 50.0}
        eq = EquityIndex.build(od, {"G1", "Y1"}, {"h", "f1"}, self.nb800, 800.0)
        same = Reach(ix, EvalRule(station_mode="same_line", walk_radius_m=0), {})
        fixed = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=0), {})
        routes = [["f1", "Y1"]]                                            # 역곡(같은 1호선 경인)으로 간다
        self.assertAlmostEqual(eq.of_plan(same, routes)["far"], 1.0)
        self.assertAlmostEqual(eq.of_plan(same, routes)["total"], 50 / 150)
        self.assertEqual(eq.of_plan(fixed, routes)["total"], 0.0)          # 역 고정이면 못 탄다
        self.assertEqual(eq.of_plan(same, [["Y1", "f1"]])["total"], 0.0)   # access인데 역 쪽이 앞(방향이 틀림)


def model(routes, od, equity=None, **kw):
    return A.Model(routes, od, A.AllocParams(**kw), equity=equity)


class SegmentCapacityTest(unittest.TestCase):
    """A→B→C→D 노선(주기 3,600초, 1대 → 시간당 좌석 45). 승객 k1 A→B 45명, k2 C→D 45명(6시만)."""

    od = {("A", "B"): [45.0, 0, 0, 0], ("C", "D"): [45.0, 0, 0, 0]}

    def test_seat_reuse(self):
        routes = [("r", 3600, (("A", "B"), ("C", "D")), ((0, 1), (2, 3)))]
        old = model(routes, self.od, cap_mode="route", wait_s=3600).detail({"r": 1})
        new = model(routes, self.od, cap_mode="segment", wait_s=3600).detail({"r": 1})
        self.assertAlmostEqual(old["total"], 45.0)                         # 좌석을 한 번만 쓴다
        self.assertAlmostEqual(new["total"], 90.0)                         # B에서 내린 자리를 C에서 다시 쓴다
        self.assertAlmostEqual(new["cap_cut"], 0.0)

    def test_bottleneck_cut_only_crossing(self):
        """k3 A→D 45명이 모든 구간을 지나면 구간 A→B는 90명 → 거기 지나는 승객만 반으로."""
        od = {**self.od, ("A", "D"): [45.0, 0, 0, 0]}
        routes = [("r", 3600, (("A", "B"), ("A", "D"), ("C", "D")), ((0, 1), (0, 3), (2, 3)))]
        det = model(routes, od, wait_s=3600).detail({"r": 1})
        self.assertAlmostEqual(det["total"], 22.5 + 22.5 + 22.5)          # 모든 구간이 90명 → 셋 다 0.5배
        routes2 = [("r", 3600, (("A", "B"), ("B", "C")), ((0, 1), (1, 2)))]
        det2 = model(routes2, {("A", "B"): [45.0, 0, 0, 0], ("B", "C"): [45.0, 0, 0, 0]}, wait_s=3600).detail({"r": 1})
        self.assertAlmostEqual(det2["total"], 90.0)

    def test_no_spans_is_old_rule(self):
        routes = [("r", 3600, (("A", "B"), ("C", "D")))]
        self.assertAlmostEqual(model(routes, self.od, wait_s=3600).detail({"r": 1})["total"], 45.0)

    def test_spans_from_reach(self):
        ix = make_index([Flow("access", "h", "1801", "G1", "1"), Flow("egress", "h2", "1801", "G1", "1")])
        r = Reach(ix, EvalRule(station_mode="fixed", walk_radius_m=0), {})
        ks, sp = r.spans(["h", "G1x", "G1", "h2"])
        self.assertEqual(ks, (("G1", "h2"), ("h", "G1")))
        self.assertEqual(sp, ((2, 3), (0, 2)))                             # egress는 지정 정류장 G1에서 그대로 탄다


class ServiceStartTest(unittest.TestCase):
    def test_hour_weights(self):
        self.assertEqual(A.AllocParams().hour_weights(), (1.0, 1.0, 1.0, 1.0))
        self.assertEqual(A.AllocParams(service_start_s=7 * 3600).hour_weights(), (0.0, 1.0, 1.0, 1.0))
        self.assertEqual(A.AllocParams(service_start_s=6 * 3600 + 1800).hour_weights(), (0.5, 1.0, 1.0, 1.0))

    def test_no_six_oclock_demand(self):
        od = {("a", "H"): [100.0, 10.0, 10.0, 10.0], ("b", "H"): [0.0, 40.0, 40.0, 40.0]}
        routes = [("ra", 600, (("a", "H"),)), ("rb", 600, (("b", "H"),))]
        m6 = model(routes, od)
        m7 = model(routes, od, service_start_s=7 * 3600)
        self.assertAlmostEqual(m6.value({"ra": 1}), 130.0)
        self.assertAlmostEqual(m7.value({"ra": 1}), 30.0)                  # 6시 100명은 못 태운다
        self.assertEqual(A.allocate(m6, 1)["n"], {"ra": 1})
        self.assertEqual(A.allocate(m7, 1)["n"], {"rb": 1})                # 07:00 시작이면 다른 노선이 낫다


class EquityFloorTest(unittest.TestCase):
    """노선 rbig: 역 가까운 동네 수요 500(탑승 많음), rfar: 역 800m 밖 수요 100. 1대만 쓸 수 있다."""

    od = {("near", "H"): [125.0] * 4, ("far", "H"): [25.0] * 4}
    routes = [("rbig", 600, (("near", "H"),)), ("rfar", 600, (("far", "H"),))]

    def floor(self, total, far):
        return A.EquityFloor({("near", "H"): 500.0, ("far", "H"): 100.0}, {("far", "H"): 100.0}, 600.0, 100.0,
                             total, far)

    def test_free_picks_most_riders(self):
        self.assertEqual(A.allocate(model(self.routes, self.od), 1)["n"], {"rbig": 1})

    def test_floor_picks_feasible(self):
        m = model(self.routes, self.od, self.floor(0.1, 0.5))             # 역 800m 밖 50% 이상
        a = A.allocate(m, 1)
        self.assertEqual(a["n"], {"rfar": 1})
        self.assertTrue(a["feasible"])
        self.assertEqual(a["warnings"], [])
        self.assertAlmostEqual(a["equity"]["far"], 1.0)
        # 2대면 둘 다 연다(하한 만족 해 중 근사 최대)
        a2 = A.allocate(model(self.routes, self.od, self.floor(0.1, 0.5)), 2)
        self.assertEqual(a2["n"], {"rbig": 1, "rfar": 1})

    def test_infeasible_warns(self):
        m = model(self.routes, self.od, self.floor(0.95, 0.5))            # 1대로는 전체 95%를 못 넘는다
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            a = A.allocate(m, 1)
        self.assertFalse(a["feasible"])
        self.assertTrue(any("형평 하한 미달" in str(x.message) for x in w))
        self.assertEqual(a["n"], {"rbig": 1})                              # 가장 가까운 해(부족 70명 < 470명)
        self.assertAlmostEqual(a["equity"]["short_total"], 0.95 * 600 - 500, places=1)
        self.assertAlmostEqual(a["equity"]["short_far"], 50.0, places=1)
        self.assertIn("형평 하한 미달", a["warnings"][0])

    def test_local_search_keeps_floor(self):
        """여러 노선 중: 하한을 만족하는 해 가운데 근사 최대. 국소 탐색이 하한을 깨는 이동을 받지 않는다."""
        od = {("near", "H"): [125.0] * 4, ("near2", "H"): [100.0] * 4, ("far", "H"): [10.0] * 4,
              ("far2", "H"): [12.0] * 4}
        routes = [("r1", 600, (("near", "H"),)), ("r2", 600, (("near2", "H"),)), ("r3", 600, (("far", "H"),)),
                  ("r4", 600, (("far2", "H"),))]
        ef = A.EquityFloor({k: sum(v) for k, v in od.items()}, {("far", "H"): 40.0, ("far2", "H"): 48.0},
                           1000.0, 88.0, 0.0, 0.5)
        a = A.allocate(model(routes, od, ef), 2)
        self.assertTrue(a["feasible"])
        self.assertEqual(a["n"], {"r1": 1, "r4": 1})                       # r4(먼 곳 48 ≥ 44) + 가장 많이 태우는 r1


class RouteFloorTest(unittest.TestCase):
    def test_per_vehicle_floor(self):
        """대당 근사 하한 90명: 하루 40명 노선은 열지 않는다. 남는 차를 몰아 넣어 대당이 떨어져도 경고."""
        od = {("a", "H"): [100.0] * 4, ("b", "H"): [10.0] * 4}
        routes = [("ra", 600, (("a", "H"),)), ("rb", 600, (("b", "H"),))]
        free = A.allocate(model(routes, od), 2)
        self.assertEqual(free["n"], {"ra": 1, "rb": 1})                    # 하한이 없으면 1대로 40명 노선도 연다
        a = A.allocate(model(routes, od, route_min_per_vehicle=90), 2)
        self.assertEqual(a["n"], {"ra": 2})                                 # 400명 ÷ 2대 = 200 ≥ 90
        self.assertTrue(a["feasible"])
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            b = A.allocate(model(routes, od, route_min_per_vehicle=90), 5)  # 5대면 어디에 둬도 대당 80
        self.assertFalse(b["feasible"])
        self.assertTrue(b["route_shortfall"])
        self.assertIn("노선 대당 하한 미달", b["warnings"][-1])


class EquityConfigTest(unittest.TestCase):
    def test_equity_section(self):
        cfg = gu_config.load(ROOT / "experiments" / "routegen_lab" / "gu_params.json", "guro")
        self.assertEqual(cfg.equity["floor"], "muni")
        self.assertEqual(cfg.equity["far_m"], 800)
        self.assertGreater(cfg.equity["route_min_per_vehicle"], 0)
        self.assertEqual(cfg.walk["service_starts"], ["07:00", "06:00"])   # 구로: 지자체안과 같은 07:00 먼저
        self.assertNotIn("targets", cfg.walk)                              # 고정 목표·역 근처 할인은 기본 경로에 없다
        self.assertNotIn("near_discount", cfg.walk)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p.json"
            for bad in ({"floor": "half"}, {"flor": "muni"}):
                p.write_text(json.dumps({"default": {"equity": bad}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    gu_config.load(p, "guro")
        with self.assertRaises(ValueError):
            A.AllocParams(cap_mode="seat")


if __name__ == "__main__":
    unittest.main()
