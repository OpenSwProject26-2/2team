"""운행안 비교 계산기 테스트.

이 도구의 핵심은 표를 그리는 것이 아니라 **비교가 성립하지 않을 때 막는 것**이다.
그래서 막는 쪽을 먼저 시험한다.
"""
import unittest

from busstrike.evaluation.compare import (RunRecord, build, check_conditions, group_samples,
                                          render, summarize_group)


def rec(run_id, plan, served=60, unserved=40, censored=0, total=100, **kw):
    base = dict(demand='d1', vehicles=11, policy=(1200, 3600, 36000), network='n1',
                served=served, unserved=unserved, censored=censored, total=total,
                verified=True)
    base.update(kw)
    return RunRecord(run_id=run_id, plan_id=plan, **base)


class FairnessTests(unittest.TestCase):
    """공정 비교 조건 C1–C6."""

    def test_same_conditions_pass(self):
        problems, shared = check_conditions([rec('a', 'plan-01'), rec('b', 'plan-02')])
        self.assertEqual([], problems)
        self.assertEqual(11, shared['vehicles'])

    def test_different_vehicle_count_blocks_comparison(self):
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02', vehicles=20)])
        self.assertFalse(result['comparable'])
        self.assertIn('C2', result['fairness_problems'][0]['condition'])

    def test_different_demand_blocks_comparison(self):
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02', demand='d2')])
        self.assertFalse(result['comparable'])
        self.assertIn('C1', result['fairness_problems'][0]['condition'])

    def test_different_policy_blocks_comparison(self):
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02', policy=(600, 3600, 36000))])
        self.assertFalse(result['comparable'])
        self.assertIn('C4', result['fairness_problems'][0]['condition'])

    def test_missing_condition_is_reported_not_assumed(self):
        """값이 없으면 같다고 치지 않는다."""
        problems, _ = check_conditions([rec('a', 'plan-01'), rec('b', 'plan-02', vehicles=None)])
        self.assertTrue(any('C2' in p['condition'] for p in problems))

    def test_blocked_comparison_renders_reason_not_table(self):
        out = render(build([rec('a', 'plan-01'), rec('b', 'plan-02', vehicles=20)]))
        self.assertIn('공정 비교 조건이 깨져', out)
        self.assertNotIn('충족률', out)

    def test_allow_unfair_is_explicit(self):
        """조건이 달라도 강제로 비교하려면 명시해야 한다."""
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02', vehicles=20)], allow_unfair=True)
        self.assertTrue(result['comparable'])
        self.assertTrue(result['fairness_problems'])   # 문제는 그대로 남는다


class UncertaintyTests(unittest.TestCase):
    """불확실성을 숨기지 않는다 (§5.5)."""

    def test_censored_becomes_a_range(self):
        r = rec('a', 'plan-01', served=60, unserved=30, censored=10)
        self.assertEqual((0.60, 0.70), r.rate_bounds)

    def test_no_censored_gives_a_point(self):
        lo, hi = rec('a', 'plan-01').rate_bounds
        self.assertEqual(lo, hi)

    def test_zero_total_is_none_not_zero(self):
        self.assertEqual((None, None), rec('a', 'p', served=0, unserved=0, total=0).rate_bounds)

    def test_multiple_samples_report_median_and_range(self):
        samples = [rec(f's{i}', 'gpt', served=s, unserved=100 - s)
                   for i, s in enumerate((50, 60, 70))]
        out = summarize_group('gpt', samples)
        self.assertEqual(3, out['samples'])
        self.assertAlmostEqual(0.60, out['rate_low'])
        self.assertEqual([0.50, 0.70], out['rate_low_range'])

    def test_single_sample_has_no_range(self):
        self.assertIsNone(summarize_group('ours', [rec('a', 'ours')])['rate_low_range'])


class VerificationTests(unittest.TestCase):
    """검증 전 수치를 검증된 것처럼 보이지 않게 한다 (§7.3)."""

    def test_unverified_run_marks_whole_result_provisional(self):
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02', verified=False)])
        self.assertEqual('독립 검증 전 잠정', result['label'])
        self.assertEqual(['b'], result['unverified_runs'])

    def test_all_verified_drops_the_provisional_label(self):
        self.assertEqual('독립 검증 통과',
                         build([rec('a', 'plan-01'), rec('b', 'plan-02')])['label'])

    def test_render_warns_about_unverified(self):
        out = render(build([rec('a', 'plan-01'), rec('b', 'plan-02', verified=False)]))
        self.assertIn('독립 검증 전 잠정', out)
        self.assertIn('비교 결론에 쓰지 말 것', out)

    def test_group_is_unverified_if_any_sample_is(self):
        out = summarize_group('gpt', [rec('a', 'gpt'), rec('b', 'gpt', verified=False)])
        self.assertFalse(out['verified'])
        self.assertEqual(['b'], out['unverified_samples'])


class AssumptionShareTests(unittest.TestCase):
    """추정으로 채운 구간 비중을 함께 낸다 (U3)."""

    def test_share_is_computed_from_segment_counts(self):
        r = rec('a', 'plan-01', segments_observed=77, segments_synthesized=6,
                segments_estimated=20)
        self.assertAlmostEqual(20 / 103, r.assumption_share)

    def test_missing_counts_give_none_not_zero(self):
        self.assertIsNone(rec('a', 'plan-01', segments_observed=77).assumption_share)


class OriginTests(unittest.TestCase):
    """출처는 비교가 끝난 뒤에만 붙인다 (C5)."""

    def test_origin_attached_only_from_manifest(self):
        result = build([rec('a', 'plan-01'), rec('b', 'plan-02')],
                       manifest={'plans': {'plan-01': {'origin': 'municipal'},
                                           'plan-02': {'origin': 'ours'}}})
        self.assertEqual({'municipal', 'ours'}, {r['origin'] for r in result['rows']})

    def test_no_manifest_means_no_origin(self):
        result = build([rec('a', 'plan-01')])
        self.assertNotIn('origin', result['rows'][0])

    def test_run_record_has_no_origin_field_by_default(self):
        """실행 기록 자체에는 출처가 비어 있어야 한다."""
        self.assertEqual({}, rec('a', 'plan-01').source)


class GroupingTests(unittest.TestCase):
    def test_samples_group_by_plan(self):
        groups = group_samples([rec('a', 'gpt'), rec('b', 'gpt'), rec('c', 'ours')])
        self.assertEqual({'gpt': 2, 'ours': 1}, {k: len(v) for k, v in groups.items()})


if __name__ == '__main__':
    unittest.main()
