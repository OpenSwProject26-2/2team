"""V1·V6·V7·V9–V17 테스트와 명세 §5 손계산 대조.

핵심은 **재계산(oracle)**과 손계산 표의 대조다. 엔진 출력은 정답이 아니라 비교 대상이다.
정답 출처: tests/evaluation/fixtures/handcalc/*.json (명세 §5를 사람이 옮긴 것).
"""
from fractions import Fraction
import json
import os
from pathlib import Path
import unittest

from busstrike.evaluation import arrivals_ref as ref
from busstrike.evaluation import inputs
from busstrike.evaluation.checks import FAIL, NOT_VERIFIED, PASS
from busstrike.evaluation.guards import headway_report, run_guards
from busstrike.evaluation.invariants import check_boarding, check_deadlines, run_invariants
from busstrike.evaluation.metrics_ref import run_metrics, summarize
from busstrike.evaluation.recompute import recompute

from . import tamper

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
HANDCALC = FIXTURES / 'handcalc'
ALL_FIXTURES = ('f1', 'f1-w9', 'f1-j14', 'f1-drain12', 'f1-wj9', 'f2', 'f2-headway599')
COMPLEX_RUN = os.environ.get('BUSSTRIKE_COMPLEX_RUN')
# 1만 명은 느리므로(약 8초) 따로 켠다. 명세 §7은 1천·1만 명 둘 다 요구한다.
COMPLEX_RUN_10K = os.environ.get('BUSSTRIKE_COMPLEX_RUN_10K')


def load(name):
    base = FIXTURES / name
    return inputs.Scenario(base / 'scenario'), inputs.Run(base / 'run')


def oracle_of(scenario, run, scan='full'):
    """집계 수요 행과 seed로 발생을 다시 만들고 재계산한다. 엔진 출력을 쓰지 않는다."""
    passengers = ref.generate(scenario.bin_rows, run.measurement['arrival_seed'])
    return passengers, recompute(scenario, passengers, run.max_wait_s, run.max_journey_s,
                                 run.drain_s, scan=scan)


def all_checks(scenario, run, scan='full'):
    passengers, oracle = oracle_of(scenario, run, scan)
    results = (run_invariants(scenario, run, passengers)
               + [ref.check_realization(scenario, run),
                  check_boarding(scenario, run, oracle),
                  check_deadlines(scenario, run, oracle)]
               + run_metrics(scenario, run, oracle) + run_guards(scenario, run))
    results.sort(key=lambda r: int(r.check_id[1:]))
    return oracle, results


# ---------- 손계산 기대값 해석 ----------

def expected_value(value):
    """[a, b]는 분수 a/b다(손계산 표의 2/9·220/3 같은 값)."""
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return float(Fraction(*value))
    return value


def walk(path, payload):
    node = payload
    for key in path.split('.'):
        node = node[key]
    return node


