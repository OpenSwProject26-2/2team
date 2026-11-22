"""파업이 없을 때 역까지 소요 시간 비교(delay): 합성 자료만 쓴다.

평소 시간 = 대기 + 차내, 표 한쪽에 칸이 없으면 평소 시간 없음, n < 5 칸, served/unserved/censored 처리,
평소 + 0·10·20분 안 도착 비율, 최근접 순위 분위수(+∞ 포함), 판정(감소 + 44대 ≤ 600초), CLI.
"""

from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from busstrike.planning.routegen import delay as D

HEADER = "passenger_id,origin,destination,time_s,outcome,reason,physical,vehicle_id,boarding_s,arrival_s,refused"
COLS = ["origin_stop", "station_id", "hour", "value_s", "n"]


def tmpdir(tc: unittest.TestCase) -> Path:
    d = tempfile.TemporaryDirectory()
    tc.addCleanup(d.cleanup)
    return Path(d.name)


def write_table(path: Path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)


def out_row(pid, origin, dest, time_s, outcome, arrival_s=None, boarding_s=None):
    """passenger_outcomes.csv 1행(dict, csv.DictReader와 같은 모양)."""
    vals = [f'["{pid}",0]', str(origin), str(dest), str(time_s), outcome,
            "no_connection" if outcome == "unserved" else "",
            {"served": "arrived", "unserved": "waiting", "censored": "onboard"}[outcome],
            "v1" if outcome == "censored" else "",
            "" if boarding_s is None else str(boarding_s), "" if arrival_s is None else str(arrival_s), "0"]
    return dict(zip(HEADER.split(","), vals))


def rec(outcome, shuttle_s=None, origin="O1", station="K1", hour=7, pid="p"):
    return D.Record(pid, origin, station, hour, outcome, shuttle_s)


class NearestRankTest(unittest.TestCase):
    def test_definition(self):
        v = [50, 10, 40, 20, 30]
        self.assertEqual(D.nearest_rank(v, 0.5), 30)      # ceil(2.5) = 3번째
        self.assertEqual(D.nearest_rank(v, 0.9), 50)      # ceil(4.5) = 5번째
        self.assertEqual(D.nearest_rank([10, 20, 30, 40], 0.5), 20)   # 짝수면 아래쪽 값(보간 없음)
        self.assertEqual(D.nearest_rank(list(range(1, 11)), 0.9), 9)  # ceil(9.0) = 9번째
        self.assertEqual(D.nearest_rank([7], 0.01), 7)
        self.assertIsNone(D.nearest_rank([], 0.5))
        with self.assertRaises(ValueError):
            D.nearest_rank([1], 0)

    def test_inf(self):
        inf = math.inf
        self.assertEqual(D.nearest_rank([100, 200, inf, inf], 0.5), 200)
        self.assertEqual(D.nearest_rank([100, inf, inf, inf], 0.5), inf)
        self.assertEqual(D.nearest_rank([100, 200, 300, inf], 0.9), inf)


class LoadNormalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        write_table(self.tmp / "w.csv", [
            ["O1", "K1", 7, 300, 20],
            ["O1", "K1", 8, 240, 12],    # 차내 표에 이 칸 없음
            ["O2", "K1", 7, 180, 4],     # n < 5 (원래 표에 없어야 하는 칸)
            ["O3", "K2", 6, 100.4, 9],
        ])
        write_table(self.tmp / "v.csv", [
            ["O1", "K1", 7, 900, 30],
            ["O2", "K1", 7, 600, 40],
            ["O3", "K2", 6, 500, 5],     # n = 5는 쓴다
            ["O4", "K2", 6, 500, 50],    # 대기 표에 이 칸 없음
        ])

    def test_sum_and_missing(self):
        n = D.load_normal(self.tmp / "w.csv", self.tmp / "v.csv")
        self.assertEqual(n, {("O1", "K1", 7): 1200, ("O3", "K2", 6): 600})
        self.assertNotIn(("O1", "K1", 8), n)   # 한쪽만 있음
        self.assertNotIn(("O4", "K2", 6), n)
        self.assertNotIn(("O2", "K1", 7), n)   # 대기 칸 n < 5

    def test_duplicate_cell(self):
        write_table(self.tmp / "d.csv", [["O1", "K1", 7, 1, 9], ["O1", "K1", 7, 2, 9]])
        with self.assertRaises(ValueError):
            D.load_normal(self.tmp / "d.csv", self.tmp / "v.csv")


