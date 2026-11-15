"""독립 검증기: 손계산 시나리오·합성 실행 통과, 리포트 형식, CLI(simulator.md §8, §9.9, §9.10)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from busstrike.evaluation.validator import VALIDATOR_VERSION, failed_checks, validate_run, write_report
from busstrike.evaluation._inputs import ValidatorInputError

from tests.evaluation.runs import make_run
from tests.sim import cases

ROOT = Path(__file__).resolve().parents[2]
CHECK_IDS = [f"V{i:02d}" for i in range(1, 16)]


def provided_t1() -> cases.Case:
    """T7-3: T1에서 생성된 trips를 plan.json에 그대로 넣은 안(origin="provided")."""
    with tempfile.TemporaryDirectory() as d:
        tt = make_run(cases.t1(), Path(d)).read_json("timetable.json")
    case = cases.t1()
    case.plan = dict(case.plan, trips=tt["trips"], vehicle_duties=tt["duties"])
    return case


HAND_CASES = {
    "T1": cases.t1,
    "T2": cases.t2,
    "T2b": lambda: cases.t2(1370),
    "T3": cases.t3,
    "T3-split-dest": lambda: cases.t3(("S1",), ("S2",)),
    "T4": cases.t4,
    "T7-provided": provided_t1,
}


class HandCalculatedRunsVerifyTests(unittest.TestCase):
    def test_hand_calculated_scenarios_pass_every_check(self):
        for name, factory in HAND_CASES.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as d:
                report = validate_run(make_run(factory(), Path(d)).d)
                self.assertEqual([c["id"] for c in report["checks"]], CHECK_IDS)
                self.assertEqual({c["id"]: c["status"] for c in report["checks"]}, dict.fromkeys(CHECK_IDS, "pass"))
                self.assertEqual(report["violations"], [])
                self.assertTrue(report["verified"])
                self.assertFalse(report["truncated"])

    def test_t1_expected_log_fixture_verifies(self):
        """§9.2에 그대로 적힌 기대 로그(fixture)를 검증기에 직접 넣는다."""
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t1(), Path(d))
            (rd.d / "events.jsonl").write_bytes(cases.t1_expected_log())
            report = validate_run(rd.d)
            self.assertTrue(report["verified"], report["violations"])
            self.assertEqual((report["plan_id"], report["plan_revision"], report["input_bundle_id"]),
                             ("plan-1", 1, "bundle-1"))
            self.assertEqual(report["run_id"], rd.read_json("run.json")["run_id"])
            self.assertEqual(report["validator_version"], VALIDATOR_VERSION)

    def test_t2b_cancel_is_checked(self):
        """T2b의 cancel은 §3.4 조건이 성립해서 나온 것이다. 조건이 성립하지 않는 T2에 cancel을 넣으면 실패한다."""
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t2(), Path(d))

            def add_cancel(es):
                es[:] = [e for e in es if not (e.get("vehicle_id") == "V1" and e.get("visit", 0) >= 6)]
                es.append({"t": 686, "type": "cancel", "vehicle_id": "V1", "trip_id": "R1-V1-001-0", "visit": 6,
                           "cause": "LATE"})
            rd.edit_events(add_cancel)
            report = validate_run(rd.d)
            self.assertIn("V09", failed_checks(report))
            self.assertTrue(any("does not hold" in v["message"] for v in report["violations"]))


    def test_cancel_time_is_not_an_operating_window_event(self):
        """cancel은 이동이 아니라 판단 기록이다(t=ready). W_e 뒤 시각이어도 V10이 아니라 V09가 본다."""
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t2(1370), Path(d))

            def late_cancel(es):
                next(e for e in es if e["type"] == "cancel")["t"] = 1400
            rd.edit_events(late_cancel)
            self.assertEqual(failed_checks(validate_run(rd.d)), ["V09"])


class SyntheticRunsVerifyTests(unittest.TestCase):
    def test_random_synthetic_runs_verify(self):
        """§9.9: 고정 seed로 만든 무작위 합성 입력 수십 개로 엔진을 돌리고 검증기를 통과시킨다."""
        for s in range(30):
            kw = dict(n_routes=3 + s % 6, n_pax=150 + 40 * (s % 7), vehicles_per_route=1 + s % 3,
                      max_wait_s=(600, 900, 1200, 1800)[s % 4])
            with self.subTest(seed=s, **kw), tempfile.TemporaryDirectory() as d:
                report = validate_run(make_run(cases.synthetic(100 + s, **kw), Path(d)).d)
                self.assertEqual(failed_checks(report), [], report["violations"][:5])


class ReportFormatTests(unittest.TestCase):
    def test_report_is_deterministic_and_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t2(1370), Path(d))
            write_report(validate_run(rd.d), rd.d / "a.json")
            write_report(validate_run(rd.d), rd.d / "b.json")
            a = (rd.d / "a.json").read_bytes()
            self.assertEqual(a, (rd.d / "b.json").read_bytes())
            doc = json.loads(a)
            self.assertEqual(set(doc), {"validator_version", "run_id", "plan_id", "plan_revision", "input_bundle_id",
                                        "verified", "checks", "violations", "truncated"})
            self.assertTrue(a.endswith(b"\n") and a.count(b"\n") == 1)

    def test_violation_cap_and_order(self):
        """검사당 100건만 싣고 건수는 checks[].violations에 전부. 순서는 (check_id, seq, passenger_id, vehicle_id)."""
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.synthetic(3), Path(d))

            def shift_loads(es):
                for e in es:
                    if e["type"] in ("arrive", "depart"):
                        e["load"] += 1
            rd.edit_events(shift_loads)
            report = validate_run(rd.d)
            v03 = next(c for c in report["checks"] if c["id"] == "V03")
            listed = [v for v in report["violations"] if v["check_id"] == "V03"]
            self.assertGreater(v03["violations"], 100)
            self.assertEqual(len(listed), 100)
            self.assertTrue(report["truncated"])
            keys = [(v["check_id"], v.get("seq", -1), v.get("passenger_id", ""), v.get("vehicle_id", ""))
                    for v in report["violations"]]
            self.assertEqual(keys, sorted(keys))
            allowed = {"check_id", "message", "seq", "t", "vehicle_id", "trip_id", "stop_id", "passenger_id",
                       "expected", "actual"}
            self.assertTrue(all(set(v) <= allowed for v in report["violations"]))

    def test_v01_failure_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t1(), Path(d))
            p = rd.d / "events.jsonl"
            p.write_bytes(p.read_bytes().replace(b'"type":"depart"', b'"type":"leave"', 1))
            report = validate_run(rd.d)
            self.assertEqual([c["status"] for c in report["checks"]], ["fail"] + ["skipped"] * 14)
            self.assertFalse(report["verified"])
            self.assertEqual(failed_checks(report), CHECK_IDS)

    def test_non_canonical_line_fails_v01(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t1(), Path(d))
            p = rd.d / "events.jsonl"
            p.write_bytes(p.read_bytes().replace(b'{"seq":0,"t":0,', b'{"seq":0, "t":0,', 1))
            report = validate_run(rd.d)
            self.assertEqual(failed_checks(report)[0], "V01")
            self.assertIn("canonical", report["violations"][0]["message"])

    def test_inputs_in_another_directory(self):
        """sim의 run()처럼 입력과 출력 디렉터리가 다를 때는 경로를 따로 준다."""
        with tempfile.TemporaryDirectory() as d:
            src = make_run(cases.t3(), Path(d) / "all").d
            out = Path(d) / "out"
            out.mkdir()
            for name in ("run.json", "timetable.json", "events.jsonl", "metrics.json"):
                shutil.move(str(src / name), out / name)
            with self.assertRaises(ValidatorInputError):
                validate_run(out)
            report = validate_run(out, scenario_path=src / "scenario.json", run_inputs_path=src / "run_inputs.json",
                                  plan_path=src / "plan.json")
            self.assertTrue(report["verified"])


class CliTests(unittest.TestCase):
    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        return subprocess.run([sys.executable, "-m", "busstrike.evaluation.validator", *args],
                              env=env, capture_output=True, text=True, check=False)

    def test_exit_codes_and_report_file(self):
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(cases.t1(), Path(d))
            ok = self._cli("--run-dir", str(rd.d))
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stderr, "")
            self.assertTrue(json.loads((rd.d / "validation.json").read_text(encoding="utf-8"))["verified"])

            m = rd.read_json("metrics.json")
            m["passengers"]["generated"] += 1
            rd.write_json("metrics.json", m)
            bad = self._cli("--run-dir", str(rd.d), "--out", str(rd.d / "v2.json"))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("V13", bad.stderr)
            self.assertFalse(json.loads((rd.d / "v2.json").read_text(encoding="utf-8"))["verified"])

            (rd.d / "run.json").unlink()
            self.assertEqual(self._cli("--run-dir", str(rd.d)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
