from dataclasses import dataclass, asdict
from datetime import date
import hashlib
import json
from .models import Passenger, SimulationError, integer

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

@dataclass(frozen=True)
class RunInputs:
    input_bundle_id: str
    scenario_id: str
    source_checksum: str
    service_date: str
    passengers: tuple[Passenger, ...]
    travel_times: tuple[tuple[str, int], ...]
    seed: str
    def __post_init__(self):
        date.fromisoformat(self.service_date)
        if any(not isinstance(x, str) or not x.strip() for x in
               (self.input_bundle_id, self.scenario_id, self.source_checksum, self.seed)):
            raise SimulationError('missing provenance')
        ids = set()
        for p in self.passengers:
            if not p.passenger_id or p.passenger_id in ids or not p.origin or not p.destination or p.origin == p.destination:
                raise SimulationError('invalid passenger or duplicate ID')
            ids.add(p.passenger_id)
            integer(p.time_s)
        if len(dict(self.travel_times)) != len(self.travel_times):
            raise SimulationError('duplicate segment realization')
        for sid, value in self.travel_times:
            if not sid: raise SimulationError('missing segment ID')
            integer(value, 1)
    @property
    def checksum(self): return digest(asdict(self))

def expand(rows):
    """Rows: (aggregate ID, origin, destination, exact arrival_s, count)."""
    result = []
    seen = set()
    for key, origin, destination, arrival, count in sorted(rows):
        integer(count)
        integer(arrival)
        if key in seen: raise SimulationError('duplicate aggregate ID')
        seen.add(key)
        for i in range(count):
            result.append(Passenger(json.dumps([key, i], separators=(',', ':')), origin, destination, arrival))
    return tuple(result)
