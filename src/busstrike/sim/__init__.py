"""M05 셔틀 시뮬레이터 핵심 엔진. 설계: docs/design/simulator.md"""

from .arrivals import build_run_inputs, realize_arrivals, realize_od, u_micro, write_arrivals
from .engine import SimContext, prepare, simulate
from .eventlog import canonical_key, canonical_sort, dumps_events, write_events
from .metrics import compute_metrics
from .models import (
    DemandFlow, Duty, Event, Leg, Passenger, PlanSpec, Policy, RouteSpec, Scenario, SimError, StopTime,
    Timetable, TimetableRoute, Trip, Vehicle, Window,
)
from .run import config_hash, run
from .timetable import assign_vehicles, build_timetable, normalize_plan, planned_cycle, validate_timetable
from .traveltime import TravelTimeTable

__all__ = [
    "DemandFlow", "Duty", "Event", "Leg", "Passenger", "PlanSpec", "Policy", "RouteSpec", "Scenario",
    "SimContext", "SimError", "StopTime", "Timetable", "TimetableRoute", "TravelTimeTable", "Trip",
    "Vehicle", "Window", "assign_vehicles", "build_run_inputs", "build_timetable", "canonical_key",
    "canonical_sort", "compute_metrics", "config_hash", "dumps_events", "normalize_plan", "planned_cycle",
    "prepare", "realize_arrivals", "realize_od", "run", "simulate", "u_micro", "validate_timetable",
    "write_arrivals", "write_events",
]