class ShuttleRecordsTest(unittest.TestCase):
    def test_parse(self):
        rows = [
            out_row("a", 116000001, 114000064, 25200 + 60, "served", boarding_s=25500, arrival_s=26400),
            out_row("b", 116000001, 114000064, 33873, "unserved"),
            out_row("c", 116000001, 114000064, 28000, "censored", boarding_s=28100),
            out_row("d", 116000001, 999, 28000, "served", boarding_s=28100, arrival_s=28500),  # 역 아님
        ]
        recs, skipped = D.shuttle_records(rows, {"114000064": "K1"})
        self.assertEqual(skipped, 1)
        self.assertEqual([r.outcome for r in recs], ["served", "unserved", "censored"])
        a, b, c = recs
        self.assertEqual((a.origin, a.station_id, a.hour, a.shuttle_s), ("116000001", "K1", 7, 26400 - 25260))
        self.assertEqual(b.hour, 9)            # 33873 // 3600
        self.assertIsNone(b.shuttle_s)
        self.assertIsNone(c.shuttle_s)

    def test_csv_example_row(self):
        tmp = tmpdir(self)
        p = tmp / "passenger_outcomes.csv"
        p.write_text(HEADER + "\n" + '"[""od000001-t032400"",0]",116000001,114000064,33873,unserved,no_connection,'
                     "waiting,,,,0\n", encoding="utf-8")
        recs, _ = D.shuttle_records(D.read_outcomes(p), {"114000064": "K1"})
        self.assertEqual(recs[0].passenger_id, '["od000001-t032400",0]')
        self.assertEqual(recs[0].outcome, "unserved")

    def test_bad_rows(self):
        with self.assertRaises(ValueError):
            D.shuttle_records([out_row("a", 1, 2, 0, "served")], {"2": "K"})   # arrival_s 없음
        bad = out_row("a", 1, 2, 0, "unserved")
        bad["outcome"] = "lost"
        with self.assertRaises(ValueError):
            D.shuttle_records([bad], {"2": "K"})


