"""운행안 비교 계산기 (M07).

여러 실행 결과를 읽어 운행안들을 한 표로 비교한다. 규칙은
`docs/design/evaluation-protocol.md`를 따른다.

이 도구의 일은 표를 그리는 것이 아니라 **비교가 성립하는지 먼저 막는 것**이다.

- 공정 비교 조건 C1–C6이 깨지면 **비교를 거부한다**. 수요·차량 수·정책·망이 다른 실행을
  한 표에 넣지 않는다.
- 독립 검증을 통과하지 않은 수치는 **"독립 검증 전 잠정"** 으로 표시한다(§7.3).
- 불확실성을 숨기지 않는다. 미확정 승객은 하한·상한으로, 같은 모델의 표본 여럿은
  중앙값과 최소·최대로 낸다(§5.5).
- 출처는 **비교가 끝난 뒤에만** 붙인다(C5 출처 숨김).

엔진 산출물 형식은 아직 팀에서 하나로 정해지지 않았다. 그래서 읽는 부분을 `adapters`로
떼어 두었다. 형식이 늘면 어댑터만 추가한다.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import statistics

from .checks import FAIL, NOT_RUN, NOT_VERIFIED, PASS

PROVISIONAL = '독립 검증 전 잠정'

# 공정 비교 조건 (프로토콜 §3). 모든 실행에서 같아야 하는 것.
CONDITIONS = (
    ('demand', 'C1 같은 수요'),
    ('vehicles', 'C2 같은 차량 수'),
    ('policy', 'C4 같은 정책'),
    ('network', 'C6 같은 망'),
)


@dataclass
class RunRecord:
    """실행 1건. 어댑터가 채운다."""
    run_id: str
    plan_id: str
    # 공정 조건 — 비교 전에 모두 같은지 본다
    demand: str = None            # 수요 입력 식별자(checksum 권장)
    vehicles: int = None          # 가용 차량 수
    policy: tuple = None          # (max_wait_s, max_journey_s, drain_s)
    network: str = None           # 망 스냅샷 식별자
    # 1차 지표 — 불확실성을 범위로 둔다
    served: int = None
    unserved: int = None
    censored: int = None
    total: int = None
    # 보조 지표
    unserved_by_reason: dict = field(default_factory=dict)
    wait_p90_s: int = None
    worst_area_rate: float = None
    worst_area: str = None
    refusal_events: int = None
    vehicles_used: int = None
    # 가정 비중 (프로토콜 U3)
    segments_observed: int = None
    segments_synthesized: int = None
    segments_estimated: int = None
    # 검증 상태
    verified: bool = False
    verify_note: str = ''
    source: dict = field(default_factory=dict)   # 비교 뒤에만 쓴다

    @property
    def rate_bounds(self):
        """충족률 (하한, 상한). 미확정이 없으면 둘이 같다. 분모 0이면 (None, None)."""
        if not self.total:
            return (None, None)
        lo = self.served / self.total
        hi = (self.served + (self.censored or 0)) / self.total
        return (lo, hi)

    @property
    def assumption_share(self):
        """추정으로 채운 구간의 비중. 셋 다 없으면 None."""
        parts = [self.segments_observed, self.segments_synthesized, self.segments_estimated]
        if any(p is None for p in parts):
            return None
        total = sum(parts)
        return (self.segments_estimated / total) if total else None


# ---------- 어댑터 ----------

def from_verifier_run(run_dir, verifier_report=None):
    """`sim.harness` 형식 실행 폴더 + `verifier_report.json`을 RunRecord로.

    보고서가 없으면 **검증되지 않은 것으로 표시**한다. 값을 추측하지 않는다.
    """
    run_dir = Path(run_dir)
    measurement = json.loads((run_dir / 'measurement.json').read_text(encoding='utf-8'))
    metrics = json.loads((run_dir / 'metrics.json').read_text(encoding='utf-8'))
    m1 = metrics.get('M1') or {}
    m2 = metrics.get('M2') or {}
    m3 = metrics.get('M3') or {}
    m5 = metrics.get('M5') or {}
    config = measurement.get('config') or {}

    report_path = Path(verifier_report) if verifier_report else run_dir / 'verifier_report.json'
    verified, note = False, '검증기 보고서 없음'
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding='utf-8'))
        verified = bool(report.get('pass'))
        counts = report.get('summary') or {}
        note = (f"pass={report.get('pass')} "
                f"(실패 {counts.get(FAIL, 0)} · 미실행 {counts.get(NOT_RUN, 0)} · "
                f"미검증 {counts.get(NOT_VERIFIED, 0)})")

    worst = m5.get('min_served_rate') or {}
    return RunRecord(
        run_id=str(run_dir), plan_id=measurement.get('scenario_manifest', {}).get('network')
        or measurement.get('arrival_seed') or str(run_dir.name),
        demand=measurement.get('realization_checksum'),
        vehicles=None,
        policy=(config.get('max_wait_s'), config.get('max_journey_s'), config.get('drain_s')),
        network=(measurement.get('scenario_manifest') or {}).get('sha256', {}).get('network.json'),
        served=m1.get('served'), unserved=m1.get('unserved'),
        censored=m1.get('censored'), total=m1.get('total'),
        unserved_by_reason=m2.get('unserved_by_reason') or {},
        wait_p90_s=(m3.get('served') or {}).get('p90_s'),
        worst_area_rate=worst.get('rate'), worst_area=worst.get('area'),
        refusal_events=m2.get('refusal_events'),
        vehicles_used=len((metrics.get('M6') or {}).get('vehicles') or []) or None,
        verified=verified, verify_note=note)


# ---------- 공정 조건 ----------

def check_conditions(records):
    """C1–C6이 모든 실행에서 같은지. (문제 목록, 공통 조건) 반환."""
    problems, shared = [], {}
    for key, label in CONDITIONS:
        values = {r.run_id: getattr(r, key) for r in records}
        present = {v for v in values.values() if v is not None}
        if not present:
            problems.append({'condition': label, 'issue': '모든 실행에 값이 없어 확인할 수 없다',
                             'values': values})
            continue
        if len(present) > 1:
            problems.append({'condition': label, 'issue': '실행마다 다르다 — 비교할 수 없다',
                             'values': values})
            continue
        if None in values.values():
            problems.append({'condition': label, 'issue': '일부 실행에 값이 없다',
                             'values': values})
        shared[key] = present.pop()
    return problems, shared


# ---------- 묶기 ----------

def group_samples(records, key=lambda r: r.plan_id):
    """같은 운행안의 표본 여럿을 묶는다. AI안은 모델당 n회를 전부 넣는다."""
    groups = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return groups


def summarize_group(name, samples):
    """표본 여럿을 중앙값과 최소·최대로. 하나면 그 값 그대로(§5.5)."""
    bounds = [s.rate_bounds for s in samples if s.rate_bounds[0] is not None]
    lows = [b[0] for b in bounds]
    highs = [b[1] for b in bounds]
    shares = [s.assumption_share for s in samples if s.assumption_share is not None]
    reasons = {}
    for s in samples:
        for k, v in (s.unserved_by_reason or {}).items():
            reasons.setdefault(k, []).append(v)
    waits = [s.wait_p90_s for s in samples if s.wait_p90_s is not None]
    worst = [s.worst_area_rate for s in samples if s.worst_area_rate is not None]

    return {
        'plan': name,
        'samples': len(samples),
        'rate_low': statistics.median(lows) if lows else None,
        'rate_high': statistics.median(highs) if highs else None,
        'rate_low_range': [min(lows), max(lows)] if len(lows) > 1 else None,
        'top_unserved_reason': (max(reasons, key=lambda k: statistics.median(reasons[k]))
                                if reasons else None),
        'unserved_by_reason_median': {k: statistics.median(v) for k, v in reasons.items()} or None,
        'wait_p90_s': statistics.median(waits) if waits else None,
        'worst_area_rate': statistics.median(worst) if worst else None,
        'assumption_share': statistics.median(shares) if shares else None,
        'verified': all(s.verified for s in samples),
        'unverified_samples': [s.run_id for s in samples if not s.verified],
    }


# ---------- 보고 ----------

def build(records, manifest=None, allow_unfair=False):
    """비교 결과 dict. 조건이 깨지면 `comparable=false`로 돌려준다."""
    problems, shared = check_conditions(records)
    comparable = not problems or allow_unfair
    groups = group_samples(records)
    rows = [summarize_group(name, samples) for name, samples in sorted(groups.items())]
    if manifest:
        plans = (manifest.get('plans') or {})
        for row in rows:                      # 출처는 비교가 끝난 뒤에만 붙인다 (C5)
            row['origin'] = (plans.get(row['plan']) or {}).get('origin')

    unverified = [r.run_id for r in records if not r.verified]
    return {
        'schema': 'busstrike.evaluation.plan_comparison/v1',
        'comparable': comparable,
        'fairness_problems': problems,
        'shared_conditions': shared,
        'label': PROVISIONAL if unverified else '독립 검증 통과',
        'unverified_runs': unverified,
        'runs': len(records),
        'rows': rows,
        'caveats': [
            '시뮬레이션 결과이며 실제 운행 성능이 아니다.',
            '수요 관측 시점과 운행안 시점이 다르면 실제 재현이 아니라 시나리오다.',
            '추정으로 채운 구간 비중이 큰 안은 그 수치로 결론을 쓰지 않는다 (프로토콜 U3).',
        ],
    }


def render(result):
    """사람이 읽는 표. 불확실성을 범위로 보인다."""
    lines = []
    if not result['comparable']:
        lines.append('⚠ 공정 비교 조건이 깨져 비교하지 않았다. 아래 문제를 먼저 해결한다.')
        for p in result['fairness_problems']:
            lines.append(f"  - {p['condition']}: {p['issue']}")
            for run_id, value in p['values'].items():
                lines.append(f"      {run_id}: {value}")
        return '\n'.join(lines)

    if result['unverified_runs']:
        lines.append(f"⚠ {result['label']} — 독립 검증을 통과하지 않은 실행 "
                     f"{len(result['unverified_runs'])}건이 섞여 있다. 비교 결론에 쓰지 말 것.")
        lines.append('')
    lines.append(f"{'운행안':<12}{'표본':>4}  {'충족률(하한–상한)':<30}"
                 f"{'미충족 최다':<16}{'P90 대기':>9}{'지역 최저':>9}{'추정 비중':>9}")
    lines.append('-' * 94)
    for row in result['rows']:
        if row['rate_low'] is None:
            rate = '—'
        elif row['rate_low'] == row['rate_high']:
            rate = f"{row['rate_low']:.3f}"
        else:
            rate = f"{row['rate_low']:.3f}–{row['rate_high']:.3f}"
        if row['rate_low_range']:
            rate += f" ({row['rate_low_range'][0]:.3f}~{row['rate_low_range'][1]:.3f})"
        wait = f"{row['wait_p90_s']}초" if row['wait_p90_s'] is not None else '—'
        worst = f"{row['worst_area_rate']:.2f}" if row['worst_area_rate'] is not None else '—'
        share = f"{row['assumption_share']:.0%}" if row['assumption_share'] is not None else '—'
        mark = '' if row['verified'] else ' *'
        lines.append(f"{row['plan']:<12}{row['samples']:>4}  {rate:<30}"
                     f"{(row['top_unserved_reason'] or '—'):<16}{wait:>9}{worst:>9}{share:>9}{mark}")
    if any(not r['verified'] for r in result['rows']):
        lines.append('\n* 독립 검증 미통과')
    lines.append('\n' + '\n'.join('· ' + c for c in result['caveats']))
    return '\n'.join(lines)
