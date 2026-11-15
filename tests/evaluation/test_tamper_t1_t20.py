"""명세 §6 변조 검출 사례 T1–T20. 전부 검출해야 통과다(명세 §7 수용 기준).

각 사례는 정상 산출물을 복사해 **한 가지만** 바꾼다. 아래 표의 `expects`는 명세 §6의
"기대 검출" 열을 그대로 옮긴 것이고, `not_caught`는 명세가 걸린다고 적었지만 실제로는
걸리지 않는 검사다(명세 기대가 과한 경우. 검출 자체는 다른 검사로 된다).

`not_caught`를 두는 이유: 명세 기대를 조용히 지우면 차이가 사라진다. 걸리지 않는다는
사실 자체를 테스트로 고정해 두고 보고한다.
"""
from dataclasses import dataclass, field
import json
import unittest

from busstrike.evaluation import arrivals_ref as ref
from busstrike.evaluation import inputs
from busstrike.evaluation.checks import FAIL

from . import tamper
from .test_metrics_recompute import FIXTURES, all_checks


@dataclass(frozen=True)
class Case:
    """변조 1건. expects는 명세 §6의 기대 검출, not_caught는 명세 기대 중 실제로 안 걸리는 것."""
    case_id: str
    description: str
    fixture: str
    mutations: tuple
    expects: tuple
    not_caught: tuple = ()
    reason: str = ''


def _events(fixture):
    return tamper.read_events(FIXTURES / fixture)


def _first_boarded_index(fixture):
    rows = _events(fixture)
    return tamper.first_index(rows, kind='boarded')


def _t13_move_outside_bin(base):
    """발생 시각을 bin 밖으로 옮기고 realization checksum을 다시 계산한다."""
    path = base / 'run/realization.jsonl'
    lines = path.read_text(encoding='utf-8').splitlines()
    meta = json.loads(lines[0])
    rows = [json.loads(line) for line in lines[1:]]
    rows[0][3] += 10_000                       # bin_width_s=1이므로 확실히 bin 밖이다
    source = ref.source_checksum(inputs.Scenario(base / 'scenario').bin_rows)
    meta['checksum'] = ref.realization_checksum(meta['seed'], source,
                                                [tuple(r) for r in rows])
    payload = [json.dumps(meta, separators=(',', ':'), sort_keys=True)]
    payload += [json.dumps(r, separators=(',', ':')) for r in rows]
    path.write_text('\n'.join(payload) + '\n', encoding='utf-8')


CASES = (
    Case('T1', 'boarded 사건 1줄 삭제', 'f2',
         (tamper.drop_event(kind='boarded'),), ('V4', 'V5', 'V8')),
    Case('T2', '같은 passenger_id의 generated 사건 복제', 'f2',
         (tamper.duplicate_event(kind='generated'),), ('V2', 'V4')),
    Case('T3', 'occupancy를 capacity+1로 수정', 'f2',
         (tamper.change_event('occupancy', 3, kind='boarded', occupancy=2),), ('V5',)),
    Case('T4', '같은 방문의 두 boarded 승객 순서 교환', 'f2',
         (tamper.swap_events(_first_boarded_index('f2')),), ('V6',)),
    Case('T5', 'outcomes CSV served→unserved 1명', 'f2',
         (tamper.change_outcome('["a",0]', outcome='unserved', reason='wait'),),
         ('V8', 'V9'), not_caught=('V9',),
         reason='V9는 metrics.json을 재계산과 비교한다. CSV만 바꾸면 metrics도 재계산도 '
                '변하지 않아 V9는 걸리지 않는다. CSV 변조는 V8이 잡는다.'),
    Case('T6', 'vehicle_arrived time_s를 일정보다 1초 앞당김', 'f2',
         (tamper.shift_event_time(-1, kind='vehicle_arrived', time_s=200),),
         ('V2', 'V3'), not_caught=('V2',),
         reason='1초 이동이 키 역행을 일으키지 않으면 V2의 순서 검사는 걸리지 않는다. '
                '일정 시각 불일치를 V3가 잡는다.'),
    Case('T7', '출발지가 아닌 정류장 방문에서 boarded', 'f2',
         (tamper.change_visit('t1:00000001', kind='boarded', passenger_id='["a",0]'),),
         ('V4',)),
    Case('T8', '목적지가 아닌 정류장에서 alighted', 'f2',
         (tamper.change_visit('t1:00000001', kind='alighted', passenger_id='["a",0]'),),
         ('V4',)),
    Case('T9', '빈 좌석이 있는 방문에서 자격 승객 승차를 삭제하고 이후 사건을 일관되게 수정',
         'f2',
         (tamper.remove_ride('["g",0]', physical='waiting', boarding_s='', arrival_s=''),),
         ('V6',)),
    Case('T10', 'metrics M1.served +1', 'f2',
         (tamper.bump_metric('M1.served', 1),), ('V9',)),
    Case('T11', 'metrics M2.refusal_events −1', 'f2',
         (tamper.bump_metric('M2.refusal_events', -1),), ('V11',)),
    Case('T12', '사유 wait→no_connection 1명 (CSV·metrics 동시)', 'f2',
         (tamper.change_outcome('["a",2]', reason='no_connection'),
          tamper.set_metric('M2.unserved_by_reason',
                            {'journey': 1, 'no_connection': 3, 'service_end': 1, 'wait': 1})),
         ('V10',)),
    Case('T13', 'realization time_s를 bin 밖으로 이동하고 checksum 재계산', 'f2',
         (_t13_move_outside_bin,), ('V1',)),
    Case('T14', 'demand_bins 한 행 인원 −1', 'f2',
         (tamper.bump_bin_count('a', -1),), ('V1', 'V17')),
    Case('T15', '인접 두 사건 줄 순서 교환', 'f2',
         (tamper.swap_events(1),), ('V2',)),
    Case('T16', 'drain_s 이후 시각 사건 1줄 추가', 'f2',
         (tamper.append_event(time_s=99_999),), ('V2',)),
    Case('T17', '배차간격 위반 일정인데 metrics G.R12.pass=true', 'f2-headway599',
         (tamper.set_metric('G.R12.pass', True),), ('V15',)),
    Case('T18', 'censored 승객 1명을 CSV에서 삭제', 'f2',
         (tamper.drop_outcome('["f",0]'),), ('V8',)),
    Case('T19', 'outcomes CSV 한 승객 destination 변경', 'f2',
         (tamper.change_outcome('["a",0]', destination='B'),), ('V1', 'V4')),
    Case('T20', 'expired 사건을 기한보다 1초 늦게 기록', 'f2',
         (tamper.shift_event_time(1, kind='expired', time_s=1050),), ('V7',)),
)


