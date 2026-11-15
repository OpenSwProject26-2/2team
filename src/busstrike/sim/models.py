"""M05 시뮬레이터 데이터 모델(simulator.md §0.3, §1.5, §3.1, §6.1).

모든 시간은 service_date 자정 기준 정수 초, 거리는 m, 인원은 정수다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SimError(Exception):
    """M00 §4 오류 코드를 담는 단일 예외(simulator.md §0.4)."""

    INPUT_INVALID = "INPUT_INVALID"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    PLAN_INFEASIBLE = "PLAN_INFEASIBLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True)
class Window:
    start_s: int
    end_s: int


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    vehicle_class: str
    seat_capacity: int
    standing_capacity: int
    available_start_s: int
    available_end_s: int
    initial_stop_id: str | None = None
    end_stop_id: str | None = None

    @property
    def capacity(self) -> int:
        return self.seat_capacity + self.standing_capacity


@dataclass(frozen=True)
class Policy:
    max_wait_s: int
    max_journey_s: int
    allowed_transfers: int
    arrival_method: str
    dwell_base_s: int
    dwell_per_boarding_s: int
    dwell_per_alighting_s: int
    turnaround_s: int
    cycle_rest_s: int
    unknown_link: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    service_date: str
    timezone: str
    network_snapshot_id: str
    demand_profile_id: str
    strike_route_ids: tuple[str, ...]
    window: Window
    authorized_count: int
    vehicles: tuple[Vehicle, ...]
    policy: Policy


@dataclass(frozen=True)
class Leg:
    in_service: bool
    stop_ids: tuple[str, ...]


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    legs: tuple[Leg, ...]
    vehicle_count: int
    vehicle_ids: tuple[str, ...] | None = None

    @property
    def anchor(self) -> str:
        return self.legs[0].stop_ids[0]


@dataclass(frozen=True)
class StopTime:
    stop_seq: int
    stop_id: str
    sched_s: int


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    vehicle_id: str
    cycle_index: int
    leg_index: int
    in_service: bool
    stop_times: tuple[StopTime, ...]

    @property
    def stop_ids(self) -> tuple[str, ...]:
        return tuple(st.stop_id for st in self.stop_times)


@dataclass(frozen=True)
class Duty:
    vehicle_id: str
    route_id: str
    trip_ids: tuple[str, ...]
    start_s: int
    end_s: int


@dataclass(frozen=True)
class TimetableRoute:
    route_id: str
    period_s: int | None
    vehicle_ids: tuple[str, ...]


@dataclass(frozen=True)
class Timetable:
    plan_id: str
    plan_revision: int
    origin: str  # "generated" | "provided"
    routes: tuple[TimetableRoute, ...]
    trips: tuple[Trip, ...]
    duties: tuple[Duty, ...]
    schema_version: int = 1


@dataclass(frozen=True)
class PlanSpec:
    """M00 ServicePlan 중 M05가 읽는 부분. 운행안 출처 필드는 일부러 담지 않는다(§1.6)."""

    plan_id: str
    scenario_id: str
    revision: int
    status: str
    routes: tuple[RouteSpec, ...]
    trips: tuple[Trip, ...] = ()
    duties: tuple[Duty, ...] = ()


@dataclass(frozen=True)
class Passenger:
    passenger_id: str
    kind: str  # "access" | "egress"
    origin_stop_ids: tuple[str, ...]
    dest_stop_ids: tuple[str, ...]
    arrival_s: int
    max_wait_s: int


@dataclass(frozen=True)
class DemandFlow:
    """실현기 입력 한 행(§2.3). access는 stop→station, egress는 station→stop.

    flow는 그 시간대의 유량(명). 문자열·정수·Decimal을 권장한다(float도 str()을 거쳐 받는다).
    """

    kind: str
    stop_id: str
    station_id: str
    bin_start_s: int
    flow: Any


# 이벤트 로그 키 순서(§6.1). 값이 None인 키는 쓰지 않는다.
EVENT_KEYS = (
    "seq", "t", "type", "vehicle_id", "trip_id", "visit", "stop_id", "stop_seq",
    "passenger_id", "alighted", "boarded", "left_behind", "load", "cause",
)


@dataclass(slots=True)
class Event:
    t: int
    type: str
    vehicle_id: str | None = None
    trip_id: str | None = None
    visit: int | None = None
    stop_id: str | None = None
    stop_seq: int | None = None
    passenger_id: str | None = None
    alighted: int | None = None
    boarded: int | None = None
    left_behind: int | None = None
    load: int | None = None
    cause: str | None = None
    seq: int | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in EVENT_KEYS:
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out