class HandCalculationMetricsTests(unittest.TestCase):
    """명세 §5 손계산 표 ↔ 검증기 재계산. 승객 단위와 지표 단위 둘 다."""

    def assert_expected(self, label, expected, actual, path=''):
        """손계산이 말한 항목만 비교한다. 명세 미기재 항목(_ 접두)은 건너뛴다."""
        if isinstance(expected, dict):
            for key, value in expected.items():
                if key.startswith('_'):
                    continue
                self.assertIn(key, actual, f'{label} {path}.{key}: 재계산에 항목이 없다')
                self.assert_expected(label, value, actual[key], f'{path}.{key}')
            return
        want = expected_value(expected)
        self.assertEqual(want, actual, f'{label} {path}: 손계산 {want!r} ≠ 재계산 {actual!r}')

    # -- 승객 단위 --

    def test_f1_passengers(self):
        spec = json.loads((HANDCALC / 'f1_expected.json').read_text())
        for name, variant in spec['variants'].items():
            scenario, run = load(name)
            _, oracle = oracle_of(scenario, run)
            for pid, expected in variant['passengers'].items():
                with self.subTest(fixture=name, passenger=pid):
                    state = oracle.states[pid]
                    self.assert_expected(f'{name} {pid}', expected, {
                        'outcome': state.outcome, 'reason': state.reason,
                        'physical': state.physical, 'boarding_s': state.boarding_s,
                        'arrival_s': state.arrival_s, 'refused': state.refused})

    def test_f2_passengers(self):
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        scenario, run = load('f2')
        _, oracle = oracle_of(scenario, run)
        from busstrike.evaluation.metrics_ref import unserved_reasons
        reasons = unserved_reasons(scenario, oracle, run.drain_s)
        self.assertEqual(len(spec['passengers']), len(oracle.states))
        for pid, expected in spec['passengers'].items():
            with self.subTest(passenger=pid):
                state = oracle.states[pid]
                self.assert_expected(f'f2 {pid}', expected, {
                    'origin': state.origin, 'destination': state.destination,
                    'time_s': state.time_s, 'outcome': state.outcome,
                    'physical': state.physical, 'arrival_s': state.arrival_s,
                    'wait_s': state.wait_s, 'refused': state.refused,
                    # 명세 §5의 '사유'는 M2 사유(V10)다. 기한 종류가 아니다.
                    'reason': reasons.get(pid)})

    # -- 지표 단위 --

    def test_f1_metrics(self):
        spec = json.loads((HANDCALC / 'f1_expected.json').read_text())
        for name, variant in spec['variants'].items():
            if not variant['metrics']:
                continue
            scenario, run = load(name)
            _, oracle = oracle_of(scenario, run)
            summary = summarize(scenario, run, oracle)
            for path, expected in variant['metrics'].items():
                if path.startswith('_'):
                    continue
                with self.subTest(fixture=name, metric=path):
                    self.assert_expected(name, expected, walk(path, summary), path)

    def test_f2_metrics(self):
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        scenario, run = load('f2')
        _, oracle = oracle_of(scenario, run)
        summary = summarize(scenario, run, oracle)
        checked = 0
        for path, expected in spec['metrics'].items():
            if path.startswith('_'):
                continue
            with self.subTest(metric=path):
                if path.startswith('G.R12'):
                    max_gap, _, _ = headway_report(scenario, spec['config']['max_headway_s'])
                    actual = {'max_gap_by_route_s': max_gap}[path.split('.', 2)[2]]
                else:
                    actual = walk(path, summary)
                self.assert_expected('f2', expected, actual, path)
            checked += 1
        self.assertEqual(13, checked, '손계산 지표 항목 수가 바뀌었다')

    def test_f2_r12_variants(self):
        """상한 600이면 pass, 599면 r1의 A·B·C 3건 위반 (명세 §5 F2 마지막 줄)."""
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        variants = spec['metrics']['_R12_variants']
        scenario, _ = load('f2')
        for label, expected in variants.items():
            limit = int(label.split('=')[1])
            with self.subTest(max_headway_s=limit):
                _, violations, _ = headway_report(scenario, limit)
                self.assertEqual(expected['pass'], not violations)
                if 'violation_count' in expected:
                    self.assertEqual(expected['violation_count'], len(violations))
                    self.assertEqual(expected['stops'], sorted(v['stop'] for v in violations))
                    self.assertEqual({expected['route']}, {v['route'] for v in violations})


class ArrivalGeneratorTests(unittest.TestCase):
    """V1 발생 생성기 독립 재구현."""

    def test_reproduces_realization_and_checksums(self):
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                seed = run.measurement['arrival_seed']
                mine = ref.generate(scenario.bin_rows, seed)
                meta, filed = run.realization()
                self.assertEqual(filed, mine, '발생이 realization.jsonl과 다르다')
                source = ref.source_checksum(scenario.bin_rows)
                self.assertEqual(meta['source_checksum'], source)
                self.assertEqual(meta['checksum'],
                                 ref.realization_checksum(seed, source, mine))
                self.assertEqual(run.measurement['realization_checksum'], meta['checksum'])

    def test_passenger_id_format(self):
        self.assertEqual('["d",0]', ref.passenger_id('d', 0))
        self.assertEqual('["S0002>S0000@28800",3]',
                         ref.passenger_id('S0002>S0000@28800', 3))

    def test_offset_stays_inside_bin(self):
        for width in (1, 2, 7, 600, 86_400):
            for index in range(5):
                offset = ref.offset('seed-x', 'agg-y', index, width)
                self.assertTrue(0 <= offset < width, (width, index, offset))

    def test_width_one_bin_is_deterministic_zero(self):
        for index in range(20):
            self.assertEqual(0, ref.offset('any', 'any', index, 1))

    def test_rejects_zero_width(self):
        with self.assertRaises(ValueError):
            ref.offset('s', 'a', 0, 0)


class OracleTests(unittest.TestCase):
    def test_full_and_index_scan_agree(self):
        """명세 문장대로의 전체 선형 탐색과 출발지 인덱스 방식이 같은 결과를 낸다."""
        def snapshot(oracle):
            return (sorted((p, s.outcome, s.reason, s.physical, s.boarding_s, s.arrival_s,
                            s.refused) for p, s in oracle.states.items()),
                    sorted(oracle.boardings.items()), sorted(oracle.alightings.items()),
                    sorted(oracle.expiries.items()), sorted(oracle.departures.items()),
                    oracle.refusal_events)
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                _, full = oracle_of(scenario, run, scan='full')
                _, index = oracle_of(scenario, run, scan='index')
                self.assertEqual(snapshot(full), snapshot(index))

    def test_rejects_unknown_scan(self):
        scenario, run = load('f1')
        with self.assertRaises(ValueError):
            oracle_of(scenario, run, scan='queue')

    def test_oracle_matches_engine_per_passenger(self):
        """재계산이 엔진 산출물과 승객 단위로 일치한다(명세 §7 수용 기준)."""
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                _, oracle = oracle_of(scenario, run)
                self.assertEqual(len(run.outcomes), len(oracle.states))
                for pid, state in oracle.states.items():
                    declared = run.outcomes[pid]
                    self.assertEqual(
                        (declared.outcome, declared.physical, declared.boarding_s,
                         declared.arrival_s, declared.refused),
                        (state.outcome, state.physical, state.boarding_s,
                         state.arrival_s, state.refused), f'{name} {pid}')


