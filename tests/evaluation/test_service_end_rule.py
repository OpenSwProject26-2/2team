"""V16 R14 운영 종료 규칙(network.json spec.service_end_rule).

F1 fixture 운행편: v1 [첫 도착 0, 복귀 10], [첫 도착 10, 복귀 20]. spec만 바꾸고 check_fleet_rules만 본다
(network.json을 바꾸면 V17 체크섬은 깨지므로 여기서는 보지 않는다).
"""
import json
import unittest

from busstrike.evaluation import inputs
from busstrike.evaluation.checks import FAIL, NOT_VERIFIED
from busstrike.evaluation.guards import check_fleet_rules

from . import tamper

NEW_RULE = 'start_within_window_return_by_available'


def with_spec(**fields):
    def mutate(base):
        path = base / 'scenario/network.json'
        doc = json.loads(path.read_text(encoding='utf-8'))
        doc['spec'].update(fields)
        path.write_text(json.dumps(doc, sort_keys=True), encoding='utf-8')
    return mutate


def r14(**fields):
    with tamper.tampered('f1', with_spec(**fields)) as base:
        result = check_fleet_rules(inputs.Scenario(base / 'scenario'), inputs.Run(base / 'run'))
    return result.status, [v.message for v in result.violations]


class R14ServiceEndRuleTests(unittest.TestCase):
    def test_default_rule_uses_window_end(self):
        self.assertEqual(r14(service_start_s=0, service_end_s=20)[0], NOT_VERIFIED)
        status, _ = r14(service_start_s=0, service_end_s=15)                 # 복귀 20 > 15
        self.assertEqual(status, FAIL)
        status, _ = r14(service_start_s=0, service_end_s=15, service_end_rule='cycle_within_window')
        self.assertEqual(status, FAIL)

    def test_new_rule_accepts_return_after_window_end(self):
        status, violations = r14(service_start_s=0, service_end_s=15, service_end_rule=NEW_RULE,
                                 vehicle_available_end_s={'v1': 20})
        self.assertEqual(status, NOT_VERIFIED, violations)

    def test_new_rule_start_must_be_before_window_end(self):
        status, _ = r14(service_start_s=0, service_end_s=10, service_end_rule=NEW_RULE,
                        vehicle_available_end_s={'v1': 20})                 # 두 번째 운행편 시작 10 = 끝
        self.assertEqual(status, FAIL)

    def test_new_rule_return_by_vehicle_available_end(self):
        status, _ = r14(service_start_s=0, service_end_s=15, service_end_rule=NEW_RULE,
                        vehicle_available_end_s={'v1': 19})
        self.assertEqual(status, FAIL)

    def test_new_rule_needs_available_end(self):
        self.assertEqual(r14(service_start_s=0, service_end_s=15, service_end_rule=NEW_RULE)[0], FAIL)
        self.assertEqual(r14(service_start_s=0, service_end_s=15, service_end_rule=NEW_RULE,
                             vehicle_available_end_s={'v2': 20})[0], FAIL)

    def test_unknown_rule_fails(self):
        self.assertEqual(r14(service_start_s=0, service_end_s=20, service_end_rule='other')[0], FAIL)


if __name__ == '__main__':
    unittest.main()
