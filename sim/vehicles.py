from dataclasses import dataclass, field

@dataclass
class VehicleState:
    capacity: int
    onboard: dict = field(default_factory=dict)
    def check(self):
        if not 0 <= len(self.onboard) <= self.capacity:
            raise RuntimeError('capacity invariant violated')
