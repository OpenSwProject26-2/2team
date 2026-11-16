"""M05 셔틀 시뮬레이터 핵심 엔진. 설계: docs/design/simulator.md, 출력 계약: docs/design/m07-output-contract.md"""

from .arrivals import (
    bin_counts, build_run_inputs, generate, realize_bins, realize_od, u_micro, write_demand_bins,
)
from .engine import SimContext, SimResult, prepare, service_schedule, simulate
from .eventlog import dumps_events, outcomes_bytes, write_events
from .gtfs import build_gtfs_files, export_gtfs, read_gtfs
from .metrics import compute_metrics, passenger_outcomes
from .models import (
    BinRow, DemandFlow, Duty, Event, Leg, Passenger, PlanSpec, Policy, RouteSpec, Scenario, ServiceTrip,
    SimError, StopTime, Timetable, TimetableRoute, Trip, Vehicle, Window,
)
from .run import config_hash, run, simulate_context
from .timetable import assign_vehicles, build_timetable, normalize_plan, planned_cycle, validate_timetable
from .traveltime import TravelTimeTable

__all__ = [
    "BinRow", "DemandFlow", "Duty", "Event", "Leg", "Passenger", "PlanSpec", "Policy", "RouteSpec", "Scenario",
    "ServiceTrip", "SimContext", "SimError", "SimResult", "StopTime", "Timetable", "TimetableRoute",
    "TravelTimeTable", "Trip", "Vehicle", "Window", "assign_vehicles", "bin_counts", "build_gtfs_files",
    "build_run_inputs", "build_timetable", "compute_metrics", "config_hash", "dumps_events", "export_gtfs",
    "generate", "normalize_plan", "outcomes_bytes", "passenger_outcomes", "planned_cycle", "prepare",
    "read_gtfs", "realize_bins", "realize_od", "run", "service_schedule", "simulate", "simulate_context",
    "u_micro", "validate_timetable", "write_demand_bins", "write_events",
]
