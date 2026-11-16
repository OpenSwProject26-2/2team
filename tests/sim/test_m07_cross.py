"""교차 검증: giho999 M07 fixture F1·F2(변형 포함)의 scenario 입력으로 새 엔진을 돌린다.

정답 출처는 `tests/evaluation/fixtures/handcalc/*.json`(명세 §5를 사람이 옮긴 값)이다. 엔진 출력은
① 손계산과 승객 단위·지표 단위로 같아야 하고 ② M07 검증기를 통과해야 한다(pass=true, 종료 코드 0).
fixture의 엔진 산출물(예전 엔진)과도 결과 CSV·일정이 같은지 덧붙여 본다(정답이 아니라 참고).
"""

import csv
import io as _io
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from busstrike.evaluation.verify import main as verify_main
from busstrike.sim import run

from tests.sim import m07_fixture_cases as fc

HANDCALC = fc.FIXTURES / "handcalc"


def expected_value(value):
    """손계산 표의 [a, b]는 분수 a/b(2/9, 220/3 같은 값)."""
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return float(Fraction(*value))
    return value


def walk(payload, path):
    node = payload
    for key in path.split("."):
        node = node[key]
    return node


class Run:
    def __init__(self, name):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            paths = fc.case_of(name).write(d / "in")
            run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], d / "out")
            self.exit_code = verify_main([str(d / "out" / "run"), "--out", str(d / "report.json"), "--quiet"])
            self.report = json.loads((d / "report.json").read_text(encoding="utf-8"))
            self.csv_text = (d / "out" / "run" / "passenger_outcomes.csv").read_text(encoding="utf-8")
            self.metrics = json.loads((d / "out" / "run" / "metrics.json").read_text(encoding="utf-8"))
            self.network = json.loads((d / "out" / "scenario" / "network.json").read_text(encoding="utf-8"))
        num = lambda x: None if x == "" else int(x)
        self.rows = {}
        for r in csv.DictReader(_io.StringIO(self.csv_text)):
            self.rows[r["passenger_id"]] = {
                "origin": r["origin"], "destination": r["destination"], "time_s": int(r["time_s"]),
                "outcome": r["outcome"], "reason": r["reason"] or None, "physical": r["physical"],
                "boarding_s": num(r["boarding_s"]), "arrival_s": num(r["arrival_s"]), "refused": int(r["refused"]),
                "wait_s": None if r["boarding_s"] == "" else int(r["boarding_s"]) - int(r["time_s"])}
        self.vehicles = {v["vehicle_id"]: v for v in self.metrics["M6"]["vehicles"]}

    def value(self, path):
        if path.startswith("M6.vehicles."):
            vid, field = path.split(".")[2:]
            return self.vehicles[vid][field]
        return walk(self.metrics, path)


class CrossValidationMixin:
    def assert_expected(self, label, expected, actual, path=""):
        """손계산이 말한 항목만 비교한다(`_` 접두 = 명세 미기재·설명)."""
        if isinstance(expected, dict):
            for key, value in expected.items():
                if key.startswith("_"):
                    continue
                self.assert_expected(label, value, actual[key], f"{path}.{key}")
            return
        self.assertEqual(expected_value(expected), actual, f"{label} {path}")

    def assert_m07_pass(self, out, name):
        self.assertEqual(out.exit_code, 0, f"{name}: {out.report['failed_checks']}")
        self.assertTrue(out.report["pass"])
        self.assertEqual(out.report["not_run_checks"], [])

    def assert_same_as_fixture_outputs(self, out, name):
        """참고 대조: 예전 엔진 산출물과 결과 CSV 바이트·일정(trip)이 같다."""
        self.assertEqual(out.csv_text, (fc.FIXTURES / name / "run" / "passenger_outcomes.csv").read_text())
        theirs = json.loads((fc.FIXTURES / name / "scenario" / "network.json").read_text())
        key = lambda t: t["trip_id"]
        self.assertEqual(sorted(out.network["trips"], key=key), sorted(theirs["trips"], key=key))
        self.assertEqual(out.network["route_of"], theirs["route_of"])


class F1CrossTests(CrossValidationMixin, unittest.TestCase):
    spec = json.loads((HANDCALC / "f1_expected.json").read_text(encoding="utf-8"))

    def test_variants(self):
        self.assertEqual(sorted(self.spec["variants"]), sorted(fc.F1_NAMES))
        for name, variant in self.spec["variants"].items():
            with self.subTest(fixture=name):
                out = Run(name)
                self.assert_m07_pass(out, name)
                cfg = variant["config"]
                self.assertEqual(out.report["run"]["max_wait_s"], cfg["max_wait_s"])
                self.assertEqual(out.report["run"]["max_journey_s"], cfg["max_journey_s"])
                self.assertEqual(out.report["run"]["drain_s"], cfg["drain_s"])
                for pid, expected in variant["passengers"].items():
                    self.assert_expected(f"{name} {pid}", expected, out.rows[pid])
                for path, expected in variant["metrics"].items():
                    if not path.startswith("_"):
                        self.assert_expected(name, expected, out.value(path), path)
                self.assert_same_as_fixture_outputs(out, name)


class F2CrossTests(CrossValidationMixin, unittest.TestCase):
    spec = json.loads((HANDCALC / "f2_expected.json").read_text(encoding="utf-8"))

    def test_f2(self):
        out = Run("f2")
        self.assert_m07_pass(out, "f2")
        self.assertEqual(len(out.rows), len(self.spec["passengers"]))
        for pid, expected in self.spec["passengers"].items():
            self.assert_expected(f"f2 {pid}", expected, out.rows[pid])
        for path, expected in self.spec["metrics"].items():
            if path.startswith("_"):
                continue
            if path == "M6.vehicles":
                for vid, fields in expected.items():
                    self.assert_expected(f"f2 {vid}", fields, out.vehicles[vid])
                continue
            self.assert_expected("f2", expected, out.value(path), path)
        self.assertTrue(out.metrics["G"]["R12"]["pass"])
        self.assert_same_as_fixture_outputs(out, "f2")

    def test_f2_headway599(self):
        out = Run("f2-headway599")
        self.assert_m07_pass(out, "f2-headway599")
        want = self.spec["metrics"]["_R12_variants"]["max_headway_s=599"]
        r12 = out.metrics["G"]["R12"]
        self.assertEqual((r12["pass"], r12["violation_count"]), (want["pass"], want["violation_count"]))
        self.assertEqual(sorted(v["stop"] for v in r12["violations"]), want["stops"])
        self.assertEqual({v["route"] for v in r12["violations"]}, {want["route"]})
        self.assert_same_as_fixture_outputs(out, "f2-headway599")


if __name__ == "__main__":
    unittest.main()
