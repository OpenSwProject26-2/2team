"""승객 실행 상태."""
from dataclasses import dataclass
from .models import Passenger

@dataclass(slots=True)
class PassengerState:
    """물리 상태(physical)와 결과(outcome)를 따로 둔다.

    physical: not_generated → waiting → onboard → arrived.
    outcome: None(미확정) → served / unserved / censored 중 하나.
    여정 기한을 넘긴 승객은 unserved지만 실제로는 계속 타고 가서 arrived가 될 수 있다.
    """
    passenger: Passenger
    physical: str = 'not_generated'
    outcome: str | None = None
    vehicle_id: str | None = None
    boarding_s: int | None = None
    arrival_s: int | None = None
    # unserved 사유(no_connection/service_end/journey/wait). unserved일 때만 값이 있다.
    reason: str | None = None
    # 만석이라 못 탄 횟수. 경험 횟수일 뿐 실패 원인 판정이 아니다.
    refused: int = 0
