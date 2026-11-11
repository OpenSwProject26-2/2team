"""차량 실행 상태."""
from dataclasses import dataclass, field

@dataclass
class VehicleState:
    """현재 탑승자(passenger_id → 상태)와 정원."""
    capacity: int
    onboard: dict = field(default_factory=dict)
    def check(self):
        """정원 초과면 즉시 실패한다(엔진 불변조건)."""
        if not 0 <= len(self.onboard) <= self.capacity:
            raise RuntimeError('capacity invariant violated')
