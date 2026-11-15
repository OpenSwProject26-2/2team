"""verifier_report.json 작성 (명세 §7).

하나라도 실패하면 전체 `pass=false`다. 부분 통과를 "검증됨"으로 적지 않는다.

`pass`와 `fully_verified`를 따로 둔다. V16은 R10(가용 fleet)·R13(방향별 구간) 자료가
없어 영구히 `not_verified`이므로, `pass=true`만 보고 "전부 검증했다"고 읽으면 안 된다.
"""
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
import subprocess
import sys

from .checks import EXAMPLE_LIMIT, FAIL, NOT_RUN, NOT_VERIFIED, PASS

SCHEMA = 'busstrike.evaluation.verifier_report/v1'
SPEC_DOCUMENT = 'docs/verifier_spec.md'
LABEL = '독립 검증기 재계산 결과. 실제 운행 승인·현장 성능 검증이 아니다.'
CAVEATS = (
    '통과해도 실제 운행 승인이나 현장 성능 검증이 아니다. independently_verified 부여는 '
    'M07 절차가 정한다 (명세 §7).',
    '합성 입력만 사용했다. 원시 교통카드 자료를 읽지 않았다.',
    'V16은 R10 가용 fleet 자료가 산출물에 없고 R13은 명세가 미검증으로 정했다. 위반이 '
    '없어도 통과가 아니다.',
    'F3(동시각·경쟁) 손계산 표가 명세에 비어 있어 그 계열은 검증하지 않았다 (명세 §8).',
)


def _git(repo, *args):
    try:
        done = subprocess.run(['git', *args], capture_output=True, text=True, check=True,
                              cwd=repo)
        return done.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def verifier_provenance():
    """검증기 커밋·작성자·작업 트리 상태 (명세 §7 "검증기 커밋·작성자")."""
    repo = Path(__file__).resolve().parents[3]
    status = _git(repo, 'status', '--porcelain')
    return {'commit': _git(repo, 'rev-parse', 'HEAD'),
            'branch': _git(repo, 'rev-parse', '--abbrev-ref', 'HEAD'),
            'author': _git(repo, 'log', '-1', '--format=%an'),
            'dirty': None if status is None else bool(status),
            'python': sys.version.split()[0], 'platform': platform.platform(),
            'spec_document': SPEC_DOCUMENT}


def engine_provenance(run):
    """엔진 커밋. measurement.json의 env 기록을 인용한다(검증기가 알 수 없는 값이다)."""
    env = run.measurement.get('env')
    if not isinstance(env, dict):
        return {'commit': None,
                'note': 'measurement.json에 env가 없어 엔진 커밋을 확인하지 못했다.'}
    return {'commit': env.get('commit'), 'branch': env.get('branch'),
            'dirty': env.get('dirty'), 'dirty_tracked': env.get('dirty_tracked'),
            'dirty_at_end': env.get('dirty_at_end'), 'python': env.get('python'),
            'platform': env.get('platform'), 'simpy': env.get('simpy'),
            'source': 'measurement.json env (엔진이 기록한 값. 검증기가 확인한 값이 아니다)'}


@dataclass
class Report:
    payload: dict

    @property
    def passed(self):
        return self.payload['pass']

    def write(self, path):
        text = json.dumps(self.payload, indent=2, sort_keys=True, ensure_ascii=False) + '\n'
        if str(path) == '-':
            sys.stdout.write(text)
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def summary_lines(self):
        """사람이 읽는 요약. 통과하지 않은 것을 통과로 보이지 않게 적는다."""
        counts = self.payload['summary']
        lines = [f"{'검사':<5}{'상태':<14}{'검사 대상':>12}  {'위반':>6}  단위"]
        lines.append('-' * 60)
        for check in self.payload['checks']:
            lines.append(f"{check['check_id']:<5}{check['status']:<14}"
                         f"{check['examined']:>12,}  {check['violation_count']:>6}  "
                         f"{check['unit']}")
        lines.append('-' * 60)
        lines.append(f"pass={self.payload['pass']}  fully_verified="
                     f"{self.payload['fully_verified']}  "
                     f"(통과 {counts[PASS]} / 실패 {counts[FAIL]} / "
                     f"미실행 {counts[NOT_RUN]} / 미검증 {counts[NOT_VERIFIED]})")
        if not self.payload['fully_verified'] and self.payload['pass']:
            lines.append('실패는 없으나 미실행·미검증 검사가 있다. "검증됨"으로 쓰지 말 것.')
        return lines


def build(scenario, run, results, *, recompute_limit=None, scan=None, generated_at=None):
    """CheckResult 목록으로 보고서를 만든다."""
    results = sorted(results, key=lambda r: int(r.check_id[1:]))
    counts = {PASS: 0, FAIL: 0, NOT_RUN: 0, NOT_VERIFIED: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    failed = [r.check_id for r in results if r.status == FAIL]
    payload = {
        'schema': SCHEMA,
        'label': LABEL,
        # 하나라도 실패하면 전체 pass=false (명세 §1 실패 처리)
        'pass': not failed,
        # 실패가 없어도 미실행·미검증이 있으면 "전부 검증"이 아니다
        'fully_verified': not failed and counts[NOT_RUN] == 0 and counts[NOT_VERIFIED] == 0,
        'failed_checks': failed,
        'not_verified_checks': [r.check_id for r in results if r.status == NOT_VERIFIED],
        'not_run_checks': [r.check_id for r in results if r.status == NOT_RUN],
        'summary': counts,
        'generated_at': (generated_at or datetime.now().astimezone()).isoformat(
            timespec='seconds'),
        'violation_example_limit': EXAMPLE_LIMIT,
        'run': {'scenario_dir': str(scenario.path), 'run_dir': str(run.path),
                'passengers': len(run.outcomes), 'events': run.measurement.get('event_count'),
                'trips': len(scenario.trips), 'stop_visits': len(scenario.visits),
                'demand_rows': len(scenario.bin_rows),
                'max_wait_s': run.max_wait_s, 'max_journey_s': run.max_journey_s,
                'drain_s': run.drain_s,
                'arrival_seed': run.measurement.get('arrival_seed'),
                'policy': (run.measurement.get('policy') or {}).get('name'),
                'has_realization_file': run.has_realization,
                'engine_label': run.measurement.get('label')},
        'inputs': {'scenario': scenario.file_digests, 'run': run.file_digests},
        'engine': engine_provenance(run),
        'verifier': verifier_provenance(),
        'settings': {'recompute_limit': recompute_limit, 'scan': scan},
        'checks': [r.as_dict() for r in results],
        'caveats': list(CAVEATS),
    }
    return Report(payload)
