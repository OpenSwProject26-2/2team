"""입력 검증·직렬화(simulator.md §0.2–§0.4, §1.5).

직렬화 규칙: `json.dumps(x, ensure_ascii=False, separators=(",", ":"))`, UTF-8, 줄 끝 `\\n`.
구조 문서(JSON)는 `sort_keys=True`, 행 문서(JSONL)는 키 순서를 호출자가 정한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import (
    Duty, Leg, Passenger, PlanSpec, Policy, RouteSpec, Scenario, SimError, StopTime,
    Timetable, TimetableRoute, Trip, Vehicle, Window,
)

_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
_SORTED_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


# ---------------------------------------------------------------- 직렬화·해시

def canonical_json(x: Any) -> str:
    return _SORTED_ENCODER.encode(x)


def dumps_line(x: Any) -> str:
    """키 순서를 그대로 두는 한 줄 JSON."""
    return _ENCODER.encode(x)


def json_bytes(x: Any) -> bytes:
    """구조 문서 파일 바이트(정렬 키, 마지막 줄 `\\n`)."""
    return (canonical_json(x) + "\n").encode("utf-8")


def jsonl_bytes(rows: Iterable[Any]) -> bytes:
    return "".join(dumps_line(r) + "\n" for r in rows).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def write_bytes(path: Path, data: bytes) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SimError(SimError.INPUT_INVALID, f"cannot read JSON {Path(path).name}") from exc


def read_jsonl(path: Path) -> list[Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]
    except (OSError, ValueError) as exc:
        raise SimError(SimError.INPUT_INVALID, f"cannot read JSONL {Path(path).name}") from exc


# ---------------------------------------------------------------- 필드 검사

def _fail(message: str, **details: Any) -> SimError:
    return SimError(SimError.INPUT_INVALID, message, details)


def _get(raw: dict, name: str) -> Any:
    if not isinstance(raw, dict) or name not in raw:
        raise _fail(f"missing field {name}", field=name)
    return raw[name]


def _int(raw: dict, name: str, minimum: int | None = None) -> int:
    v = _get(raw, name)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _fail(f"{name} must be int", field=name)
    if minimum is not None and v < minimum:
        raise _fail(f"{name} must be >= {minimum}", field=name)
    return v


def _str(raw: dict, name: str) -> str:
    v = _get(raw, name)
    if not isinstance(v, str) or not v:
        raise _fail(f"{name} must be non-empty string", field=name)
    return v


def _opt_str(raw: dict, name: str) -> str | None:
    v = raw.get(name)
    if v is None:
        return None
    if not isinstance(v, str) or not v:
        raise _fail(f"{name} must be string or null", field=name)
    return v


def _str_list(raw: dict, name: str, *, sorted_unique: bool = False, min_len: int = 0) -> tuple[str, ...]:
    v = _get(raw, name)
    if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
        raise _fail(f"{name} must be a list of strings", field=name)
    if len(v) < min_len:
        raise _fail(f"{name} needs at least {min_len} items", field=name)
    if sorted_unique and list(v) != sorted(set(v)):
        raise _fail(f"{name} must be sorted and unique", field=name)
    return tuple(v)


# ---------------------------------------------------------------- scenario.json

def window_from_dict(raw: dict) -> Window:
    w = Window(_int(raw, "start_s"), _int(raw, "end_s"))
    if not w.start_s < w.end_s:
        raise _fail("operating_window requires start_s < end_s")
    return w


def vehicle_from_dict(raw: dict) -> Vehicle:
    v = Vehicle(
        vehicle_id=_str(raw, "vehicle_id"),
        vehicle_class=_str(raw, "vehicle_class"),
        seat_capacity=_int(raw, "seat_capacity", 1),
        standing_capacity=_int(raw, "standing_capacity", 0),
        available_start_s=_int(raw, "available_start_s"),
        available_end_s=_int(raw, "available_end_s"),
        initial_stop_id=_opt_str(raw, "initial_stop_id"),
        end_stop_id=_opt_str(raw, "end_stop_id"),
    )
    if v.available_end_s < v.available_start_s:
        raise _fail("available_end_s < available_start_s", vehicle_id=v.vehicle_id)
    return v


def policy_from_dict(raw: dict) -> Policy:
    p = Policy(
        max_wait_s=_int(raw, "max_wait_s", 0),
        max_journey_s=_int(raw, "max_journey_s", 0),
        allowed_transfers=_int(raw, "allowed_transfers", 0),
        arrival_method=_str(raw, "arrival_method"),
        dwell_base_s=_int(raw, "dwell_base_s", 0),
        dwell_per_boarding_s=_int(raw, "dwell_per_boarding_s", 0),
        dwell_per_alighting_s=_int(raw, "dwell_per_alighting_s", 0),
        turnaround_s=_int(raw, "turnaround_s", 0),
        cycle_rest_s=_int(raw, "cycle_rest_s", 0),
        unknown_link=_str(raw, "unknown_link"),
    )
    if p.unknown_link != "error":
        raise _fail('policy.unknown_link must be "error"', field="unknown_link")
    return p


def check_vehicles_unique(vehicles: Sequence[Vehicle]) -> None:
    ids = [v.vehicle_id for v in vehicles]
    if len(ids) != len(set(ids)):
        raise _fail("vehicle_id must be unique")


def scenario_from_dict(raw: dict) -> Scenario:
    timezone = _str(raw, "timezone")
    if timezone != "Asia/Seoul":
        raise _fail("timezone must be Asia/Seoul", field="timezone")
    fleet = _get(raw, "fleet")
    items = _get(fleet, "vehicles")
    if not isinstance(items, list):
        raise _fail("fleet.vehicles must be a list")
    vehicles = tuple(vehicle_from_dict(x) for x in items)
    check_vehicles_unique(vehicles)
    return Scenario(
        scenario_id=_str(raw, "scenario_id"),
        service_date=_str(raw, "service_date"),
        timezone=timezone,
        network_snapshot_id=_str(raw, "network_snapshot_id"),
        demand_profile_id=_str(raw, "demand_profile_id"),
        strike_route_ids=_str_list(raw, "strike_route_ids"),
        window=window_from_dict(_get(raw, "operating_window")),
        authorized_count=_int(fleet, "authorized_count", 0),
        vehicles=vehicles,
        policy=policy_from_dict(_get(raw, "policy")),
    )


# ---------------------------------------------------------------- arrivals.jsonl

PASSENGER_KEYS = ("passenger_id", "kind", "origin_stop_ids", "dest_stop_ids", "arrival_s", "max_wait_s")


def passenger_from_dict(raw: dict) -> Passenger:
    kind = _str(raw, "kind")
    if kind not in ("access", "egress"):
        raise _fail("kind must be access or egress", field="kind")
    p = Passenger(
        passenger_id=_str(raw, "passenger_id"),
        kind=kind,
        origin_stop_ids=_str_list(raw, "origin_stop_ids", sorted_unique=True, min_len=1),
        dest_stop_ids=_str_list(raw, "dest_stop_ids", sorted_unique=True, min_len=1),
        arrival_s=_int(raw, "arrival_s"),
        max_wait_s=_int(raw, "max_wait_s", 0),
    )
    if set(p.origin_stop_ids) & set(p.dest_stop_ids):
        raise _fail("origin_stop_ids and dest_stop_ids overlap", passenger_id=p.passenger_id)
    return p


def passenger_to_dict(p: Passenger) -> dict:
    return {
        "passenger_id": p.passenger_id,
        "kind": p.kind,
        "origin_stop_ids": list(p.origin_stop_ids),
        "dest_stop_ids": list(p.dest_stop_ids),
        "arrival_s": p.arrival_s,
        "max_wait_s": p.max_wait_s,
    }


def check_passengers(passengers: Sequence[Passenger], window: Window) -> None:
    seen: set[str] = set()
    for p in passengers:
        if p.passenger_id in seen:
            raise _fail("duplicate passenger_id", passenger_id=p.passenger_id)
        seen.add(p.passenger_id)
        if not window.start_s <= p.arrival_s <= window.end_s:
            raise _fail("arrival_s outside operating window", passenger_id=p.passenger_id)


# ---------------------------------------------------------------- plan.json / timetable.json

def _trip_from_dict(raw: dict) -> Trip:
    items = _get(raw, "stop_times")
    if not isinstance(items, list) or len(items) < 2:
        raise _fail("trip.stop_times needs at least 2 entries", trip_id=raw.get("trip_id"))
    stop_times = tuple(StopTime(_int(x, "stop_seq", 0), _str(x, "stop_id"), _int(x, "sched_s")) for x in items)
    in_service = _get(raw, "in_service")
    if not isinstance(in_service, bool):
        raise _fail("in_service must be bool", trip_id=raw.get("trip_id"))
    return Trip(
        trip_id=_str(raw, "trip_id"),
        route_id=_str(raw, "route_id"),
        vehicle_id=_str(raw, "vehicle_id"),
        cycle_index=_int(raw, "cycle_index"),
        leg_index=_int(raw, "leg_index", 0),
        in_service=in_service,
        stop_times=stop_times,
    )


def _duty_from_dict(raw: dict) -> Duty:
    return Duty(
        vehicle_id=_str(raw, "vehicle_id"),
        route_id=_str(raw, "route_id"),
        trip_ids=_str_list(raw, "trip_ids"),
        start_s=_int(raw, "start_s"),
        end_s=_int(raw, "end_s"),
    )


def _route_from_dict(raw: dict) -> RouteSpec:
    legs_raw = _get(raw, "legs")
    if not isinstance(legs_raw, list) or not legs_raw:
        raise _fail("route.legs must be a non-empty list", route_id=raw.get("route_id"))
    legs = []
    for leg in legs_raw:
        in_service = _get(leg, "in_service")
        if not isinstance(in_service, bool):
            raise _fail("leg.in_service must be bool", route_id=raw.get("route_id"))
        legs.append(Leg(in_service, _str_list(leg, "stop_ids")))
    vehicle_ids = None
    if raw.get("vehicle_ids") is not None:
        vehicle_ids = _str_list(raw, "vehicle_ids")
    return RouteSpec(
        route_id=_str(raw, "route_id"),
        legs=tuple(legs),
        vehicle_count=_int(raw, "vehicle_count", 0),
        vehicle_ids=vehicle_ids,
    )


def plan_from_dict(raw: dict) -> PlanSpec:
    """plan.json → PlanSpec. 운행안 출처 필드는 읽지 않는다(공정성, §1.6)."""
    routes_raw = _get(raw, "routes")
    if not isinstance(routes_raw, list):
        raise _fail("routes must be a list")
    routes = tuple(_route_from_dict(r) for r in routes_raw)
    ids = [r.route_id for r in routes]
    if len(ids) != len(set(ids)):
        raise _fail("route_id must be unique")
    trips_raw = raw.get("trips") or []
    duties_raw = raw.get("vehicle_duties") or []
    if not isinstance(trips_raw, list) or not isinstance(duties_raw, list):
        raise _fail("trips and vehicle_duties must be lists")
    return PlanSpec(
        plan_id=_str(raw, "plan_id"),
        scenario_id=_str(raw, "scenario_id"),
        revision=_int(raw, "revision", 0),
        status=_str(raw, "status"),
        routes=routes,
        trips=tuple(_trip_from_dict(t) for t in trips_raw),
        duties=tuple(_duty_from_dict(d) for d in duties_raw),
    )


def trip_to_dict(t: Trip) -> dict:
    return {
        "trip_id": t.trip_id,
        "route_id": t.route_id,
        "vehicle_id": t.vehicle_id,
        "cycle_index": t.cycle_index,
        "leg_index": t.leg_index,
        "in_service": t.in_service,
        "stop_times": [{"stop_seq": s.stop_seq, "stop_id": s.stop_id, "sched_s": s.sched_s} for s in t.stop_times],
    }


def duty_to_dict(d: Duty) -> dict:
    return {"vehicle_id": d.vehicle_id, "route_id": d.route_id, "trip_ids": list(d.trip_ids),
            "start_s": d.start_s, "end_s": d.end_s}


def timetable_to_dict(tt: Timetable) -> dict:
    return {
        "schema_version": tt.schema_version,
        "plan_id": tt.plan_id,
        "plan_revision": tt.plan_revision,
        "origin": tt.origin,
        "routes": [{"route_id": r.route_id, "period_s": r.period_s, "vehicle_ids": list(r.vehicle_ids)}
                   for r in tt.routes],
        "trips": [trip_to_dict(t) for t in tt.trips],
        "duties": [duty_to_dict(d) for d in tt.duties],
    }


def timetable_from_dict(raw: dict) -> Timetable:
    if raw.get("schema_version") != 1:
        raise _fail("timetable.schema_version must be 1")
    origin = _str(raw, "origin")
    if origin not in ("generated", "provided"):
        raise _fail("timetable.origin must be generated or provided")
    routes = tuple(
        TimetableRoute(_str(r, "route_id"), None if r.get("period_s") is None else _int(r, "period_s", 1),
                       _str_list(r, "vehicle_ids"))
        for r in _get(raw, "routes")
    )
    return Timetable(
        plan_id=_str(raw, "plan_id"),
        plan_revision=_int(raw, "plan_revision", 0),
        origin=origin,
        routes=routes,
        trips=tuple(_trip_from_dict(t) for t in _get(raw, "trips")),
        duties=tuple(_duty_from_dict(d) for d in _get(raw, "duties")),
    )