class DelayMetricsTest(unittest.TestCase):
    NORMAL = {("O1", "K1", 7): 1000, ("O1", "K1", 8): 800}

    def test_served_unserved_censored(self):
        recs = [
            rec("served", 900),            # delay −100
            rec("served", 1500),           # +500
            rec("served", 2100),           # +1100
            rec("served", 2500),           # +1500
            rec("unserved"),               # +∞
            rec("censored"),               # +∞
            rec("served", 999, hour=9),    # 평소 시간 없음
            rec("unserved", hour=9),       # 평소 시간 없음
        ]
        m = D.delay_metrics(recs, self.NORMAL)
        self.assertEqual((m["passengers"], m["served"], m["unserved"], m["censored"]), (8, 5, 2, 1))
        self.assertEqual((m["with_baseline"], m["no_baseline"]), (6, 2))
        self.assertEqual(m["coverage"], 6 / 8)
        self.assertEqual((m["served_with_baseline"], m["not_arrived"]), (4, 2))
        # served만: [−100, 500, 1100, 1500]
        self.assertEqual(m["median_served_s"], 500)
        self.assertEqual(m["p90_served_s"], 1500)
        # 전원(+∞ 2명): [−100, 500, 1100, 1500, ∞, ∞] → 중앙 3번째, p90 6번째
        self.assertEqual(m["median_all_s"], 1100)
        self.assertEqual(m["p90_all_s"], math.inf)
        # 분모 = 평소 시간 있는 6명(못 도착한 2명 포함, 평소 시간 없는 2명 제외)
        self.assertEqual(m["share_within"], {0: 1 / 6, 600: 2 / 6, 1200: 3 / 6})

    def test_thresholds_boundary(self):
        recs = [rec("served", 1000), rec("served", 1600), rec("served", 2200), rec("served", 2201)]
        m = D.delay_metrics(recs, self.NORMAL, thresholds=(0, 600, 1200))
        self.assertEqual(m["share_within"], {0: 0.25, 600: 0.5, 1200: 0.75})   # delay ≤ T(같으면 포함)
        m = D.delay_metrics(recs, self.NORMAL, thresholds=(300,))
        self.assertEqual(m["share_within"], {300: 0.25})
        self.assertEqual(m["thresholds_s"], [300])

    def test_median_all_inf(self):
        recs = [rec("served", 1100), rec("unserved"), rec("censored")]
        m = D.delay_metrics(recs, self.NORMAL)
        self.assertEqual(m["median_served_s"], 100)
        self.assertEqual(m["median_all_s"], math.inf)

    def test_no_baseline_at_all(self):
        recs = [rec("served", 500, hour=6), rec("unserved", hour=6)]
        m = D.delay_metrics(recs, self.NORMAL)
        self.assertEqual((m["with_baseline"], m["no_baseline"]), (0, 2))
        self.assertIsNone(m["median_served_s"])
        self.assertIsNone(m["median_all_s"])
        self.assertEqual(m["share_within"], {0: None, 600: None, 1200: None})
        self.assertEqual(D.delay_metrics([], self.NORMAL)["coverage"], None)

    def test_by_hour(self):
        recs = [rec("served", 1200, hour=7), rec("unserved", hour=7), rec("served", 700, hour=8)]
        m = D.delay_metrics(recs, self.NORMAL)
        self.assertEqual(sorted(m["by_hour"]), [7, 8])
        self.assertEqual(m["by_hour"][7]["median_served_s"], 200)
        self.assertEqual(m["by_hour"][7]["share_within"][600], 0.5)
        self.assertEqual(m["by_hour"][8]["median_served_s"], -100)
        self.assertNotIn("by_hour", m["by_hour"][7])

    def test_n_below_5_means_no_baseline(self):
        tmp = tmpdir(self)
        write_table(tmp / "w.csv", [["O1", "K1", 7, 300, 4]])
        write_table(tmp / "v.csv", [["O1", "K1", 7, 700, 50]])
        normal = D.load_normal(tmp / "w.csv", tmp / "v.csv")
        m = D.delay_metrics([rec("served", 1000)], normal)
        self.assertEqual((m["with_baseline"], m["no_baseline"]), (0, 1))


