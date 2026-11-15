"""독립성 정적 검사 (명세 §1 코드 분리, 수락 조건 §7).

`src/busstrike/evaluation/` 아래 어떤 파일도 M05 엔진(`sim`·`busstrike.sim`)과
M04 운행안(`opt`·`busstrike.planning`)을 import하지 않는다. 엔진과 같은 코드를 쓰면
같은 버그를 그대로 통과시키기 때문이다.

fixture 생성 스크립트(`tests/evaluation/fixtures/make_fixtures.py`)는 `sim`을 import한다.
그것은 검증기 패키지 밖에 있고 검증기가 import하지 않으므로 이 검사 대상이 아니다.
"""
import ast
from pathlib import Path
import subprocess
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[2] / 'src/busstrike/evaluation'
# 금지: 최상위 패키지 이름과 완전 이름 둘 다 막는다.
FORBIDDEN_ROOTS = {'sim', 'opt'}
FORBIDDEN_NAMES = {'busstrike.sim', 'busstrike.planning'}
# 동적 import로 우회하는 것도 막는다.
DYNAMIC = {'__import__', 'import_module'}


def python_files():
    return sorted(PACKAGE.rglob('*.py'))


def forbidden(name):
    """import 대상 모듈 이름이 금지 대상인지."""
    if not name:
        return None
    root = name.split('.')[0]
    if root in FORBIDDEN_ROOTS:
        return root
    for full in FORBIDDEN_NAMES:
        if name == full or name.startswith(full + '.'):
            return full
    return None


def imported_names(tree, module_package):
    """파일 하나가 import하는 절대 모듈 이름 목록. 상대 import도 풀어 쓴다."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level 1 = busstrike.evaluation, level 2 = busstrike, ...
                base = module_package.split('.')
                base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = '.'.join(base)
                target = f'{prefix}.{node.module}' if node.module else prefix
                names.append(target)
                names.extend(f'{target}.{alias.name}' for alias in node.names)
            else:
                names.append(node.module)
                names.extend(f'{node.module}.{alias.name}' for alias in node.names)
    return names


class ImportIndependenceTests(unittest.TestCase):
    def test_package_exists_and_has_files(self):
        self.assertTrue(PACKAGE.is_dir(), f'검증기 패키지가 없다: {PACKAGE}')
        self.assertTrue(python_files(), '검증기 패키지에 .py 파일이 없다')

    def test_no_engine_or_planning_imports(self):
        offenders = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for name in imported_names(tree, 'busstrike.evaluation'):
                hit = forbidden(name)
                if hit:
                    offenders.append(f'{path.name}: import {name} (금지: {hit})')
        self.assertEqual([], offenders,
                         'evaluation 패키지가 엔진·운행안 코드를 import한다:\n' + '\n'.join(offenders))

    def test_no_dynamic_imports(self):
        offenders = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, 'id', None) or getattr(func, 'attr', None)
                if name in DYNAMIC:
                    offenders.append(f'{path.name}:{node.lineno}: {name}() 사용')
        self.assertEqual([], offenders,
                         '동적 import로 독립성을 우회할 수 있다:\n' + '\n'.join(offenders))

    def test_importing_verifier_does_not_load_engine(self):
        """검증기를 import해도 엔진·운행안 모듈이 적재되지 않는다.

        같은 인터프리터에서 재면 함께 수집된 다른 모듈 테스트(tests/planning)가 이미
        busstrike.planning을 적재해 둔 상태라 의미가 없다. 그래서 깨끗한 하위
        프로세스에서 검사한다.
        """
        probe = (
            'import sys\n'
            'import busstrike.evaluation.inputs, busstrike.evaluation.invariants\n'
            'import busstrike.evaluation.checks\n'
            f'roots = {sorted(FORBIDDEN_ROOTS)!r}\n'
            f'names = {sorted(FORBIDDEN_NAMES)!r}\n'
            'bad = sorted(n for n in sys.modules '
            'if n.split(".")[0] in roots or any(n == f or n.startswith(f + ".") for f in names))\n'
            'print(",".join(bad))\n')
        done = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True,
                              cwd=PACKAGE.parents[2])
        self.assertEqual(0, done.returncode,
                         f'검증기만 import하는 데 실패했다:\n{done.stderr}')
        loaded = [n for n in done.stdout.strip().split(',') if n]
        self.assertEqual([], loaded, f'검증기 import가 엔진 모듈을 적재했다: {loaded}')


if __name__ == '__main__':
    unittest.main()
