"""M00와 연결할 C 내부 초안 모델. 공용 계약의 승인본이 아니다."""

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Literal


class InputError(ValueError):
    def __init__(self, message: str, code: str = "INPUT_INVALID") -> None:
        self.code = code
        super().__init__(message)


def integer(value: int, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise InputError(f"{name}: {minimum} 이상의 정수 필요")


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InputError("비어 있지 않은 문자열 ID 필요")


@dataclass(frozen=True)
class Segment:
    segment_id: str
    origin: str
    destination: str
    travel_s: int
    distance_m: int


@dataclass(frozen=True)
class Route:
    route_id: str
    outbound: tuple[str, ...]
    return_segments: tuple[str, ...]
    original: bool = False


@dataclass(frozen=True)
class Demand:
    demand_id: str
    origin: str
    destination: str
    arrival_s: int
    passenger_count: int
    original_route_id: str


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    capacity: int
    initial_stop: str
    return_stop: str
    available_start_s: int
    available_end_s: int


@dataclass(frozen=True)
class Policy:
    start_s: int
    end_s: int
    dwell_s: int
    turnaround_s: int
    max_wait_s: int
    max_travel_s: int


@dataclass(frozen=True)
class Inputs:
    input_bundle_id: str
    scenario_id: str
    dataset_version: str
    network_version: str
    demand_version: str
    conditions_version: str
    service_date: str
    split: Literal["development", "evaluation"]
    scope_mode: Literal["single_bus_leg"]
    stops: tuple[str, ...]
    segments: tuple[Segment, ...]
    allowed_turns: tuple[tuple[str, str], ...]
    turnaround_stops: tuple[str, ...]
    routes: tuple[Route, ...]
    demands: tuple[Demand, ...]
    fleet: tuple[Vehicle, ...]
    policy: Policy

    def __post_init__(self) -> None:
        for value in (self.input_bundle_id, self.scenario_id, self.dataset_version,
                      self.network_version, self.demand_version, self.conditions_version):
            identifier(value)
        try:
            date.fromisoformat(self.service_date)
        except (TypeError, ValueError) as exc:
            raise InputError("service_date는 ISO 날짜여야 함") from exc
        if self.scope_mode != "single_bus_leg":
            raise InputError("첫 버전은 single_bus_leg만 지원", "OUT_OF_SCOPE")
        if self.split not in ("development", "evaluation"):
            raise InputError("지원하지 않는 데이터 split")
        for ids in (self.stops, tuple(s.segment_id for s in self.segments),
                    tuple(r.route_id for r in self.routes),
                    tuple(d.demand_id for d in self.demands),
                    tuple(v.vehicle_id for v in self.fleet)):
            for value in ids:
                identifier(value)
            if len(ids) != len(set(ids)):
                raise InputError("중복 ID")
        if not self.stops:
            raise InputError("정류장이 필요함")
        stop_set = set(self.stops)
        segment_ids = {s.segment_id for s in self.segments}
        for segment in self.segments:
            if {segment.origin, segment.destination} - stop_set:
                raise InputError("구간의 정류장 참조 오류")
            integer(segment.travel_s, "travel_s", 1)
            integer(segment.distance_m, "distance_m")
        for turn in self.allowed_turns:
            if len(turn) != 2 or set(turn) - segment_ids:
                raise InputError("허용 회전 참조 오류")
        if set(self.turnaround_stops) - stop_set:
            raise InputError("회차 지점 참조 오류")
        for key, value in asdict(self.policy).items():
            integer(value, key)
        if self.policy.end_s <= self.policy.start_s:
            raise InputError("운영 종료는 시작 이후여야 함")
        for route in self.routes:
            if type(route.original) is not bool:
                raise InputError("original은 bool이어야 함")
        original_ids = {r.route_id for r in self.routes if r.original}
        for demand in self.demands:
            integer(demand.arrival_s, "arrival_s")
            integer(demand.passenger_count, "passenger_count")
            if {demand.origin, demand.destination} - stop_set:
                raise InputError("수요 정류장 참조 오류")
            if demand.origin == demand.destination:
                raise InputError("동일 출도착 수요는 전처리에서 분리해야 함")
            if demand.original_route_id not in original_ids:
                raise InputError("수요 원래 노선 참조 오류")
        for vehicle in self.fleet:
            integer(vehicle.capacity, "capacity", 1)
            integer(vehicle.available_start_s, "available_start_s")
            integer(vehicle.available_end_s, "available_end_s")
            if vehicle.available_end_s <= vehicle.available_start_s:
                raise InputError("차량 가용시간 오류")
            if {vehicle.initial_stop, vehicle.return_stop} - stop_set:
                raise InputError("차량 위치 참조 오류")
            if vehicle.initial_stop != vehicle.return_stop:
                raise InputError("첫 버전은 동일 거점 복귀만 지원", "OUT_OF_SCOPE")
        signatures = {(v.capacity, v.initial_stop, v.return_stop,
                       v.available_start_s, v.available_end_s) for v in self.fleet}
        if len(signatures) > 1:
            raise InputError("첫 버전은 동일 조건 차량만 지원", "OUT_OF_SCOPE")

    @property
    def checksum(self) -> str:
        content = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode()).hexdigest()

    @property
    def total_demand(self) -> int:
        return sum(d.passenger_count for d in self.demands)


@dataclass(frozen=True)
class Plan:
    plan_id: str
    scenario_id: str
    revision: int
    source: str
    input_checksum: str
    allocation: tuple[tuple[str, int], ...]

    def to_service_plan(self, inputs: Inputs) -> dict:
        from .candidates import validate_allocation

        validate_allocation(inputs, self)
        routes = {r.route_id: r for r in inputs.routes}
        return {
            "plan_id": self.plan_id, "scenario_id": self.scenario_id,
            "revision": self.revision, "status": "draft",
            "plan_mode": "simulation", "source": self.source,
            "input_bundle_id": inputs.input_bundle_id,
            "input_checksum": self.input_checksum,
            "input_versions": {k: getattr(inputs, k) for k in
                               ("dataset_version", "network_version", "demand_version", "conditions_version")},
            "routes": [{**asdict(routes[r]), "vehicle_count": n}
                       for r, n in self.allocation if n],
            "schedule_policy": "uniform", "schedule_status": "pending_b",
            "vehicle_duties": [], "trips": [],
        }


def make_plan(inputs: Inputs, allocation: dict[str, int], source: str) -> Plan:
    normalized = tuple(sorted(allocation.items()))
    token = json.dumps([inputs.checksum, source, normalized], separators=(",", ":"))
    plan_id = "plan-" + hashlib.sha256(token.encode()).hexdigest()[:20]
    return Plan(plan_id, inputs.scenario_id, 1, source, inputs.checksum, normalized)


def load_inputs(path: str | Path) -> Inputs:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw["stops"] = tuple(raw["stops"])
        raw["turnaround_stops"] = tuple(raw["turnaround_stops"])
        raw["allowed_turns"] = tuple(tuple(t) for t in raw["allowed_turns"])
        raw["segments"] = tuple(Segment(**s) for s in raw["segments"])
        raw["routes"] = tuple(Route(**{**r, "outbound": tuple(r["outbound"]),
                                      "return_segments": tuple(r["return_segments"])})
                              for r in raw["routes"])
        raw["demands"] = tuple(Demand(**d) for d in raw["demands"])
        raw["fleet"] = tuple(Vehicle(**v) for v in raw["fleet"])
        raw["policy"] = Policy(**raw["policy"])
        return Inputs(**raw)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"입력 형식 오류: {exc}") from exc
