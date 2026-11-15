"""M05 시뮬레이터 데이터 모델(simulator.md §0.3, §1.5, §3.1, §6.1, m07-output-contract.md).

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
    # M07 출력용(선택). drain_s가 없으면 W_e + max_journey_s(§6.2). 나머지 둘은 없으면 M07이 미검증으로 둔다.
    drain_s: int | None = None
    max_headway_s: int | None = None
    wait_threshold_s: int | None = None


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
    stop_area: tuple[tuple[str, str], ...] = ()   # (stop_id, area). M07 M5 지역(없으면 unknown)


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
    """승객 1명. M07 계약상 출발 정류장 1개 → 목적 정류장 1개(m07-output-contract.md §2.2, I9).

    `passenger_id`는 `json.dumps([aggregate_id, index], separators=(",", ":"))`, `arrival_s`는 M07의 time_s.
    """

    passenger_id: str
    origin: str
    destination: str
    arrival_s: int


@dataclass(frozen=True)
class BinRow:
    """`demand_bins.csv` 1행: 집계 수요 1개(m07-output-contract.md §2.2)."""

    aggregate_id: str
    origin: str
    destination: str
    bin_start_s: int
    bin_width_s: int
    passenger_count: int


@dataclass(frozen=True)
class DemandFlow:
    """실현기 입력 한 행(§2.3). access는 stop → station_stop, egress는 station_stop → stop.

    역에 딸린 정류장 중 어느 것에 붙일지는 상류(M03)가 운행안과 무관하게 정해 준다(가정 A25).
    flow는 그 시간대의 유량(명). 문자열·정수·Decimal을 권장한다(float도 str()을 거쳐 받는다).
    """

    kind: str
    stop_id: str
    station_stop_id: str
    bin_start_s: int
    flow: Any


@dataclass(frozen=True)
class ServiceTrip:
    """M07 `network.json`의 운행편 1개 = 영업 leg 1개 + 뒤따르는 복귀 공차(m07-output-contract.md §2.1).

    departures[i]는 그 정류장을 떠나는 시각(마지막 정류장은 도착과 같다, I3), return_s는 복귀 공차가
    끝나 출발 거점에 돌아온 시각(I1), distance_m은 영업 + 복귀 공차 거리(I2).
    """

    trip_id: str
    route_id: str
    vehicle_id: str
    capacity: int
    stops: tuple[str, ...]
    arrivals: tuple[int, ...]
    departures: tuple[int, ...]
    return_s: int
    distance_m: int

    def stable_id(self, index: int) -> str:
        return f"{self.trip_id}:{index:08d}"


# M07 사건 로그 키 순서(m07-output-contract.md §5). 값이 없으면 null로 쓴다.
EVENT_KEYS = ("time_s", "phase", "stable_id", "kind", "passenger_id", "vehicle_id", "occupancy")

PHASE_ALIGHT, PHASE_GENERATE, PHASE_BOARD, PHASE_DEPART, PHASE_EXPIRE = 0, 1, 2, 3, 4


@dataclass(slots=True)
class Event:
    """M07 사건 1건(m07-output-contract.md §2.4)."""

    time_s: int
    phase: int
    stable_id: str
    kind: str
    passenger_id: str | None = None
    vehicle_id: str | None = None
    occupancy: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in EVENT_KEYS}
