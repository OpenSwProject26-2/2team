from dataclasses import dataclass
from math import ceil
from .passengers import PassengerState

@dataclass(frozen=True)
class Result:
    plan_id: str
    plan_revision: int
    input_bundle_id: str
    source_checksum: str
    run_checksum: str
    config_hash: str
    passengers: tuple[PassengerState, ...]
    served: int
    unserved: int
    censored: int
    p90_wait_s: int | None
    distance_m: int
    event_count: int
    @property
    def total(self): return len(self.passengers)

def p90(values):
    return sorted(values)[ceil(len(values)*.9)-1] if values else None
