"""검증기 독립성(simulator.md §8.1, §10): `src/busstrike/evaluation/`은 `busstrike.sim`을 import하지 않는다."""

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "src" / "busstrike" / "evaluation"


def imported_modules(source: str) -> list[str]:
    """소스가 import하는 모듈 이름(상대 import는 `busstrike.evaluation` 기준 절대 이름으로 푼다)."""
    tree = ast.parse(source)
    package = ["busstrike", "evaluation"]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)] if node.level - 1 <= len(package) else []
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            out.append(prefix)
            out += [f"{prefix}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
                "import_module", "__import__"):
            out += [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return out


def is_sim(name: str) -> bool:
    return name == "busstrike.sim" or name.startswith("busstrike.sim.") or name == "sim" or name.startswith("sim.")


class ValidatorIndependenceTests(unittest.TestCase):
    def test_evaluation_sources_exist(self):
        names = {p.name for p in EVAL_DIR.glob("*.py")}
        self.assertTrue({"__init__.py", "validator.py", "_inputs.py", "_checks.py"} <= names)

    def test_no_sim_import_in_evaluation_package(self):
        for path in sorted(EVAL_DIR.rglob("*.py")):
            with self.subTest(file=path.name):
                bad = [m for m in imported_modules(path.read_text(encoding="utf-8")) if is_sim(m)]
                self.assertEqual(bad, [], f"{path.name} imports {bad}")

    def test_ast_scan_catches_sim_imports(self):
        """검사 자체가 sim import를 놓치지 않는지(절대·상대·importlib)."""
        samples = ["import busstrike.sim", "from busstrike.sim import run", "from busstrike import sim",
                   "from ..sim import engine", "from .. import sim", "import importlib\nimportlib.import_module('busstrike.sim')"]
        for src in samples:
            with self.subTest(source=src):
                self.assertTrue(any(is_sim(m) for m in imported_modules(src)), src)
        self.assertFalse(any(is_sim(m) for m in imported_modules("import json\nfrom ._inputs import RunFiles")))

    def test_running_validator_does_not_load_sim(self):
        code = ("import sys, busstrike.evaluation.validator as v, busstrike.evaluation._checks, "
                "busstrike.evaluation._inputs; print(sorted(m for m in sys.modules if m.startswith('busstrike.sim')))")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(proc.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
