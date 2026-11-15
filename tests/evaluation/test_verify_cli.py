"""진입점과 verifier_report.json (명세 §7).

    python -m busstrike.evaluation.verify <실행 결과 폴더>

하나라도 실패하면 pass=false이고 종료 코드는 1이다. 부분 통과를 "검증됨"으로 적지 않는다.
"""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from busstrike.evaluation import verify as cli
from busstrike.evaluation.checks import CheckResult, EXAMPLE_LIMIT, FAIL, NOT_RUN, NOT_VERIFIED
from busstrike.evaluation.report import SCHEMA

from . import tamper
from .test_metrics_recompute import ALL_FIXTURES, FIXTURES

COMPLEX_RUN = os.environ.get('BUSSTRIKE_COMPLEX_RUN')
REQUIRED_TOP_KEYS = ('schema', 'pass', 'fully_verified', 'failed_checks', 'not_verified_checks',
                     'not_run_checks', 'summary', 'generated_at', 'run', 'inputs', 'engine',
                     'verifier', 'settings', 'checks', 'caveats', 'label')
REQUIRED_CHECK_KEYS = ('check_id', 'title', 'status', 'unit', 'examined', 'violation_count',
                       'violation_examples')


def run_cli(*argv):
    """main()을 직접 불러 (종료 코드, 보고서 dict)를 돌려준다."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'verifier_report.json'
        code = cli.main([*argv, '--out', str(out), '--quiet'])
        return code, json.loads(out.read_text(encoding='utf-8'))


class ReportShapeTests(unittest.TestCase):
    def test_report_has_every_required_field(self):
        code, payload = run_cli(str(FIXTURES / 'f2/run'))
        self.assertEqual(0, code)
        for key in REQUIRED_TOP_KEYS:
            self.assertIn(key, payload)
        self.assertEqual(SCHEMA, payload['schema'])
        self.assertEqual([f'V{i}' for i in range(1, 18)],
                         [c['check_id'] for c in payload['checks']])
        for check in payload['checks']:
            for key in REQUIRED_CHECK_KEYS:
                self.assertIn(key, check, check['check_id'])

    def test_records_input_file_digests(self):
        _, payload = run_cli(str(FIXTURES / 'f2/run'))
        self.assertEqual({'network.json', 'demand_bins.csv', 'scenario_manifest.json'},
                         set(payload['inputs']['scenario']))
        self.assertEqual({'events.jsonl', 'passenger_outcomes.csv', 'metrics.json',
                          'measurement.json', 'realization.jsonl'},
                         set(payload['inputs']['run']))
        for digests in payload['inputs'].values():
            for name, digest in digests.items():
                self.assertRegex(digest, r'^[0-9a-f]{64}$', name)

    def test_records_engine_and_verifier_commits(self):
        _, payload = run_cli(str(FIXTURES / 'f2/run'))
        self.assertRegex(payload['engine']['commit'], r'^[0-9a-f]{40}$')
        self.assertRegex(payload['verifier']['commit'], r'^[0-9a-f]{40}$')
        self.assertIsNotNone(payload['verifier']['author'])
        self.assertEqual('docs/verifier_spec.md', payload['verifier']['spec_document'])

    def test_engine_commit_is_quoted_not_verified(self):
        """엔진 커밋은 measurement.json 기록을 인용한 값이다. 검증기가 확인한 값이 아니다."""
        _, payload = run_cli(str(FIXTURES / 'f2/run'))
        self.assertIn('measurement.json', payload['engine']['source'])

    def test_caveats_mention_that_pass_is_not_operational_approval(self):
        _, payload = run_cli(str(FIXTURES / 'f2/run'))
        joined = ' '.join(payload['caveats'])
        self.assertIn('실제 운행 승인', joined)
        self.assertIn('independently_verified', joined)


class AcceptanceTests(unittest.TestCase):
    def test_every_fixture_passes_with_exit_zero(self):
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                code, payload = run_cli(str(FIXTURES / name / 'run'))
                self.assertEqual(0, code)
                self.assertTrue(payload['pass'])
                self.assertEqual([], payload['failed_checks'])

    def test_pass_is_not_the_same_as_fully_verified(self):
        """V16은 자료가 없어 통과할 수 없다. pass=true를 "전부 검증"으로 읽으면 안 된다."""
        for name in ALL_FIXTURES:
            with self.subTest(fixture=name):
                _, payload = run_cli(str(FIXTURES / name / 'run'))
                self.assertTrue(payload['pass'])
                self.assertFalse(payload['fully_verified'])
                self.assertEqual(['V16'], payload['not_verified_checks'])

    def test_any_failure_makes_the_whole_report_fail(self):
        with tamper.tampered('f2', tamper.bump_metric('M1.served', 1),
                             tamper.repair_digests) as base:
            code, payload = run_cli(str(base / 'run'))
        self.assertEqual(1, code)
        self.assertFalse(payload['pass'])
        self.assertFalse(payload['fully_verified'])
        self.assertIn('V9', payload['failed_checks'])
        self.assertGreater(payload['summary'][FAIL], 0)

    def test_single_failure_among_sixteen_passes_still_fails(self):
        """부분 통과를 검증됨으로 쓰지 않는다 (명세 §1 실패 처리)."""
        with tamper.tampered('f2', tamper.bump_metric('M2.refusal_events', -1),
                             tamper.repair_digests) as base:
            code, payload = run_cli(str(base / 'run'))
        self.assertEqual(['V11'], payload['failed_checks'])
        self.assertEqual(1, code)
        self.assertFalse(payload['pass'])


class ScaleLimitTests(unittest.TestCase):
    def test_above_limit_oracle_checks_are_not_run_not_pass(self):
        """재계산 상한을 넘으면 oracle이 필요한 검사를 미실행으로 남긴다 (명세 §4)."""
        code, payload = run_cli(str(FIXTURES / 'f2/run'), '--max-recompute', '2')
        self.assertEqual(0, code)
        self.assertEqual(['V6', 'V7', 'V9', 'V10', 'V11', 'V12', 'V13', 'V14'],
                         payload['not_run_checks'])
        self.assertFalse(payload['fully_verified'])
        skipped = next(c for c in payload['checks'] if c['check_id'] == 'V9')
        self.assertEqual(NOT_RUN, skipped['status'])
        self.assertEqual(0, skipped['examined'])
        self.assertIn('정확도', skipped['note'])

    def test_skip_note_records_the_spec_inconsistency(self):
        """명세 §4는 1만 명 초과에도 V11을 적용한다고 적었으나 V11은 oracle이 필요하다."""
        _, payload = run_cli(str(FIXTURES / 'f2/run'), '--max-recompute', '2')
        note = next(c for c in payload['checks'] if c['check_id'] == 'V11')['note']
        self.assertIn('V11', note)
        self.assertIn('명세 보완', note)

    def test_default_limit_is_ten_thousand(self):
        self.assertEqual(10_000, cli.DEFAULT_RECOMPUTE_LIMIT)


class InputHandlingTests(unittest.TestCase):
    def test_works_without_realization_file(self):
        """--write-realization 없이 실행한 결과도 검증한다 (명세 §2의 (b) 경로)."""
        with tamper.tampered('f2') as base:
            (base / 'run/realization.jsonl').unlink()
            # 내 fixture는 realization digest를 기록하므로 그 기록도 지운다.
            path = base / 'run/measurement.json'
            payload = json.loads(path.read_text())
            payload.pop('realization_sha256', None)
            payload.pop('realization_checksum', None)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            code, report = run_cli(str(base / 'run'))
        self.assertEqual(0, code)
        self.assertFalse(report['run']['has_realization_file'])
        v1 = next(c for c in report['checks'] if c['check_id'] == 'V1')
        self.assertEqual('pass', v1['status'])
        self.assertEqual(9, v1['examined'])
        self.assertIn('checksum 대조는 하지 못했다', v1['note'])

    def test_missing_run_directory_returns_exit_two(self):
        with mock.patch('sys.stderr', new=io.StringIO()) as err:
            code = cli.main(['/tmp/busstrike-does-not-exist', '--out', '-', '--quiet'])
        self.assertEqual(2, code)
        self.assertIn('입력 오류', err.getvalue())

    def test_writes_report_to_stdout_with_dash(self):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            code = cli.main([str(FIXTURES / 'f2/run'), '--out', '-', '--quiet'])
        self.assertEqual(0, code)
        self.assertEqual(SCHEMA, json.loads(out.getvalue())['schema'])

    def test_default_out_path_is_inside_the_run_directory(self):
        with tamper.tampered('f1') as base:
            code = cli.main([str(base / 'run'), '--quiet'])
            self.assertEqual(0, code)
            self.assertTrue((base / 'run/verifier_report.json').is_file())


class ViolationExampleLimitTests(unittest.TestCase):
    def test_report_keeps_only_the_first_twenty_examples(self):
        result = CheckResult('V9', '예시 상한 시험', unit='승객')
        for i in range(25):
            result.add(f'위반 {i}', index=i)
        payload = result.resolve().as_dict()
        self.assertEqual(20, EXAMPLE_LIMIT)
        self.assertEqual(25, payload['violation_count'])
        self.assertEqual(20, len(payload['violation_examples']))
        self.assertEqual(5, payload['violation_examples_truncated'])
        self.assertEqual('위반 0', payload['violation_examples'][0]['message'])

    def test_status_is_resolved_from_violations(self):
        self.assertEqual('pass', CheckResult('V2', 'x').resolve().status)
        failing = CheckResult('V2', 'x')
        failing.add('나쁨')
        self.assertEqual(FAIL, failing.resolve().status)

    def test_explicit_status_survives_resolve(self):
        held = CheckResult('V16', 'x')
        held.status = NOT_VERIFIED
        self.assertEqual(NOT_VERIFIED, held.resolve().status)


@unittest.skipUnless(COMPLEX_RUN, 'BUSSTRIKE_COMPLEX_RUN 미설정')
class ComplexCliTests(unittest.TestCase):
    def test_complex_run_passes_through_the_cli(self):
        code, payload = run_cli(COMPLEX_RUN)
        self.assertEqual(0, code)
        self.assertTrue(payload['pass'])
        self.assertEqual(1000, payload['run']['passengers'])
        self.assertEqual(89_703, payload['run']['events'])
        self.assertEqual([], payload['not_run_checks'])


if __name__ == '__main__':
    unittest.main()
