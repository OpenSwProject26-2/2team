"""검사 결과 자료형. 검사 하나가 CheckResult 하나를 돌려준다."""
from dataclasses import dataclass, field

# 보고서에 남기는 위반 예시 개수 (명세 §7 "앞 20건 위반 예")
EXAMPLE_LIMIT = 20

PASS = 'pass'
FAIL = 'fail'
NOT_RUN = 'not_run'            # 의도적으로 실행하지 않음(규모 상한 등)
NOT_VERIFIED = 'not_verified'  # 자료가 없어 검증 불가. 통과가 아니다(명세 V16 R13)


@dataclass(frozen=True)
class Violation:
    """위반 1건. where에는 재현에 필요한 최소 정보만 담는다."""
    message: str
    where: dict

    def as_dict(self):
        return {'message': self.message, 'where': self.where}


@dataclass
class CheckResult:
    """검사 1건의 결과.

    examined: 실제로 검사한 대상 수(사건 수·승객 수·방문 수 등. 단위는 unit에 적는다).
    violations: 발견한 위반 전체. 보고서에는 앞 EXAMPLE_LIMIT건만 싣는다.
    """
    check_id: str
    title: str
    unit: str = ''
    examined: int = 0
    violations: list = field(default_factory=list)
    status: str = ''
    note: str = ''

    def add(self, message, **where):
        self.violations.append(Violation(message, where))

    def resolve(self):
        """status를 아직 정하지 않았으면 위반 수로 정한다."""
        if not self.status:
            self.status = FAIL if self.violations else PASS
        return self

    @property
    def passed(self):
        return self.status == PASS

    def as_dict(self):
        out = {'check_id': self.check_id, 'title': self.title, 'status': self.status,
               'unit': self.unit, 'examined': self.examined,
               'violation_count': len(self.violations),
               'violation_examples': [v.as_dict() for v in self.violations[:EXAMPLE_LIMIT]]}
        if len(self.violations) > EXAMPLE_LIMIT:
            out['violation_examples_truncated'] = len(self.violations) - EXAMPLE_LIMIT
        if self.note:
            out['note'] = self.note
        return out
