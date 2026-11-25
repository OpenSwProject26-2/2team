"""pareto.json의 hull(지배당하지 않음) 계산과 같은 plan 합치기(작은 합성 예제)."""

from __future__ import annotations

import unittest

from busstrike.pipeline import pareto as PA


def pt(i, fill, far, sha=None, algo="a", floor="none"):
    return {"id": i, "algo": algo, "floor": floor, "plan_sha256": sha or i, "served_rate": fill, "equity_far": far,
            "floor_met": True, "short_far": 0.0}


class HullTest(unittest.TestCase):
    def test_dominated_points_excluded(self):
        ps = [pt("A", 0.30, 0.50), pt("B", 0.35, 0.45), pt("C", 0.28, 0.48), pt("D", 0.30, 0.50 - 1e-9)]
        PA.mark_hull(ps)
        self.assertEqual({p["id"]: p["hull"] for p in ps}, {"A": True, "B": True, "C": False, "D": False})

    def test_ties_both_on_hull(self):
        ps = [pt("A", 0.30, 0.50), pt("B", 0.30, 0.50)]          # 같은 점은 서로 지배하지 않는다(엄격히 큰 것이 없음)
        PA.mark_hull(ps)
        self.assertTrue(all(p["hull"] for p in ps))

    def test_missing_metrics_not_on_hull(self):
        ps = [pt("A", None, None), pt("B", 0.1, 0.1)]
        PA.mark_hull(ps)
        self.assertEqual([p["hull"] for p in ps], [False, True])

    def test_muni_not_in_hull(self):
        doc = PA.document("guro", 11, 1.0, ["20261028"], 100.0, [pt("A", 0.2, 0.4)], {"id": "muni", "served_rate": 0.9,
                                                                                         "equity_far": 0.9})
        self.assertTrue(doc["plans"][0]["hull"])                   # 지자체안이 더 좋아도 hull 계산에 넣지 않는다
        self.assertNotIn("hull", doc["muni"])


class MergeTest(unittest.TestCase):
    def test_same_sha_merged_with_floors(self):
        rows = [pt("x1", 0.3, 0.5, sha="s1", floor="none"), pt("x2", 0.3, 0.5, sha="s1", floor="0.50"),
                pt("x3", 0.2, 0.6, sha="s2", floor="0.58")]
        out = PA.merge_same_plan(rows)
        self.assertEqual([p["floors"] for p in out], [["none", "0.50"], ["0.58"]])
        self.assertEqual([len(p["floor_check"]) for p in out], [2, 1])

    def test_other_algo_same_sha_kept_apart(self):
        rows = [pt("x1", 0.3, 0.5, sha="s1", algo="a"), pt("x2", 0.3, 0.5, sha="s1", algo="b")]
        self.assertEqual(len(PA.merge_same_plan(rows)), 2)


if __name__ == "__main__":
    unittest.main()


class FixtureSchemaTest(unittest.TestCase):
    """tests/fixtures/pipeline 예시(구로 스모크를 줄인 것)가 docs/design/pipeline.md 5절 형식을 따른다."""

    def test_pareto_fixture_keys(self):
        import json
        from pathlib import Path
        d = json.loads((Path(__file__).resolve().parents[1] / "fixtures/pipeline/pareto_guro_smoke.json")
                       .read_text(encoding="utf-8"))
        for k in ("gu", "fleet", "strike", "eval_days", "demand_total", "plans", "muni"):
            self.assertIn(k, d)
        for p in d["plans"]:
            self.assertTrue(set(PA.PLAN_KEYS) <= set(p), set(PA.PLAN_KEYS) - set(p))
            self.assertIn(p["base"], ("sector", "feeder", "oneway_loop"))
            self.assertEqual(set(p["reasons"]), {"noconn", "order", "wait", "refused"})

    def test_export_fixture_keys(self):
        import json
        from pathlib import Path
        e = json.loads((Path(__file__).resolve().parents[1] / "fixtures/pipeline/export_guro_smoke.json")
                       .read_text(encoding="utf-8"))
        r = e["routes"][0]
        self.assertTrue({"route_id", "name", "vehicles", "cycle_min", "headway_min", "stops", "shape"} <= set(r))
        self.assertTrue({"seq", "stop_id", "name", "lat", "lon", "kind"} <= set(r["stops"][0]))
        self.assertEqual(len(r["shape"][0]), 2)
