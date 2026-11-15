"""T7 — 노선+대수만 있는 지자체안 재현과 공정성(simulator.md §1.6, §9.8)."""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from busstrike.sim import run

from tests.sim import cases

SIM_DIR = Path(__file__).resolve().parents[2] / "src" / "busstrike" / "sim"


def run_plan(case, plan, d: Path):
    case.plan = plan
    paths = case.write(d / "in")
    out = d / "out"
    result = run(paths["scenario.json"], paths["run_inputs.json"], paths["plan.json"], out)
    return result, {name: (out / name).read_bytes() for name in ("timetable.json", "events.jsonl", "metrics.json")}


class T7FairnessTests(unittest.TestCase):
    def test_routes_only_plan_reproduces_t1(self):
        with tempfile.TemporaryDirectory() as d:
            case = cases.t1()
            plan = cases.plan_dict([cases.r1(vehicle_count=1)], trips=[], vehicle_duties=[])
            result, files = run_plan(case, plan, Path(d))
        self.assertEqual(result["timetable_origin"], "generated")
        self.assertEqual(files["events.jsonl"], cases.t1_expected_log())

    def test_source_does_not_change_outputs(self):
        outputs = {}
        for source in ("municipal", "general_ai", "optimizer"):
            with tempfile.TemporaryDirectory() as d:
                _, files = run_plan(cases.t1(), cases.plan_dict([cases.r1()], source=source), Path(d))
            outputs[source] = files
        base = outputs["municipal"]
        for source, files in outputs.items():
            with self.subTest(source=source):
                self.assertEqual(files, base)

    def test_provided_trips_give_same_log(self):
        with tempfile.TemporaryDirectory() as d:
            _, generated = run_plan(cases.t1(), cases.plan_dict([cases.r1()]), Path(d) / "gen")
            tt = json.loads(generated["timetable.json"])
            plan = cases.plan_dict([cases.r1()], trips=tt["trips"], vehicle_duties=tt["duties"])
            result, provided = run_plan(cases.t1(), plan, Path(d) / "prov")
        self.assertEqual(result["timetable_origin"], "provided")
        self.assertEqual(provided["events.jsonl"], generated["events.jsonl"])
        ptt = json.loads(provided["timetable.json"])
        self.assertEqual((ptt["trips"], ptt["duties"]), (tt["trips"], tt["duties"]))

    def test_sim_sources_never_read_plan_source(self):
        offenders = []
        for path in sorted(SIM_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "source":
                    offenders.append(f"{path.name}:{node.lineno} .source")
                if isinstance(node, ast.Constant) and node.value == "source":
                    offenders.append(f"{path.name}:{node.lineno} 'source'")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
