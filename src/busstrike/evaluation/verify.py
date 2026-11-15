"""M05 독립 검증기 진입점.

    python -m busstrike.evaluation.verify <실행 결과 폴더>

입력은 엔진 산출물 파일뿐이다. 엔진을 호출하지 않고 `sim`·`opt`를 import하지 않는다
(명세 §1). 하나라도 실패하면 보고서 `pass=false`이고 종료 코드는 1이다.

규모 상한: 명세 §4의 재계산 oracle은 N ≤ 10,000이다. 그보다 크면 oracle이 필요한
검사(V6·V7·V9–V14)를 `not_run`으로 남기고 불변조건·체크섬만 적용한다. 이때 결과를
"정확도"라고 쓰지 않는다.
"""
import argparse
from pathlib import Path
import sys

from . import arrivals_ref, inputs, report
from .checks import CheckResult, NOT_RUN
from .guards import run_guards
from .invariants import check_boarding, check_deadlines, run_invariants
from .metrics_ref import run_metrics
from .recompute import recompute

DEFAULT_RECOMPUTE_LIMIT = 10_000
# oracle이 있어야만 판정할 수 있는 검사
ORACLE_CHECKS = (('V6', 'FIFO·비유휴', '승차가 있는 방문'), ('V7', '기한', '승객'),
                 ('V9', 'M1 충족률', '승객'), ('V10', 'M2 미충족 사유', '미충족 승객'),
                 ('V11', 'M2 만차 거절', '승객'), ('V12', 'M3 대기시간', '탑승 승객'),
                 ('V13', 'M5 지역별 충족률', '지역'), ('V14', 'M6 차량별', '차량'))

SKIP_NOTE = (
    '승객 {passengers:,}명이 재계산 상한 {limit:,}명을 넘어 재계산 oracle을 돌리지 않았다. '
    '명세 §4는 oracle을 N ≤ 10,000으로 한정한다. 이 실행 결과를 "정확도 검증됨"으로 쓰지 '
    '말 것. --max-recompute로 상한을 올릴 수 있다. '
    '(명세 §4는 10,000명 초과에도 V11을 적용한다고 적었으나 V11의 만차 거절은 oracle 없이는 '
    '판정할 수 없다. 명세 보완이 필요하다.)')


def verify(run_dir, scenario_dir=None, *, recompute_limit=DEFAULT_RECOMPUTE_LIMIT, scan='full'):
    """검증을 실행해 Report를 돌려준다."""
    scenario, run = inputs.resolve(run_dir, scenario_dir)
    passengers = arrivals_ref.generate(scenario.bin_rows, run.measurement.get('arrival_seed'))

    results = run_invariants(scenario, run, passengers)
    results.append(arrivals_ref.check_realization(scenario, run))
    results.extend(run_guards(scenario, run))

    if len(passengers) <= recompute_limit:
        oracle = recompute(scenario, passengers, run.max_wait_s, run.max_journey_s,
                           run.drain_s, scan=scan)
        results.append(check_boarding(scenario, run, oracle))
        results.append(check_deadlines(scenario, run, oracle))
        results.extend(run_metrics(scenario, run, oracle))
    else:
        note = SKIP_NOTE.format(passengers=len(passengers), limit=recompute_limit)
        for check_id, title, unit in ORACLE_CHECKS:
            skipped = CheckResult(check_id, title, unit=unit, note=note)
            skipped.status = NOT_RUN
            results.append(skipped)

    return report.build(scenario, run, results, recompute_limit=recompute_limit, scan=scan)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m busstrike.evaluation.verify',
        description='M05 산출물을 독립적으로 다시 계산해 검증한다 (docs/verifier_spec.md).')
    parser.add_argument('run', help='실행 결과 폴더 (events.jsonl·passenger_outcomes.csv·'
                                    'metrics.json·measurement.json)')
    parser.add_argument('--scenario', help='시나리오 폴더. 생략하면 run 옆·상위에서 찾는다')
    parser.add_argument('--out', default=None,
                        help="보고서 경로. 기본 <run>/verifier_report.json, '-'는 표준출력")
    parser.add_argument('--max-recompute', type=int, default=DEFAULT_RECOMPUTE_LIMIT,
                        help=f'재계산 oracle 승객 상한 (기본 {DEFAULT_RECOMPUTE_LIMIT})')
    parser.add_argument('--scan', choices=('full', 'index'), default='full',
                        help='승차 자격 탐색 방식. full이 명세 문장 그대로다 (기본 full)')
    parser.add_argument('--quiet', action='store_true', help='요약 표를 찍지 않는다')
    args = parser.parse_args(argv)

    try:
        result = verify(args.run, args.scenario, recompute_limit=args.max_recompute,
                        scan=args.scan)
    except inputs.InputError as exc:
        print(f'입력 오류: {exc}', file=sys.stderr)
        return 2

    out = args.out if args.out is not None else str(Path(args.run) / 'verifier_report.json')
    written = result.write(out)
    if not args.quiet:
        stream = sys.stderr if out == '-' else sys.stdout
        for line in result.summary_lines():
            print(line, file=stream)
        if written is not None:
            print(f'보고서: {written}', file=stream)
    return 0 if result.passed else 1


if __name__ == '__main__':
    sys.exit(main())
