"""결정론 테스트(simulator.md §0.2, §9.9)와 속도 측정(§7.4). 비교 대상은 M07 폴더의 모든 파일이다."""

import ast
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from busstrike.sim import (
    compute_metrics, normalize_plan, passenger_outcomes, prepare, run, service_schedule, simulate,
)
from busstrike.sim.eventlog import dumps_events

from tests.sim import cases

ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = ROOT / "src" / "busstrike" / "sim"
M07_FILES = ["run/events.jsonl", "run/measurement.json", "run/metrics.json", "run/passenger_outcomes.csv",
             "run/realization.jsonl", "run/timetable.json", "scenario/demand_bins.csv", "scenario/network.json",
             "scenario/scenario_manifest.json"]
FORBIDDEN_IMPORTS = {"random", "time", "datetime", "uuid", "secrets"}


def folder_bytes(out: Path) -> dict[str, bytes]:
    """출력 폴더의 모든 파일(상대 경로 → 바이트). 검증기 보고서는 엔진 출력이 아니라 넣지 않는다."""
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def run_files(case, d: Path) -> dict[str, bytes]:
    paths = case.write(d / "in")
    run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], d / "out")
    return folder_bytes(d / "out")


class SameInputSameBytesTests(unittest.TestCase):
    def test_two_runs_byte_identical(self):
        for name, factory in (("t1", cases.t1), ("t2b", lambda: cases.t2(999)), ("t3", cases.t3),
                              ("synthetic", lambda: cases.synthetic(11))):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as d:
                first = run_files(factory(), Path(d) / "a")
                second = run_files(factory(), Path(d) / "b")
                self.assertEqual(sorted(first), M07_FILES)
                self.assertEqual(first, second)
                self.assertGreater(len(first["run/events.jsonl"]), 0)

    def test_hash_seed_does_not_matter(self):
        with tempfile.TemporaryDirectory() as d:
            paths = cases.synthetic(5).write(Path(d) / "in")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
            outs = []
            for seed in ("0", "12345"):
                out = Path(d) / f"out-{seed}"
                env["PYTHONHASHSEED"] = seed
                proc = subprocess.run(
                    [sys.executable, "-m", "busstrike.sim", "run", "--scenario", str(paths["scenario.json"]),
                     "--run-inputs", str(paths["run_inputs.json"]), "--plan", str(paths["plan.json"]),
                     "--out", str(out)],
                    env=env, capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                outs.append(folder_bytes(out))
            self.assertEqual(outs[0], outs[1])

    def test_input_order_does_not_matter(self):
        case = cases.synthetic(7)
        sc, tt, plan, pas = case.objects()

        def log(plan, vehicles, passengers):
            timetable = normalize_plan(plan, vehicles, sc.window, sc.policy, tt)
            ctx = prepare(passengers, vehicles, sc.window, sc.policy, tt)
            return dumps_events(simulate(ctx, service_schedule(timetable, ctx)).events)

        base = log(plan, list(sc.vehicles), pas)
        rng = random.Random(99)
        for _ in range(3):
            routes, vehicles, passengers = list(plan.routes), list(sc.vehicles), list(pas)
            rng.shuffle(routes)
            rng.shuffle(vehicles)
            rng.shuffle(passengers)
            self.assertEqual(log(replace(plan, routes=tuple(routes)), vehicles, passengers), base)

    def test_no_randomness_or_clock_in_simulator(self):
        offenders = []
        for path in sorted(SIM_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]]
                    if node.module == "os" and any(a.name == "urandom" for a in node.names):
                        names.append("os.urandom")
                elif isinstance(node, ast.Attribute) and node.attr == "urandom":
                    names = ["os.urandom"]
                else:
                    continue
                for name in names:
                    if name in FORBIDDEN_IMPORTS or name == "os.urandom":
                        offenders.append(f"{path.name}:{node.lineno} {name}")
        self.assertEqual(offenders, [])


class SpeedMeasurementTests(unittest.TestCase):
    """§7.4: 합성 5만 명·30노선·60대. 목표(2초)는 추정이라 실패 기준이 아니라 측정만 하고, 이상 징후만 막는다."""

    def test_50k_passengers(self):
        case = cases.synthetic(2026, n_routes=30, n_pax=50_000, vehicles_per_route=2)
        sc, tt, plan, pas = case.objects()
        t0 = time.perf_counter()
        timetable = normalize_plan(plan, sc.vehicles, sc.window, sc.policy, tt)
        ctx = prepare(pas, sc.vehicles, sc.window, sc.policy, tt)
        schedule = service_schedule(timetable, ctx)
        t1 = time.perf_counter()
        result = simulate(ctx, schedule)
        events = result.events
        t2 = time.perf_counter()
        data = dumps_events(events)
        metrics = compute_metrics(result, passenger_outcomes(result, schedule), schedule, stop_area={},
                                  wait_threshold_s=None, max_headway_s=None)
        t3 = time.perf_counter()
        with tempfile.TemporaryDirectory() as d:
            paths = case.write(Path(d) / "in")
            t4 = time.perf_counter()
            run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], Path(d) / "out")
            t5 = time.perf_counter()
        served = metrics["M1"]["served"]
        sys.stderr.write(
            f"\n[speed] passengers={len(pas)} vehicles={len(sc.vehicles)} trips={len(timetable.trips)} "
            f"events={len(events)} log_bytes={len(data)} served={served} "
            f"| prepare+timetable={t1 - t0:.2f}s simulate={t2 - t1:.2f}s log+metrics={t3 - t2:.2f}s "
            f"| run()={t5 - t4:.2f}s python={sys.version.split()[0]}\n")
        self.assertEqual(metrics["M1"]["total"], 50_000)
        self.assertGreater(served, 0)
        self.assertLess(t5 - t4, 120.0)


if __name__ == "__main__":
    unittest.main()