class AllChecksOnNormalOutputTests(unittest.TestCase):
    def test_v1_to_v17_present_and_not_failing(self):
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                _, results = all_checks(scenario, run)
                self.assertEqual([f'V{i}' for i in range(1, 18)],
                                 [r.check_id for r in results])
                failed = {r.check_id: [v.as_dict() for v in r.violations[:2]]
                          for r in results if r.status == FAIL}
                self.assertEqual({}, failed)

    def test_v16_is_never_reported_as_pass(self):
        """R10 가용 fleet과 R13 방향별 구간은 산출물에 자료가 없다. 통과로 적지 않는다."""
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                _, results = all_checks(scenario, run)
                v16 = next(r for r in results if r.check_id == 'V16')
                self.assertEqual(NOT_VERIFIED, v16.status)
                self.assertIn('R13', v16.note)

    def test_examined_counts_are_not_zero(self):
        scenario, run = load('f2')
        _, results = all_checks(scenario, run)
        for result in results:
            if result.check_id == 'V7':
                continue  # F2에 기한 만료가 있으나 0건인 fixture도 정상이다
            with self.subTest(check=result.check_id):
                self.assertGreater(result.examined, 0, f'{result.check_id}가 공집합을 통과시켰다')


class MetricChecksAreNotVacuousTests(unittest.TestCase):
    """V6·V7·V9–V17이 실제로 변조를 잡는다. T1–T20 전체는 4단계."""

    def caught_by(self, fixture, *mutations):
        with tamper.tampered(fixture, *mutations) as base:
            scenario = inputs.Scenario(base / 'scenario')
            run = inputs.Run(base / 'run')
            _, results = all_checks(scenario, run)
            return sorted((r.check_id for r in results if r.status == FAIL),
                          key=lambda cid: int(cid[1:]))

    def test_v6_catches_swapped_boarding_order(self):
        """같은 방문의 두 승차 순서를 바꾸면 V6가 잡는다 (T4)."""
        caught = self.caught_by('f2', tamper.swap_events(
            tamper.first_index(tamper.read_events(FIXTURES / 'f2'), kind='boarded')))
        self.assertIn('V6', caught)

    def test_v7_catches_late_expiry(self):
        """기한보다 늦게 기록한 만료 사건을 V7이 잡는다 (T20)."""
        caught = self.caught_by('f2', tamper.shift_event_time(1, kind='expired'))
        self.assertIn('V7', caught)

    def test_v9_catches_inflated_served(self):
        caught = self.caught_by('f2', tamper.bump_metric('M1.served', 1))
        self.assertIn('V9', caught)

    def test_v10_catches_reason_swap(self):
        """사유를 CSV·metrics 동시에 바꿔도 V10이 일정에서 다시 판정해 잡는다 (T12)."""
        caught = self.caught_by(
            'f2', tamper.change_outcome('["a",2]', reason='no_connection'),
            tamper.set_metric('M2.unserved_by_reason',
                              {'journey': 1, 'no_connection': 3, 'service_end': 1, 'wait': 1}))
        self.assertIn('V10', caught)

    def test_v11_catches_refusal_undercount(self):
        caught = self.caught_by('f2', tamper.bump_metric('M2.refusal_events', -1))
        self.assertIn('V11', caught)

    def test_v12_catches_wrong_p90(self):
        caught = self.caught_by('f2', tamper.set_metric('M3.served.p90_s', 1))
        self.assertIn('V12', caught)

    def test_v13_catches_wrong_area_rate(self):
        caught = self.caught_by('f2', tamper.set_metric('M5.min_served_rate',
                                                        {'area': 'dong-1', 'rate': 0.25}))
        self.assertIn('V13', caught)

    def test_v14_catches_wrong_empty_time(self):
        def fn(payload):
            payload['M6']['vehicles'][0]['empty_time_s'] = 0
            return payload
        caught = self.caught_by('f2', tamper.edit_json('run/metrics.json', fn))
        self.assertIn('V14', caught)

    def test_v15_catches_false_r12_pass(self):
        """배차간격 위반 일정인데 pass=true면 V15가 잡는다 (T17)."""
        caught = self.caught_by('f2-headway599', tamper.set_metric('G.R12.pass', True))
        self.assertIn('V15', caught)

    def test_v17_catches_edited_scenario_file(self):
        def mutate(base):
            path = base / 'scenario/demand_bins.csv'
            path.write_text(path.read_text().replace('A,C,0,1,3', 'A,C,0,1,2'))
        caught = self.caught_by('f2', mutate)
        self.assertIn('V17', caught)
        self.assertIn('V1', caught)

    def test_v1_catches_realization_moved_outside_bin(self):
        """발생 시각을 bin 밖으로 옮기고 checksum을 다시 계산해도 V1이 잡는다 (T13)."""
        def mutate(base):
            path = base / 'run/realization.jsonl'
            lines = path.read_text().splitlines()
            meta, rows = lines[0], [json.loads(l) for l in lines[1:]]
            rows[0][3] += 10_000
            payload = [meta] + [json.dumps(r, separators=(',', ':')) for r in rows]
            path.write_text('\n'.join(payload) + '\n')
            source = ref.source_checksum(inputs.Scenario(base / 'scenario').bin_rows)
            fixed = json.loads(meta)
            fixed['checksum'] = ref.realization_checksum(
                fixed['seed'], source, [tuple(r) for r in rows])
            payload[0] = json.dumps(fixed, separators=(',', ':'), sort_keys=True)
            path.write_text('\n'.join(payload) + '\n')
        caught = self.caught_by('f2', mutate)
        self.assertIn('V1', caught)


