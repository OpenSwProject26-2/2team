"""시간표 생성기(simulator.md §1).

노선+대수만 있는 운행안을 차량별 trip·duty로 바꾼다. 운행안 출처와 무관하게 모든 안이
`normalize_plan` 하나를 지난다(§1.6). 정수 연산만 쓴다.
"""

from __future__ import annotations

from typing import Sequence

from .models import (
    Duty, PlanSpec, Policy, RouteSpec, SimError, StopTime, Timetable, TimetableRoute, Trip,
    Vehicle, Window,
)
from .traveltime import TravelTimeTable


def _infeasible(message: str, **details) -> SimError:
    return SimError(SimError.PLAN_INFEASIBLE, message, details)


# ---------------------------------------------------------------- 노선 구조 검사

def check_route(route: RouteSpec, tt: TravelTimeTable) -> None:
    """leg 연속성(§0.3)과 구간 자료 존재(§0.4)."""
    for li, leg in enumerate(route.legs):
        if len(leg.stop_ids) < 2:
            raise _infeasible("leg needs at least 2 stops", route_id=route.route_id, leg_index=li)
        nxt = route.legs[(li + 1) % len(route.legs)]
        if leg.stop_ids[-1] != nxt.stop_ids[0]:
            raise _infeasible("legs are not continuous", route_id=route.route_id, leg_index=li)
        for a, b in zip(leg.stop_ids, leg.stop_ids[1:]):
            if not tt.has(a, b):
                raise SimError(SimError.DATA_INCOMPLETE, "missing segment",
                               {"route_id": route.route_id, "segment": [a, b]})


# ---------------------------------------------------------------- §1.2 계획 순환시간

def planned_cycle(route: RouteSpec, t0: int, policy: Policy,
                  tt: TravelTimeTable) -> tuple[tuple[tuple[int, ...], ...], int, int]:
    """(leg별 스냅숏 예정 시각, 마지막 leg 종점 도착, 다음 cycle 가능 시각)."""
    t = t0
    leg_times: list[tuple[int, ...]] = []
    finish = t0
    for leg in route.legs:
        times = [t]
        stop_dwell = policy.dwell_base_s if leg.in_service else 0
        for a, b in zip(leg.stop_ids, leg.stop_ids[1:]):
            dep = t + stop_dwell
            t = dep + tt.lookup(a, b, dep)
            times.append(t)
        leg_times.append(tuple(times))
        finish = t
        if leg.in_service:
            t += policy.turnaround_s
    ready = t + policy.cycle_rest_s
    return tuple(leg_times), finish, ready


# ---------------------------------------------------------------- §1.4 차량 배정

def assign_vehicles(plan: PlanSpec, vehicles: Sequence[Vehicle]) -> dict[str, tuple[Vehicle, ...]]:
    """노선별 배정 차량(각각 vehicle_id 오름차순).

    명시 배정(`vehicle_ids`) 차량을 먼저 빼 두고, 나머지 노선은 route_id 순으로 남은 차량을
    vehicle_id 순으로 앞에서부터 받는다.
    """
    by_id = {v.vehicle_id: v for v in vehicles}
    routes = sorted(plan.routes, key=lambda r: r.route_id)
    total = sum(r.vehicle_count for r in routes)
    if total > len(by_id):
        raise _infeasible("assigned vehicles exceed available fleet", assigned=total, available=len(by_id))
    reserved: set[str] = set()
    out: dict[str, tuple[Vehicle, ...]] = {}
    for r in routes:
        if r.vehicle_ids is None:
            continue
        ids = list(r.vehicle_ids)
        if len(ids) != r.vehicle_count:
            raise _infeasible("vehicle_ids length differs from vehicle_count", route_id=r.route_id)
        for vid in ids:
            if vid not in by_id:
                raise _infeasible("unknown vehicle_id", route_id=r.route_id, vehicle_id=vid)
            if vid in reserved:
                raise _infeasible("vehicle assigned twice", route_id=r.route_id, vehicle_id=vid)
            reserved.add(vid)
        out[r.route_id] = tuple(by_id[v] for v in sorted(ids))
    pool = [by_id[v] for v in sorted(by_id) if v not in reserved]
    for r in routes:
        if r.vehicle_ids is not None:
            continue
        out[r.route_id] = tuple(pool[:r.vehicle_count])
        pool = pool[r.vehicle_count:]
    for r in routes:
        for v in out[r.route_id]:
            for field, stop in (("initial_stop_id", v.initial_stop_id), ("end_stop_id", v.end_stop_id)):
                if stop is not None and stop != r.anchor:
                    raise _infeasible("depot deadhead is not modelled in v1", reason="DEADHEAD_DATA_REQUIRED",
                                      route_id=r.route_id, vehicle_id=v.vehicle_id, field=field)
    return out


