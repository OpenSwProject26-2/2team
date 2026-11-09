from dataclasses import dataclass
from .models import Passenger

@dataclass
class PassengerState:
    passenger: Passenger
    physical: str = 'not_generated'
    outcome: str | None = None
    vehicle_id: str | None = None
    boarding_s: int | None = None
    arrival_s: int | None = None
    # Exclusive unserved reason (see sim.metrics.REASON_PRIORITY); None unless unserved.
    reason: str | None = None
    # Vehicle visits at which this passenger was eligible but left behind because the
    # vehicle was full. A count of experiences, not a cause of failure.
    refused: int = 0