@unittest.skipUnless(COMPLEX_RUN, 'BUSSTRIKE_COMPLEX_RUN 미설정 (make_complex.sh로 생성)')
class ComplexScaleTests(unittest.TestCase):
    """합성 complex 실행 결과에서 V1–V17과 승객 단위 완전 일치 (명세 §7)."""

    def test_all_checks_and_exact_passenger_match(self):
        scenario, run = inputs.resolve(COMPLEX_RUN)
        oracle, results = all_checks(scenario, run)
        failed = {r.check_id: [v.as_dict() for v in r.violations[:2]]
                  for r in results if r.status == FAIL}
        self.assertEqual({}, failed)
        from busstrike.evaluation.metrics_ref import unserved_reasons
        reasons = unserved_reasons(scenario, oracle, run.drain_s)
        self.assertEqual(len(run.outcomes), len(oracle.states))
        for pid, state in oracle.states.items():
            declared = run.outcomes[pid]
            # CSV reason은 M2 사유(V10 재계산)와 비교한다. oracle.state.reason은 기한 종류다.
            self.assertEqual(
                (declared.outcome, declared.reason, declared.physical, declared.boarding_s,
                 declared.arrival_s, declared.refused),
                (state.outcome, reasons.get(pid), state.physical, state.boarding_s,
                 state.arrival_s, state.refused), pid)

    def test_recomputed_metrics_equal_engine_metrics(self):
        scenario, run = inputs.resolve(COMPLEX_RUN)
        _, oracle = oracle_of(scenario, run)
        summary = summarize(scenario, run, oracle)
        self.assertEqual(run.metrics['M1']['served'], summary['M1']['served'])
        self.assertEqual(run.metrics['M2']['unserved_by_reason'],
                         summary['M2']['unserved_by_reason'])
        self.assertEqual(run.metrics['M3']['served']['p90_s'], summary['M3']['served']['p90_s'])


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(COMPLEX_RUN_10K, 'BUSSTRIKE_COMPLEX_RUN_10K 미설정 (느린 선택 실행)')
class ComplexScale10kTests(unittest.TestCase):
    """합성 complex 1만 명. 명세 §4의 재계산 상한이며 §7 수용 기준의 큰 쪽이다."""

    def test_all_checks_and_exact_passenger_match(self):
        from busstrike.evaluation.metrics_ref import unserved_reasons
        scenario, run = inputs.resolve(COMPLEX_RUN_10K)
        self.assertEqual(10_000, len(run.outcomes))
        oracle, results = all_checks(scenario, run)
        failed = {r.check_id: [v.as_dict() for v in r.violations[:2]]
                  for r in results if r.status == FAIL}
        self.assertEqual({}, failed)
        reasons = unserved_reasons(scenario, oracle, run.drain_s)
        for pid, state in oracle.states.items():
            declared = run.outcomes[pid]
            self.assertEqual(
                (declared.outcome, declared.reason, declared.physical, declared.boarding_s,
                 declared.arrival_s, declared.refused),
                (state.outcome, reasons.get(pid), state.physical, state.boarding_s,
                 state.arrival_s, state.refused), pid)