def caught_by(case):
    """변조 사본을 만들어 실패한 검사 ID 목록을 돌려준다."""
    with tamper.tampered(case.fixture, *case.mutations) as base:
        scenario = inputs.Scenario(base / 'scenario')
        run = inputs.Run(base / 'run')
        _, results = all_checks(scenario, run)
        return sorted((r.check_id for r in results if r.status == FAIL),
                      key=lambda cid: int(cid[1:]))


class TamperDetectionTests(unittest.TestCase):
    """T1–T20이 전부 검출되고, 명세가 적은 검사가 실제로 걸리는지 확인한다."""

    def test_all_cases_are_detected(self):
        for case in CASES:
            with self.subTest(case=case.case_id, change=case.description):
                caught = caught_by(case)
                self.assertNotEqual([], caught,
                                    f'{case.case_id}이(가) 전혀 검출되지 않았다: {case.description}')

    def test_expected_checks_catch_each_case(self):
        for case in CASES:
            expected = [cid for cid in case.expects if cid not in case.not_caught]
            with self.subTest(case=case.case_id):
                caught = caught_by(case)
                missing = [cid for cid in expected if cid not in caught]
                self.assertEqual([], missing,
                                 f'{case.case_id}: 명세가 기대한 {missing}이 걸리지 않았다 '
                                 f'(걸린 것: {caught})')

    def test_documented_spec_overclaims_stay_documented(self):
        """명세 기대 중 걸리지 않는 검사를 고정한다. 걸리게 되면 이 테스트가 알려 준다."""
        documented = [case for case in CASES if case.not_caught]
        self.assertEqual(['T5', 'T6'], [case.case_id for case in documented],
                         '명세 기대와의 차이 목록이 바뀌었다. 보고 내용을 갱신하라.')
        for case in documented:
            with self.subTest(case=case.case_id):
                self.assertTrue(case.reason, f'{case.case_id}: 차이 사유를 적어야 한다')
                caught = caught_by(case)
                for cid in case.not_caught:
                    self.assertNotIn(cid, caught,
                                     f'{case.case_id}: 명세대로 {cid}가 걸렸다. '
                                     f'not_caught에서 빼고 보고를 갱신하라.')

    def test_t9_forgery_is_self_consistent(self):
        """T9 위조는 V5(정원)·V8(결과)를 통과해야 의미가 있다. V6만이 잡는 사례다."""
        case = next(c for c in CASES if c.case_id == 'T9')
        caught = caught_by(case)
        self.assertIn('V6', caught)
        self.assertNotIn('V5', caught, 'T9 위조가 자기 정합적이지 않다 (occupancy 모순)')
        self.assertNotIn('V8', caught, 'T9 위조가 자기 정합적이지 않다 (결과 모순)')

    def test_case_table_covers_t1_to_t20(self):
        self.assertEqual([f'T{i}' for i in range(1, 21)], [c.case_id for c in CASES])


if __name__ == '__main__':
    unittest.main()


class TamperDetectionWithRepairedDigestsTests(unittest.TestCase):
    """digest까지 다시 맞춘 위조에서도 해당 검사가 잡는지 확인한다.

    V17은 파일을 건드리면 무조건 걸린다. 그 도움 없이도 T1–T20이 검출되는지를 보는 것이
    이 테스트의 목적이다.
    """

    def caught(self, case):
        with tamper.tampered(case.fixture, *case.mutations, tamper.repair_digests) as base:
            scenario = inputs.Scenario(base / 'scenario')
            run = inputs.Run(base / 'run')
            _, results = all_checks(scenario, run)
            return sorted((r.check_id for r in results if r.status == FAIL),
                          key=lambda cid: int(cid[1:]))

    def test_v17_no_longer_fires(self):
        """보정이 제대로 됐는지 먼저 확인한다. V17이 남으면 보정이 불완전하다."""
        for case in CASES:
            with self.subTest(case=case.case_id):
                self.assertNotIn('V17', self.caught(case),
                                 f'{case.case_id}: digest 보정이 불완전하다')

    def test_still_detected_without_v17(self):
        for case in CASES:
            with self.subTest(case=case.case_id, change=case.description):
                caught = self.caught(case)
                self.assertNotEqual([], caught,
                                    f'{case.case_id}이(가) V17 없이는 검출되지 않는다')

    def test_expected_checks_still_catch(self):
        for case in CASES:
            expected = [cid for cid in case.expects
                        if cid not in case.not_caught and cid != 'V17']
            with self.subTest(case=case.case_id):
                caught = self.caught(case)
                missing = [cid for cid in expected if cid not in caught]
                self.assertEqual([], missing,
                                 f'{case.case_id}: {missing}이 걸리지 않았다 (걸린 것: {caught})')