# ---------------------------------------------------------------- §1.3 주기·출발·종료

def _service_legs(route: RouteSpec) -> int:
    return sum(1 for leg in route.legs if leg.in_service)


def route_period(route: RouteSpec, vehicles: Sequence[Vehicle], window: Window, policy: Policy,
                 tt: TravelTimeTable) -> tuple[int, int]:
    """(첫 출발 start, 고정 주기 P)."""
    start = max([window.start_s] + [v.available_start_s for v in vehicles])
    samples = [start] + [b for b in tt.bin_starts() if start < b < window.end_s]
    period = max(planned_cycle(route, s, policy, tt)[2] - s for s in samples)
    return start, period


def build_route_trips(route: RouteSpec, vehicles: Sequence[Vehicle], window: Window, policy: Policy,
                      tt: TravelTimeTable) -> tuple[int | None, list[Trip]]:
    n = len(vehicles)
    if n == 0:
        return None, []
    start, period = route_period(route, vehicles, window, policy, tt)
    n_rev = _service_legs(route)
    per_pax = policy.dwell_per_boarding_s + policy.dwell_per_alighting_s
    trips: list[Trip] = []
    for k, v in enumerate(sorted(vehicles, key=lambda x: x.vehicle_id)):
        end = min(window.end_s, v.available_end_s)
        allow = per_pax * v.capacity * n_rev
        m = 0
        prev_ready: int | None = None
        while True:
            s = start + (k * period) // n + m * period
            if prev_ready is not None:
                s = max(s, prev_ready)
            leg_times, finish, ready = planned_cycle(route, s, policy, tt)
            if finish + allow > end:
                break
            for li, leg in enumerate(route.legs):
                trips.append(Trip(
                    trip_id=f"{route.route_id}-{v.vehicle_id}-{m:03d}-{li}",
                    route_id=route.route_id,
                    vehicle_id=v.vehicle_id,
                    cycle_index=m,
                    leg_index=li,
                    in_service=leg.in_service,
                    stop_times=tuple(StopTime(seq, stop, leg_times[li][seq])
                                     for seq, stop in enumerate(leg.stop_ids)),
                ))
            prev_ready = ready
            m += 1
    return period, trips


def _trip_order(t: Trip) -> tuple:
    return (t.vehicle_id, t.cycle_index, t.leg_index)


def _duties(trips: Sequence[Trip]) -> tuple[Duty, ...]:
    by_vehicle: dict[str, list[Trip]] = {}
    for t in trips:
        by_vehicle.setdefault(t.vehicle_id, []).append(t)
    duties = []
    for vid in sorted(by_vehicle):
        ts = by_vehicle[vid]
        duties.append(Duty(vid, ts[0].route_id, tuple(t.trip_id for t in ts),
                           ts[0].stop_times[0].sched_s, ts[-1].stop_times[-1].sched_s))
    return tuple(duties)


def build_timetable(plan: PlanSpec, vehicles: Sequence[Vehicle], window: Window,
                    policy: Policy, tt: TravelTimeTable) -> Timetable:
    for r in plan.routes:
        check_route(r, tt)
    assigned = assign_vehicles(plan, vehicles)
    routes: list[TimetableRoute] = []
    trips: list[Trip] = []
    for r in sorted(plan.routes, key=lambda x: x.route_id):
        vs = assigned[r.route_id]
        period, route_trips = build_route_trips(r, vs, window, policy, tt)
        routes.append(TimetableRoute(r.route_id, period, tuple(v.vehicle_id for v in vs)))
        trips.extend(route_trips)
    trips.sort(key=_trip_order)
    return Timetable(plan.plan_id, plan.revision, "generated", tuple(routes), tuple(trips), _duties(trips))


def provided_timetable(plan: PlanSpec) -> Timetable:
    trips = tuple(sorted(plan.trips, key=_trip_order))
    duty_ids = {d.vehicle_id: d for d in plan.duties}
    duties = tuple(duty_ids[v] for v in sorted(duty_ids))
    route_vehicles: dict[str, set[str]] = {r.route_id: set() for r in plan.routes}
    for t in trips:
        route_vehicles.setdefault(t.route_id, set()).add(t.vehicle_id)
    routes = tuple(TimetableRoute(rid, None, tuple(sorted(route_vehicles[rid]))) for rid in sorted(route_vehicles))
    return Timetable(plan.plan_id, plan.revision, "provided", routes, trips, duties)


# ---------------------------------------------------------------- §1.5 검사

