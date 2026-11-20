"""구별 설정 파일(gu_config)과 구 수요에 맞춘 허브 기준(hub_share)."""

import unittest
from types import SimpleNamespace

from busstrike.planning.routegen import generate as G
from busstrike.planning.routegen.gu_config import GuConfig

RAW = {
    "default": {
        "generate": {"distance_basis": "network"},
        "configs": {"base": {}, "hub135": {"hub_min": 135}},
        "absorb": {"maeul_cap": 35},
        "walk": {"radius_m": 400, "lambdas": [0], "generate": {"hub_min": 135, "max_stops": 12}},
    },
    "gu": {"guro": {"configs": {"hub135": {"hub_min": 200}, "auto": {"hub_share": 0.8}},
                    "absorb": {"maeul_cap": 45}, "walk": {"lambdas": [3]}}},
}


class GuConfigTest(unittest.TestCase):
    def test_rule_overrides_are_refused_by_default(self):
        # 설계 15절: 구별 덮어쓰기는 입력 종류만. RAW의 guro는 규칙(configs)·정책 상수(absorb·walk.lambdas)를 덮어쓴다
        with self.assertRaises(ValueError):
            GuConfig(RAW, "guro")
        ok = GuConfig({"default": {}, "gu": {"guro": {"walk": {"service_starts": ["07:00"]}}}}, "guro")
        self.assertEqual(ok.walk["service_starts"], ["07:00"])
        self.assertEqual(ok.gu_overrides, [{"path": "walk.service_starts", "kind": "input"}])

    def test_gu_overrides_default(self):
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            c = GuConfig(RAW, "guro", allow_rule_override=True)          # 옛 결과 재현용: 경고만
        self.assertTrue(any("구별 덮어쓰기" in str(x.message) for x in w))
        self.assertEqual(list(c.configs), ["base", "hub135", "auto"])
        self.assertEqual(c.params("hub135", home_gu="구로구").hub_min, 200)        # 구 값이 기본을 덮는다
        self.assertEqual(c.params("base").distance_basis, "network")               # 공통 generate는 모든 설정에
        self.assertEqual(c.absorb_params().maeul_cap, 45)
        self.assertEqual(c.walk["lambdas"], [3])
        self.assertEqual(c.walk_params().max_stops, 12)
        other = GuConfig(RAW, "dobong")                                             # 덮어쓸 값이 없는 구는 기본 그대로
        self.assertEqual(other.params("hub135").hub_min, 135)
        self.assertEqual(other.absorb_params().maeul_cap, 35)

    def test_unknown_keys_are_errors(self):
        with self.assertRaises(ValueError):
            GuConfig({"default": {"configs": {"x": {"hubmin": 1}}}}, "g")
        with self.assertRaises(ValueError):
            GuConfig({"default": {"walk": {"radius": 1}}}, "g")


class HubShareTest(unittest.TestCase):
    def _net_demand(self, totals):
        st = {k: SimpleNamespace(access_stop=f"a{k}", egress_stop=None) for k in totals}
        return SimpleNamespace(stations=st), SimpleNamespace(station_total=lambda: dict(totals))

    def test_threshold_follows_gu_demand(self):
        net, dem = self._net_demand({"A": 500, "B": 300, "C": 150, "D": 50})    # 합 1000
        p = G.Params(hub_share=0.8)                       # 큰 순 500+300 = 80% → 기준 300
        self.assertEqual(G.hub_threshold(dem, net, p), 300)
        self.assertEqual(sorted(G.hubs_of(dem, net, p)), ["A", "B"])
        self.assertEqual(G.hub_threshold(dem, net, G.Params(hub_share=0.95)), 150)
        self.assertEqual(G.hub_threshold(dem, net, G.Params(hub_share=1.0, hub_floor=60)), 60)   # 하한
        self.assertEqual(G.hub_threshold(dem, net, G.Params(hub_min=135)), 135)                  # 비우면 고정값

    def test_small_gu_gets_lower_threshold(self):
        big, dbig = self._net_demand({"A": 1500, "B": 900, "C": 400})
        small, dsmall = self._net_demand({"A": 300, "B": 180, "C": 80})
        p = G.Params(hub_share=0.8)
        self.assertGreater(G.hub_threshold(dbig, big, p), G.hub_threshold(dsmall, small, p))


if __name__ == "__main__":
    unittest.main()
