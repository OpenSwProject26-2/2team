"""M05 internal immutable models; not approved M00 DTOs."""
from dataclasses import dataclass

class SimulationError(ValueError):
    code = "INPUT_INVALID"

def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise SimulationError("invalid integer")

@dataclass(frozen=True, slots=True)
class Passenger:
    passenger_id: str
    origin: str
    destination: str
    time_s: int

@dataclass(frozen=True)
class Trip:
    trip_id: str
    vehicle_id: str
    capacity: int
    stops: tuple[str, ...]
    arrivals: tuple[int, ...]
    departures: tuple[int, ...]
    return_s: int
    distance_m: int

@dataclass(frozen=True)
class Config:
    max_wait_s: int
    max_journey_s: int
    drain_s: int
    boarding_policy: str
    travel_policy: str
    def __post_init__(self):
        for x in (self.max_wait_s, self.max_journey_s, self.drain_s): integer(x)
        if self.boarding_policy != 'fifo' or self.travel_policy != 'fixed_segments':
            raise SimulationError("unsupported explicit policy")
