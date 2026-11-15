"""V2–V5·V8 불변조건 테스트.

정상 산출물에서는 전부 통과해야 하고, 손계산 fixture의 승객별 값과도 맞아야 한다.
합성 complex 실행 결과가 있으면(저장소 밖, make_complex.sh로 생성) 거기서도 돌린다.
"""
import json
import os
from pathlib import Path
import unittest

from busstrike.evaluation import inputs
from busstrike.evaluation.checks import PASS
from busstrike.evaluation.invariants import (classify, collect_passenger_events,
                                             run_invariants, split_visit_id)

from . import tamper

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
HANDCALC = FIXTURES / 'handcalc'
F1_VARIANTS = ('f1', 'f1-w9', 'f1-j14', 'f1-drain12', 'f1-wj9')
ALL_FIXTURES = F1_VARIANTS + ('f2', 'f2-headway599')
# 저장소 밖 합성 complex 산출물. 없으면 해당 테스트는 skip하고 "미실행"으로 보고한다.
COMPLEX_RUN = os.environ.get('BUSSTRIKE_COMPLEX_RUN')


def load(name):
    base = FIXTURES / name
    return inputs.Scenario(base / 'scenario'), inputs.Run(base / 'run')


def failures(results):
    return {r.check_id: [v.as_dict() for v in r.violations[:3]]
            for r in results if r.status != PASS}


class InvariantsOnNormalOutputTests(unittest.TestCase):
    """정상 산출물에서 V2–V5·V8이 모두 통과한다."""

    def test_all_fixtures_pass(self):
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                scenario, run = load(name)
                _, passengers = run.realization()
                results = run_invariants(scenario, run, passengers)
                self.assertEqual({}, failures(results), f'{name}에서 불변조건 위반')
                self.assertEqual(['V2', 'V3', 'V4', 'V5', 'V8'],
                                 [r.check_id for r in results])

    def test_examined_counts_are_not_zero(self):
        """통과했다고 보고하려면 실제로 검사한 대상이 있어야 한다."""
        scenario, run = load('f2')
        _, passengers = run.realization()
        for result in run_invariants(scenario, run, passengers):
            with self.subTest(check=result.check_id):
                self.assertGreater(result.examined, 0,
                                   f'{result.check_id}가 아무것도 검사하지 않고 통과했다')


class HandCalculationTests(unittest.TestCase):
    """사건 로그에서 다시 정한 결과가 명세 §5 손계산 표와 같다."""

    def assert_matches_handcalc(self, name, expected_passengers):
        scenario, run = load(name)
        records = collect_passenger_events(run)
        for pid, expected in expected_passengers.items():
            with self.subTest(fixture=name, passenger=pid):
                self.assertIn(pid, records, f'{name}: 승객 {pid}의 사건이 없다')
                outcome, physical, arrival_s, reason = classify(records[pid], run.drain_s)
                record = records[pid]
                boarding_s = record.boarded[0].time_s if record.boarded else None
                actual = {'outcome': outcome, 'physical': physical, 'arrival_s': arrival_s,
                          'expiry_kind': reason, 'boarding_s': boarding_s,
                          'wait_s': None if boarding_s is None else boarding_s - record.generated[0].time_s}
                for field, want in expected.items():
                    # reason(명세 §5의 '사유' 열)은 M2 사유이며 일정 전체를 봐야 정해진다.
                    # 사건 로그만으로는 기한 종류(wait·journey)밖에 알 수 없으므로 V10에서 검사한다.
                    if field.startswith('_') or field in ('origin', 'destination', 'time_s',
                                                          'refused', 'reason'):
                        continue
                    self.assertEqual(want, actual[field],
                                     f'{name} {pid} {field}: 손계산 {want!r} ≠ 재계산 {actual[field]!r}')

    def test_f1_variants(self):
        spec = json.loads((HANDCALC / 'f1_expected.json').read_text())
        for name, variant in spec['variants'].items():
            self.assert_matches_handcalc(name, variant['passengers'])

    def test_f2(self):
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        self.assert_matches_handcalc('f2', spec['passengers'])

    def test_f2_od_and_generation_times_match_handcalc(self):
        """OD·발생 시각은 CSV가 아니라 realization에서 확인한다."""
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        _, run = load('f2')
        _, passengers = run.realization()
        actual = {pid: (origin, destination, time_s)
                  for pid, origin, destination, time_s in passengers}
        self.assertEqual(len(spec['passengers']), len(actual))
        for pid, expected in spec['passengers'].items():
            with self.subTest(passenger=pid):
                self.assertEqual((expected['origin'], expected['destination'],
                                  expected['time_s']), actual[pid])

    def test_expiry_kind_is_not_the_m2_reason(self):
        """명세 §5 표의 '사유'는 M2 사유(V10)이고 사건의 기한 종류와 다를 수 있다.

        F2에서 ["c",0]·["d",0]은 기한 사건이 wait인데 M2 사유는 no_connection이고,
        ["e",0]은 wait인데 service_end다. 이 구분을 잃으면 V10을 사건 종류로 대체해
        통과시키게 되므로 여기서 명시적으로 고정한다.
        """
        _, run = load('f2')
        records = collect_passenger_events(run)
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        differs = {'["c",0]': 'no_connection', '["d",0]': 'no_connection',
                   '["e",0]': 'service_end'}
        for pid, m2_reason in differs.items():
            with self.subTest(passenger=pid):
                self.assertEqual(m2_reason, spec['passengers'][pid]['reason'])
                self.assertEqual('wait', classify(records[pid], run.drain_s)[3])
                self.assertEqual(m2_reason, run.outcomes[pid].reason)

    def test_f2_refused_counts_match_handcalc(self):
        """refused는 사건에 없으므로 CSV 값을 손계산 표와 비교한다(V11에서 사건으로 재계산)."""
        spec = json.loads((HANDCALC / 'f2_expected.json').read_text())
        _, run = load('f2')
        for pid, expected in spec['passengers'].items():
            with self.subTest(passenger=pid):
                self.assertEqual(expected['refused'], run.outcomes[pid].refused)


