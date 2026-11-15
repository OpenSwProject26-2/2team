"""이벤트 엔진(simulator.md §3, §4). 파일 입출력·난수·시계를 쓰지 않는다.

차량 이벤트만 `heapq`에 넣고(키 `(t, vehicle_id, visit, kind)`), 승객 도착은 정렬된 배열
포인터로, 대기 한도 초과는 마지막에 한 번에 판정한다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Sequence

from . import io
from .eventlog import canonical_sort
from .models import Event, Passenger, Policy, SimError, Timetable, Trip, Vehicle, Window
from .traveltime import TravelTimeTable

ARRIVE = 0
DEPART = 1

_WAITING = 1
_ONBOARD = 2
_DONE = 3


@dataclass(frozen=True)
class SimContext:
    """실행마다 다시 만들 필요가 없는 입력(정렬·검사가 끝난 상태)."""

    passengers: tuple[Passenger, ...]
    arrival_s_by_passenger: dict[str, int]
    vehicles: dict[str, Vehicle]
    window: Window
    policy: Policy
    tt: TravelTimeTable


def _sorted_passengers(passengers: Sequence[Passenger], window: Window) -> tuple[Passenger, ...]:
    io.check_passengers(passengers, window)
    for p in passengers:
        if set(p.origin_stop_ids) & set(p.dest_stop_ids):
            raise SimError(SimError.INPUT_INVALID, "origin_stop_ids and dest_stop_ids overlap",
                           {"passenger_id": p.passenger_id})
    return tuple(sorted(passengers, key=lambda p: (p.arrival_s, p.passenger_id)))


def prepare(passengers: Sequence[Passenger], vehicles: Sequence[Vehicle], window: Window,
            policy: Policy, tt: TravelTimeTable) -> SimContext:
    if policy.allowed_transfers != 0:
        raise SimError(SimError.OUT_OF_SCOPE, "v1 supports allowed_transfers = 0 only",
                       {"allowed_transfers": policy.allowed_transfers})
    tt.check_window(window)
    io.check_vehicles_unique(vehicles)
    pax = _sorted_passengers(passengers, window)
    return SimContext(pax, {p.passenger_id: p.arrival_s for p in pax},
                      {v.vehicle_id: v for v in vehicles}, window, policy, tt)


class _VehicleState:
    __slots__ = ("vehicle", "trips", "cycle_info", "ti", "k", "load", "onboard", "pattern")

    def __init__(self, vehicle: Vehicle, trips: list[Trip], cycle_info: dict[int, tuple[int, int]],
                 pattern: list[int]):
        self.vehicle = vehicle
        self.trips = trips
        self.cycle_info = cycle_info   # trip 순번(새 cycle 첫 trip) → (예정 소요, 영업 trip 수)
        self.pattern = pattern         # trip 순번 → 영업 패턴 번호(공차는 -1)
        self.ti = 0
        self.k = 0
        self.load = 0
        self.onboard: dict[int, list[int]] = {}


def simulate(ctx: SimContext, timetable: Timetable,
             passengers: Sequence[Passenger] | None = None) -> list[Event]:
    """시간표를 실행해 정렬·seq 부여까지 끝난 이벤트 목록을 돌려준다."""
    if passengers is None:
        pax = ctx.passengers
        arrival_map = ctx.arrival_s_by_passenger
    else:
        pax = _sorted_passengers(passengers, ctx.window)
        arrival_map = {p.passenger_id: p.arrival_s for p in pax}
    policy = ctx.policy
    tt = ctx.tt
    window_end = ctx.window.end_s
    per_pax_dwell = policy.dwell_per_boarding_s + policy.dwell_per_alighting_s

    # ---- 영업 패턴(정류장 순서열)과 정적 원인 판정용 색인(§4.5)
    pattern_of: dict[tuple[str, ...], int] = {}
    patterns: list[tuple[str, ...]] = []
    for t in timetable.trips:
        if t.in_service and t.stop_ids not in pattern_of:
            pattern_of[t.stop_ids] = len(patterns)
            patterns.append(t.stop_ids)
    stop_positions: dict[str, list[tuple[int, int]]] = {}
    for pid, stops in enumerate(patterns):
        for k, s in enumerate(stops[:-1]):
            stop_positions.setdefault(s, []).append((pid, k))

    cause_cache: dict[tuple[tuple[str, ...], tuple[str, ...]], str | None] = {}

    def static_cause(p: Passenger) -> str | None:
        key = (p.origin_stop_ids, p.dest_stop_ids)
        if key in cause_cache:
            return cause_cache[key]
        positions = [pos for s in p.origin_stop_ids for pos in stop_positions.get(s, ())]
        if not positions:
            cause: str | None = "NO_ROUTE_AT_STOP"
        else:
            dest = set(p.dest_stop_ids)
            reachable = any(any(s in dest for s in patterns[pid][k + 1:]) for pid, k in positions)
            cause = None if reachable else "NO_ROUTE_TO_DEST"
        cause_cache[key] = cause
        return cause

    alight_cache: dict[tuple[int, int, tuple[str, ...]], int] = {}

    def alight_index(pid: int, k: int, dest: tuple[str, ...]) -> int:
        key = (pid, k, dest)
        j = alight_cache.get(key)
        if j is None:
            j = -1
            stops = patterns[pid]
            dest_set = set(dest)
            for jj in range(k + 1, len(stops)):
                if stops[jj] in dest_set:
                    j = jj
                    break
            alight_cache[key] = j
        return j

    # ---- 차량 상태
    trips_by_id = {t.trip_id: t for t in timetable.trips}
    states: dict[str, _VehicleState] = {}
    heap: list[tuple[int, str, int, int]] = []
    for duty in timetable.duties:
        vehicle = ctx.vehicles.get(duty.vehicle_id)
        if vehicle is None:
            raise SimError(SimError.PLAN_INFEASIBLE, "duty vehicle not in fleet", {"vehicle_id": duty.vehicle_id})
        trips = [trips_by_id[x] for x in duty.trip_ids]
        if not trips:
            continue
        cycle_info: dict[int, tuple[int, int]] = {}
        for i, t in enumerate(trips):
            if t.leg_index != 0:
                continue
            j = i
            while j + 1 < len(trips) and trips[j + 1].cycle_index == t.cycle_index and trips[j + 1].leg_index != 0:
                j += 1
            dur = trips[j].stop_times[-1].sched_s - t.stop_times[0].sched_s
            cycle_info[i] = (dur, sum(1 for x in trips[i:j + 1] if x.in_service))
        pattern = [pattern_of[t.stop_ids] if t.in_service else -1 for t in trips]
        states[duty.vehicle_id] = _VehicleState(vehicle, trips, cycle_info, pattern)
        heap.append((trips[0].stop_times[0].sched_s, duty.vehicle_id, 0, ARRIVE))
    heapq.heapify(heap)

    n = len(pax)
    state = [0] * n
    denied_count = [0] * n
    queues: dict[str, list[int]] = {}
    events: list[Event] = []
    emit = events.append

    def admit(idx: int) -> None:
        p = pax[idx]
        cause = static_cause(p)
        if cause is not None:
            state[idx] = _DONE
            emit(Event(t=p.arrival_s, type="no_service", passenger_id=p.passenger_id, cause=cause))
            return
        state[idx] = _WAITING
        for s in p.origin_stop_ids:
            q = queues.get(s)
            if q is None:
                queues[s] = [idx]
            else:
                q.append(idx)

    def start_next_trip(free: int, vid: str, vs: _VehicleState, visit: int) -> None:
        cur = vs.trips[vs.ti]
        nxt_i = vs.ti + 1
        if nxt_i >= len(vs.trips):
            return
        nxt = vs.trips[nxt_i]
        ready = free + (policy.turnaround_s if cur.in_service else 0)
        if nxt.leg_index == 0:
            ready += policy.cycle_rest_s
            s = max(nxt.stop_times[0].sched_s, ready)
            dur, n_service = vs.cycle_info[nxt_i]
            allow = per_pax_dwell * vs.vehicle.capacity * n_service
            if s + dur + allow > min(window_end, vs.vehicle.available_end_s):
                emit(Event(t=ready, type="cancel", vehicle_id=vid, trip_id=nxt.trip_id, visit=visit + 1,
                           cause="LATE"))
                return
        else:
            s = max(nxt.stop_times[0].sched_s, ready)
        vs.ti = nxt_i
        vs.k = 0
        heapq.heappush(heap, (s, vid, visit + 1, ARRIVE))

    def on_arrive(t: int, vid: str, vs: _VehicleState, visit: int) -> None:
        trip = vs.trips[vs.ti]
        k = vs.k
        last = len(trip.stop_times) - 1
        stop = trip.stop_times[k].stop_id
        alighters: list[int] = []
        boarders: list[int] = []
        denied: list[int] = []
        left = 0
        if trip.in_service:
            alighters = vs.onboard.pop(k, [])
            vs.load -= len(alighters)
            if k < last:
                q = queues.get(stop)
                if q:
                    cap = vs.vehicle.capacity
                    pid = vs.pattern[vs.ti]
                    keep: list[int] = []
                    for idx in q:
                        if state[idx] != _WAITING:
                            continue
                        p = pax[idx]
                        if p.arrival_s + p.max_wait_s < t:
                            continue
                        j = alight_index(pid, k, p.dest_stop_ids)
                        if j < 0:
                            keep.append(idx)
                            continue
                        if vs.load + len(boarders) < cap:
                            boarders.append(idx)
                            state[idx] = _ONBOARD
                            lst = vs.onboard.get(j)
                            if lst is None:
                                vs.onboard[j] = [idx]
                            else:
                                lst.append(idx)
                        else:
                            left += 1
                            denied_count[idx] += 1
                            if denied_count[idx] == 1:
                                denied.append(idx)
                            keep.append(idx)
                    queues[stop] = keep
                vs.load += len(boarders)
        trip_id = trip.trip_id
        emit(Event(t=t, type="arrive", vehicle_id=vid, trip_id=trip_id, visit=visit, stop_id=stop, stop_seq=k,
                   alighted=len(alighters), boarded=len(boarders), left_behind=left, load=vs.load))
        for idx in alighters:
            state[idx] = _DONE
            emit(Event(t=t, type="alight", vehicle_id=vid, trip_id=trip_id, visit=visit, stop_id=stop,
                       stop_seq=k, passenger_id=pax[idx].passenger_id))
        for idx in boarders:
            emit(Event(t=t, type="board", vehicle_id=vid, trip_id=trip_id, visit=visit, stop_id=stop,
                       stop_seq=k, passenger_id=pax[idx].passenger_id))
        for idx in denied:
            emit(Event(t=t, type="denied", vehicle_id=vid, trip_id=trip_id, visit=visit, stop_id=stop,
                       stop_seq=k, passenger_id=pax[idx].passenger_id))
        if not trip.in_service:
            dwell = 0
        elif k < last:
            dwell = (policy.dwell_base_s + policy.dwell_per_boarding_s * len(boarders)
                     + policy.dwell_per_alighting_s * len(alighters))
        else:
            dwell = policy.dwell_per_alighting_s * len(alighters)
        if k < last:
            heapq.heappush(heap, (t + dwell, vid, visit, DEPART))
        else:
            start_next_trip(t + dwell, vid, vs, visit)

    def on_depart(t: int, vid: str, vs: _VehicleState, visit: int) -> None:
        trip = vs.trips[vs.ti]
        k = vs.k
        a = trip.stop_times[k].stop_id
        b = trip.stop_times[k + 1].stop_id
        emit(Event(t=t, type="depart", vehicle_id=vid, trip_id=trip.trip_id, visit=visit, stop_id=a, stop_seq=k,
                   load=vs.load))
        vs.k = k + 1
        heapq.heappush(heap, (t + tt.lookup(a, b, t), vid, visit + 1, ARRIVE))

    # ---- 메인 루프(§3.2): 같은 시각이면 승객 도착을 먼저 받는다
    i = 0
    while heap:
        t, vid, visit, kind = heapq.heappop(heap)
        while i < n and pax[i].arrival_s <= t:
            admit(i)
            i += 1
        vs = states[vid]
        if kind == ARRIVE:
            on_arrive(t, vid, vs, visit)
        else:
            on_depart(t, vid, vs, visit)
    while i < n:
        admit(i)
        i += 1

    # ---- 남은 대기 승객(§4.5)
    for idx in sorted((x for x in range(n) if state[x] == _WAITING), key=lambda x: pax[x].passenger_id):
        p = pax[idx]
        deadline = p.arrival_s + p.max_wait_s
        if deadline <= window_end:
            emit(Event(t=deadline, type="abandon", passenger_id=p.passenger_id,
                       cause="FULL" if denied_count[idx] else "WAIT_EXCEEDED"))
        else:
            emit(Event(t=window_end, type="left_waiting", passenger_id=p.passenger_id,
                       cause="FULL" if denied_count[idx] else "SERVICE_ENDED"))
    return canonical_sort(events, arrival_map)