class JudgeTest(unittest.TestCase):
    @staticmethod
    def fleet(med_all, med_served):
        return {"median_all_s": med_all, "median_served_s": med_served}

    def test_pass(self):
        j = D.judge({11: self.fleet(math.inf, 900), 22: self.fleet(1200, 700), 44: self.fleet(500, 400)})
        self.assertTrue(j["monotonic_decrease"])
        self.assertTrue(j["top_fleet_within_limit"])
        self.assertTrue(j["pass"])
        self.assertEqual(j["medians_s"], {11: math.inf, 22: 1200, 44: 500})
        self.assertEqual(j["other"]["key"], "median_served_s")
        self.assertTrue(j["other"]["pass"])

    def test_limit_boundary(self):
        self.assertTrue(D.judge({11: self.fleet(900, 0), 22: self.fleet(700, 0), 44: self.fleet(600, 0)})["pass"])
        j = D.judge({11: self.fleet(900, 0), 22: self.fleet(700, 0), 44: self.fleet(601, 0)})
        self.assertTrue(j["monotonic_decrease"])
        self.assertFalse(j["top_fleet_within_limit"])
        self.assertFalse(j["pass"])

    def test_not_monotonic(self):
        j = D.judge({11: self.fleet(800, 0), 22: self.fleet(900, 0), 44: self.fleet(300, 0)})
        self.assertFalse(j["monotonic_decrease"])
        self.assertTrue(j["top_fleet_within_limit"])
        self.assertFalse(j["pass"])
        j = D.judge({11: self.fleet(500, 0), 22: self.fleet(500, 0), 44: self.fleet(300, 0)})   # 같으면 실패
        self.assertFalse(j["monotonic_decrease"])
        j = D.judge({11: self.fleet(math.inf, 0), 22: self.fleet(math.inf, 0), 44: self.fleet(300, 0)})
        self.assertFalse(j["monotonic_decrease"])                                              # ∞ = ∞

    def test_inf_or_none_at_44(self):
        j = D.judge({11: self.fleet(900, 0), 22: self.fleet(800, 0), 44: self.fleet(math.inf, 0)})
        self.assertFalse(j["monotonic_decrease"])
        self.assertFalse(j["top_fleet_within_limit"])
        j = D.judge({11: self.fleet(900, 0), 22: self.fleet(800, 0), 44: self.fleet(None, 0)})
        self.assertFalse(j["pass"])

    def test_key_and_order(self):
        by = {44: self.fleet(math.inf, 300), 11: self.fleet(math.inf, 900), 22: self.fleet(math.inf, 600)}
        j = D.judge(by, fleets=(44, 11, 22), key="median_served_s")
        self.assertEqual(j["fleets"], [11, 22, 44])
        self.assertTrue(j["pass"])
        self.assertFalse(j["other"]["pass"])
        with self.assertRaises(KeyError):
            D.judge({11: self.fleet(1, 1), 22: self.fleet(1, 1)})


class CliTest(unittest.TestCase):
    def test_end_to_end(self):
        tmp = tmpdir(self)
        write_table(tmp / "w.csv", [["O1", "K1", 7, 300, 20]])
        write_table(tmp / "v.csv", [["O1", "K1", 7, 700, 20]])
        (tmp / "map.csv").write_text("stop_id,station_id\nS1,K1\n", encoding="utf-8")
        shuttle = {11: [None, None], 22: [2000, None], 44: [1400, 1500]}   # 셔틀 시간(None = 못 도착)
        paths = []
        for f, times in shuttle.items():
            p = tmp / f"out{f}.csv"
            with open(p, "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=HEADER.split(","))
                w.writeheader()
                for i, t in enumerate(times):
                    w.writerow(out_row(f"p{i}", "O1", "S1", 25200, "unserved" if t is None else "served",
                                       arrival_s=None if t is None else 25200 + t))
            paths.append(str(p))
        out = tmp / "res" / "out.json"
        rc = D.main(["--wait", str(tmp / "w.csv"), "--invehicle", str(tmp / "v.csv"), "--outcomes", *paths,
                     "--dest-map", str(tmp / "map.csv"), "--out", str(out), "--fleets", "11,22,44"])
        self.assertEqual(rc, 0)
        res = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(res["normal_cells"], 1)
        m11, m22, m44 = (s["metrics"] for s in res["scenarios"])
        self.assertEqual(m11["median_all_s"], "inf")       # JSON에서 ∞는 문자열
        self.assertEqual(m22["median_all_s"], 1000)
        self.assertEqual(m44["median_all_s"], 400)
        self.assertEqual(m44["share_within"], {"0": 0.0, "600": 1.0, "1200": 1.0})
        self.assertTrue(res["judge"]["pass"])

    def test_dest_map_conflict(self):
        tmp = tmpdir(self)
        (tmp / "map.csv").write_text("stop_id,station_id\nS1,K1\nS1,K1\nS2,K2\n", encoding="utf-8")
        self.assertEqual(D.load_dest_map(tmp / "map.csv"), {"S1": "K1", "S2": "K2"})
        (tmp / "bad.csv").write_text("stop_id,station_id\nS1,K1\nS1,K2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            D.load_dest_map(tmp / "bad.csv")


if __name__ == "__main__":
    unittest.main()