def validate_timetable(timetable: Timetable, plan: PlanSpec, vehicles: Sequence[Vehicle],
                       window: Window, tt: TravelTimeTable) -> None:
    by_id = {v.vehicle_id: v for v in vehicles}
    routes = {r.route_id: r for r in plan.routes}
    for r in plan.routes:
        check_route(r, tt)
    seen_trips: set[str] = set()
    by_vehicle: dict[str, list[Trip]] = {}
    for t in timetable.trips:
        if t.trip_id in seen_trips:
            raise _infeasible("duplicate trip_id", trip_id=t.trip_id)
        seen_trips.add(t.trip_id)
        route = routes.get(t.route_id)
        if route is None:
            raise _infeasible("trip on unknown route", trip_id=t.trip_id)
        if t.vehicle_id not in by_id:
            raise _infeasible("trip vehicle not in fleet", trip_id=t.trip_id, vehicle_id=t.vehicle_id)
        if not 0 <= t.leg_index < len(route.legs):
            raise _infeasible("leg_index out of range", trip_id=t.trip_id)
        leg = route.legs[t.leg_index]
        if t.stop_ids != leg.stop_ids or t.in_service != leg.in_service:
            raise _infeasible("trip stops differ from route leg", trip_id=t.trip_id)
        if [s.stop_seq for s in t.stop_times] != list(range(len(t.stop_times))):
            raise _infeasible("stop_seq must be 0..n-1", trip_id=t.trip_id)
        times = [s.sched_s for s in t.stop_times]
        if any(b < a for a, b in zip(times, times[1:])):
            raise _infeasible("sched_s must be non-decreasing", trip_id=t.trip_id)
        by_vehicle.setdefault(t.vehicle_id, []).append(t)

    duties = {d.vehicle_id: d for d in timetable.duties}
    if set(duties) != set(by_vehicle) or len(duties) != len(timetable.duties):
        raise _infeasible("duties must list exactly the vehicles with trips")
    route_users: dict[str, set[str]] = {}
    for vid, ts in by_vehicle.items():
        v = by_id[vid]
        ts.sort(key=lambda t: (t.cycle_index, t.leg_index))
        route_ids = {t.route_id for t in ts}
        if len(route_ids) != 1:
            raise _infeasible("vehicle on more than one route", vehicle_id=vid)
        rid = ts[0].route_id
        route_users.setdefault(rid, set()).add(vid)
        d = duties[vid]
        if d.route_id != rid or list(d.trip_ids) != [t.trip_id for t in ts]:
            raise _infeasible("duty does not match trips", vehicle_id=vid)
        if d.start_s != ts[0].stop_times[0].sched_s or d.end_s != ts[-1].stop_times[-1].sched_s:
            raise _infeasible("duty start_s/end_s differ from trips", vehicle_id=vid)
        lo = max(window.start_s, v.available_start_s)
        hi = min(window.end_s, v.available_end_s)
        for t in ts:
            for s in t.stop_times:
                if not lo <= s.sched_s <= hi:
                    raise _infeasible("sched_s outside vehicle window", trip_id=t.trip_id, sched_s=s.sched_s)
        for a, b in zip(ts, ts[1:]):
            if a.stop_times[-1].stop_id != b.stop_times[0].stop_id:
                raise _infeasible("consecutive trips are not connected", trip_id=b.trip_id)
            if b.stop_times[0].sched_s < a.stop_times[-1].sched_s:
                raise _infeasible("trips of a vehicle overlap", trip_id=b.trip_id)
        if v.initial_stop_id is not None and v.initial_stop_id != ts[0].stop_times[0].stop_id:
            raise _infeasible("duty does not start at initial_stop_id", reason="DEADHEAD_DATA_REQUIRED",
                              vehicle_id=vid)
        if v.end_stop_id is not None and v.end_stop_id != ts[-1].stop_times[-1].stop_id:
            raise _infeasible("duty does not end at end_stop_id", reason="DEADHEAD_DATA_REQUIRED", vehicle_id=vid)
    for rid, users in route_users.items():
        route = routes[rid]
        if len(users) > route.vehicle_count:
            raise _infeasible("route uses more vehicles than vehicle_count", route_id=rid)
        if route.vehicle_ids is not None and not users <= set(route.vehicle_ids):
            raise _infeasible("route uses vehicles outside vehicle_ids", route_id=rid)
    if sum(len(u) for u in route_users.values()) > len(by_id):
        raise _infeasible("timetable uses more vehicles than available")


# ---------------------------------------------------------------- §1.6 공정성

def normalize_plan(plan: PlanSpec, vehicles: Sequence[Vehicle], window: Window,
                   policy: Policy, tt: TravelTimeTable) -> Timetable:
    """모든 운행안이 지나는 단일 경로. trips가 비면 생성, 있으면 그대로 검사."""
    if not plan.trips:
        timetable = build_timetable(plan, vehicles, window, policy, tt)
    else:
        timetable = provided_timetable(plan)
    validate_timetable(timetable, plan, vehicles, window, tt)
    return timetable
