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