class LoaderTests(unittest.TestCase):
    def test_visit_stable_id_roundtrip(self):
        scenario, _ = load('f2')
        for visit in scenario.visits.values():
            self.assertEqual((visit.trip_id, visit.index), split_visit_id(visit.stable_id))

    def test_split_visit_id_rejects_passenger_ids(self):
        for stable_id in ('generate:["d",0]', 'wait:["d",0]', 'journey:["d",0]', 'nope'):
            self.assertIsNone(split_visit_id(stable_id), stable_id)

    def test_resolve_finds_sibling_scenario(self):
        scenario, run = inputs.resolve(FIXTURES / 'f1/run')
        self.assertEqual(3, len(run.outcomes))
        self.assertEqual(2, len(scenario.trips))

    def test_missing_run_directory_raises_input_error(self):
        with self.assertRaises(inputs.InputError):
            inputs.Run(FIXTURES / 'does-not-exist')

    def test_f2_schedule_shape(self):
        """손으로 만든 F2 일정이 명세 §5 F2 본문과 같은지 확인한다."""
        scenario, _ = load('f2')
        self.assertEqual({'t1', 't2', 't3'}, set(scenario.trips))
        t1 = scenario.visits_by_trip['t1']
        self.assertEqual(('A', 'B', 'C'), tuple(v.stop for v in t1))
        self.assertEqual((100, 200, 300), tuple(v.arrival_s for v in t1))
        self.assertEqual((110, 210, 300), tuple(v.departure_s for v in t1))
        self.assertEqual(2, scenario.trips['t1'].capacity)
        self.assertEqual({'t1': 'r1', 't2': 'r1', 't3': 'r2'}, scenario.route_of)
        self.assertEqual('dong-2', scenario.stop_area['C'])


@unittest.skipUnless(COMPLEX_RUN, 'BUSSTRIKE_COMPLEX_RUN 미설정 (make_complex.sh로 생성)')
class ComplexScaleTests(unittest.TestCase):
    """합성 complex 실행 결과에서도 불변조건이 통과한다."""

    def test_invariants_pass(self):
        scenario, run = inputs.resolve(COMPLEX_RUN)
        _, passengers = run.realization()
        results = run_invariants(scenario, run, passengers)
        self.assertEqual({}, failures(results))
        by_id = {r.check_id: r for r in results}
        self.assertEqual(len(run.outcomes), by_id['V8'].examined)
        self.assertEqual(len(scenario.visits), by_id['V3'].examined)


if __name__ == '__main__':
    unittest.main()


class ChecksAreNotVacuousTests(unittest.TestCase):
    """검사가 실제로 변조를 잡는지 확인한다.

    통과만 하는 검사는 쓸모없다. T1–T20 전체는 4단계에서 다루고, 여기서는 V2·V3·V4·V5·V8이
    각각 최소 한 번은 물리는지만 고정한다.
    """

    def caught_by(self, fixture, *mutations):
        with tamper.tampered(fixture, *mutations) as base:
            scenario = inputs.Scenario(base / 'scenario')
            run = inputs.Run(base / 'run')
            _, passengers = run.realization()
            results = run_invariants(scenario, run, passengers)
            return sorted(r.check_id for r in results if r.status != PASS)

    def test_v2_catches_reordered_lines(self):
        self.assertIn('V2', self.caught_by('f2', tamper.swap_events(1)))

    def test_v2_catches_event_after_drain(self):
        self.assertIn('V2', self.caught_by('f2', tamper.append_event(time_s=99_999)))

    def test_v2_catches_duplicated_generated_event(self):
        caught = self.caught_by('f2', tamper.duplicate_event(kind='generated'))
        self.assertEqual(['V2', 'V4'], caught)

    def test_v3_catches_schedule_mismatch(self):
        caught = self.caught_by('f2', tamper.shift_event_time(-1, kind='vehicle_arrived', time_s=200))
        self.assertIn('V3', caught)

    def test_v4_and_v5_catch_deleted_boarding(self):
        caught = self.caught_by('f2', tamper.drop_event(kind='boarded'))
        self.assertEqual(['V4', 'V5', 'V8'], caught)

    def test_v5_catches_impossible_occupancy(self):
        caught = self.caught_by('f2', tamper.change_event('occupancy', 99, kind='boarded'))
        self.assertEqual(['V5'], caught)

    def test_v8_catches_flipped_outcome(self):
        caught = self.caught_by('f2', tamper.change_outcome('["a",0]', outcome='unserved',
                                                            reason='wait'))
        self.assertIn('V8', caught)

    def test_v8_catches_missing_passenger_row(self):
        caught = self.caught_by('f2', tamper.drop_outcome('["f",0]'))
        self.assertIn('V8', caught)
