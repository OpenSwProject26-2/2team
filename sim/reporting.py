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
    # P4 raw counters (defaults keep older constructors valid).
    refusal_count: int = 0
    vehicle_stats: tuple['VehicleStats', ...] = ()
    # Set only when passengers were streamed to an outcome sink (passengers == ()).
    streamed_total: int | None = None
    @property
    def total(self): return len(self.passengers) if self.streamed_total is None else self.streamed_total

@dataclass(frozen=True)
class VehicleStats:
    """Per-vehicle M6 counters within drain_s.

    service_time_s / distance_m: completed duty cycles only (return_s <= drain_s), same
    rule as Result.distance_m. empty_time_s: legs that left a stop with zero occupancy and
    arrived by drain_s, plus dead-head returns of completed cycles. Empty *distance* is not
    computable because Trip has no per-segment distance (TODO: needs segment distances).
    """
    vehicle_id: str
    trips_completed: int
    boarded: int
    served_alighted: int
    service_time_s: int
    distance_m: int
    empty_time_s: int

def p90(values):
    return sorted(values)[ceil(len(values)*.9)-1] if values else None
