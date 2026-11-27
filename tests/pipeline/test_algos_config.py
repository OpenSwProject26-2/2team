"""12종 표, 평가 날짜·형평 기준 해석, 결정론에 쓰는 이름 규칙."""

from __future__ import annotations

import json
import unittest

from busstrike.pipeline import algos as AL
from busstrike.pipeline import run as RUN


class AlgosTest(unittest.TestCase):
    def test_twelve_with_korean_names(self):
        self.assertEqual(len(AL.ALGOS), 12)
        self.assertEqual(len({a.id for a in AL.ALGOS}), 12)
        names = {a.id: a.name for a in AL.ALGOS}
        self.assertEqual(names["sector_lg0_fill0"], "섹터 순회")
        self.assertEqual(names["feeder_lg1_fill0"], "피더 순회 + 호선")
        self.assertEqual(names["oneway_loop_lg0_fill1"], "편도 후 순회 + 소외")
        self.assertEqual(names["oneway_loop_lg1_fill1"], "편도 후 순회 + 둘 다")
        self.assertEqual(names["oneway_loop_lg0_fill0"], "편도 노선 생성 후 순회")

    def test_both_off_is_old_command(self):
        self.assertEqual(AL.BY_ID["oneway_loop_lg0_fill0"].walk_eval_args(), [])
        self.assertEqual(AL.BY_ID["feeder_lg0_fill0"].walk_eval_args(), ["--tour", "feeder", "--tour-pool", "tour_feeder"])
        self.assertEqual(AL.BY_ID["sector_lg0_fill0"].walk_eval_args(), ["--tour", "sector", "--tour-pool", "tour_sector"])

    def test_options_independent(self):
        g = {a.id: a.gen_overrides() for a in AL.ALGOS}
        self.assertEqual(g["feeder_lg1_fill0"], {"line_group": True})
        self.assertEqual(g["feeder_lg0_fill1"], {"fill": True, "fill_bonus": 1.0, "fill_top": 5, "fill_pool": 60})
        self.assertEqual(json.loads(AL.BY_ID["sector_lg1_fill1"].walk_eval_args()[-1]),
                         {"line_group": True, "fill": True, "fill_bonus": 1.0, "fill_top": 5, "fill_pool": 60})

    def test_select(self):
        self.assertEqual(len(AL.select("all")), 12)
        with self.assertRaises(ValueError):
            AL.select("tour_pair")


class ConfigTest(unittest.TestCase):
    def test_days(self):
        self.assertEqual(RUN.parse_days("2026-10-27..30"), ["20261027", "20261028", "20261029", "20261030"])
        self.assertEqual(RUN.parse_days("2026-10-28"), ["20261028"])

    def test_test_week_refused(self):
        with self.assertRaises(SystemExit):
            RUN.parse_days("2026-11-03")

    def test_floor_args(self):
        self.assertEqual(RUN.floor_args("none"), ["--equity-floor", "muni", "--far-floor", "none"])
        self.assertEqual(RUN.floor_args("muni"), ["--equity-floor", "muni"])
        self.assertEqual(RUN.floor_args("0.58"), ["--equity-floor", "muni", "--far-floor", "0.58"])
        self.assertEqual(RUN.floor_args("+0.05"), ["--equity-floor", "muni", "--far-floor", "+0.05"])
        with self.assertRaises(ValueError):
            RUN.floor_args("high")


if __name__ == "__main__":
    unittest.main()
