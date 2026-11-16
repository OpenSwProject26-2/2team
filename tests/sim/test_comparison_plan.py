"""비교 입력 plan.json(experiments/plans/schema.md) → M05 운행안 변환(adapters/comparison_plan.py)."""

from __future__ import annotations

import contextlib
import io as _io
import json
import tempfile
import unittest
from pathlib import Path

from busstrike.evaluation.verify import main as verify_main
from busstrike.sim import SimError, TravelTimeTable, io, run
from busstrike.sim.adapters.comparison_plan import check_plan, main, to_engine_plan

from tests.sim import cases


def comparison(routes, **over) -> dict:
    d = {"plan_id": "plan-01", "revision": 1, "network_snapshot_id": "synthetic-net-1", "routes": routes}
    d.update(over)
    return d


def r(route_id="R1", stops=("A", "B", "S"), vehicles=1) -> dict:
    return {"route_id": route_id, "stops": list(stops), "vehicles": vehicles}


def t1_objects():
    case = cases.t1()
    return case, io.scenario_from_dict(case.scenario), TravelTimeTable.from_dict(case.travel_times)


def failed(report) -> list[str]:
    return sorted({f["check"] for f in report["failures"]})


class ConversionTests(unittest.TestCase):
    def test_t1_route_converts_to_r1_legs(self):
        _, sc, tt = t1_objects()
        plan, report = to_engine_plan(comparison([r()]), tt, sc)
        self.assertTrue(report["ok"])
        self.assertEqual(plan["routes"], [cases.r1()])
        self.assertEqual((plan["trips"], plan["vehicle_duties"], plan["scenario_id"]), ([], [], "sc-synthetic"))
        self.assertEqual(report["routes"], [{"route_id": "R1", "return_stops": ["S", "B", "A"],
                                             "return_distance_m": 2500}])

    def test_converted_plan_reproduces_t1_and_passes_m07(self):
        case, sc, tt = t1_objects()
        case.plan, _ = to_engine_plan(comparison([r()]), tt, sc)
        with tempfile.TemporaryDirectory() as d:
            paths = case.write(Path(d) / "in")
            out = Path(d) / "out"
            result = run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], out)
            log = (out / "run" / "events.jsonl").read_bytes()
            code = verify_main([str(out / "run"), "--out", str(Path(d) / "report.json"), "--quiet"])
        self.assertEqual(result["timetable_origin"], "generated")
        self.assertEqual(log, cases.t1_expected_log())
        self.assertEqual(code, 0)

    def test_return_path_is_shortest_then_lexicographic(self):
        segs = {("A", "B"): (100, [[0, 50]]), ("B", "A"): (500, [[0, 300]]),
                ("B", "X"): (100, [[0, 100]]), ("X", "A"): (100, [[0, 100]]),
                ("B", "Y"): (100, [[0, 100]]), ("Y", "A"): (100, [[0, 100]])}
        tt = TravelTimeTable.from_dict(cases.travel_times_dict(segs))
        sc = io.scenario_from_dict(cases.scenario_dict((0, 1000), [cases.vehicle_dict("V1", 3, 1000)], 600, 1200))
        report = check_plan(comparison([r(stops=("A", "B"))]), tt, sc)
        self.assertEqual(report["routes"][0]["return_stops"], ["B", "X", "A"])  # 200초 < 300초, X < Y

    def test_same_input_same_bytes(self):
        _, sc, tt = t1_objects()
        a = io.json_bytes(to_engine_plan(comparison([r()]), tt, sc)[0])
        b = io.json_bytes(to_engine_plan(comparison([r()]), tt, sc)[0])
        self.assertEqual(a, b)


class CheckTests(unittest.TestCase):
    def setUp(self):
        _, self.sc, self.tt = t1_objects()
        self.two = io.scenario_from_dict(cases.scenario_dict(
            (0, 1500), [cases.vehicle_dict("V1", 3, 1500), cases.vehicle_dict("V2", 3, 1500)], 600, 1200))

    def check(self, plan, scenario=None):
        return check_plan(plan, self.tt, scenario or self.sc)

    def test_s1_unknown_stop(self):
        self.assertEqual(failed(self.check(comparison([r(stops=("A", "B", "Z"))]))), ["S1", "S4"])

    def test_s2_duplicate_stop(self):
        self.assertIn("S2", failed(self.check(comparison([r(stops=("A", "B", "A"))]))))

    def test_s3_too_short(self):
        self.assertEqual(failed(self.check(comparison([r(stops=("A",))]))), ["S3"])

    def test_s4_missing_segment(self):
        self.assertEqual(failed(self.check(comparison([r(stops=("A", "S"))]))), ["S4"])

    def test_s5_vehicle_count(self):
        self.assertEqual(failed(self.check(comparison([r(vehicles=0)]))), ["S5"])
        self.assertEqual(failed(self.check(comparison([r(vehicles=True)]))), ["S5"])
        self.assertEqual(failed(self.check(comparison([r(vehicles=1)]), self.two)), ["S5"])  # C2: 합 1 ≠ 2

    def test_s6_duplicate_route_id(self):
        report = self.check(comparison([r(), r()]), self.two)
        self.assertEqual(failed(report), ["S6"])

    def test_origin_and_notes_are_rejected(self):
        for key in ("origin", "notes", "scenario_id"):
            with self.subTest(key=key):
                self.assertEqual(failed(self.check(comparison([r()], **{key: "x"}))), ["FORMAT"])
        route = dict(r(), comment="x")
        self.assertEqual(failed(self.check(comparison([route]))), ["FORMAT"])

    def test_c6_network_snapshot(self):
        self.assertEqual(failed(self.check(comparison([r()], network_snapshot_id="other"))), ["C6"])

    def test_no_return_path(self):
        oneway = TravelTimeTable.from_dict(cases.travel_times_dict(
            {k: v for k, v in cases.F1_SEGMENTS.items() if k in {("A", "B"), ("B", "S")}}))
        report = check_plan(comparison([r()]), oneway, self.sc)
        self.assertEqual(failed(report), ["RETURN"])
        self.assertEqual(report["checks"]["S4"], "pass")
        with self.assertRaises(SimError) as cm:
            to_engine_plan(comparison([r()]), oneway, self.sc)
        self.assertEqual(cm.exception.code, SimError.PLAN_INFEASIBLE)


class CliTests(unittest.TestCase):
    def run_cli(self, plan: dict):
        case = cases.t1()
        with tempfile.TemporaryDirectory() as d:
            paths = case.write(Path(d) / "in")
            src = Path(d) / "comparison.json"
            src.write_text(json.dumps(plan), encoding="utf-8")
            out = Path(d) / "engine" / "plan.json"
            buf = _io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(_io.StringIO()):
                code = main(["--plan", str(src), "--scenario", str(paths["scenario.json"]),
                             "--travel-times", str(paths["travel_times.json"]), "--out", str(out)])
            written = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return code, buf.getvalue(), written

    def test_exit_codes(self):
        code, stdout, written = self.run_cli(comparison([r()]))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["ok"])
        self.assertEqual(written["routes"], [cases.r1()])

        code, stdout, written = self.run_cli(comparison([r(stops=("A", "S"))]))
        self.assertEqual((code, written), (1, None))
        self.assertEqual(json.loads(stdout)["checks"]["S4"], "fail")

        code, _, written = self.run_cli([])  # 객체가 아님 → 검사 실패(FORMAT)
        self.assertEqual((code, written), (1, None))


if __name__ == "__main__":
    unittest.main()
