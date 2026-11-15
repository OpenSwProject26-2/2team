"""이벤트 엔진(simulator.md §3, §4, m07-output-contract.md §4). 파일 입출력·난수·시계를 쓰지 않는다.

M07 계약에 맞춘 **고정 일정 실행기**다. 차량은 `timetable.json`의 예정 시각을 그대로 지킨다(정차는
`dwell_base_s` 고정, 1인당 정차는 지원하지 않음). 사건 키 `(time_s, phase, stable_id)` 순서로 처리한다.

- 방문 사건(하차 phase 0 · 승차 phase 2 · 출발 phase 3)은 일정에서 미리 만들어 정렬한다.
- 승객 발생(phase 1)은 `(arrival_s, passenger_id)` 순 배열, 기한(phase 4)은 정렬된 배열이다.
- 세 줄을 `heapq.merge`로 합쳐 한 번 훑는다. 승차는 정류장별 FIFO 대기열에서 자격을 잃은 승객을
  지연 제거하며 고른다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Sequence

from . import io
from .models import (
    PHASE_ALIGHT, PHASE_BOARD, PHASE_DEPART, PHASE_EXPIRE, PHASE_GENERATE,
    Event, Passenger, Policy, ServiceTrip, SimError, Timetable, Trip, Vehicle, Window,
)
from .traveltime import TravelTimeTable

WAITING, ONBOARD, ARRIVED = "waiting", "onboard", "arrived"
SERVED, UNSERVED, CENSORED = "served", "unserved", "censored"


@dataclass(frozen=True)
class SimContext:
    """실행마다 다시 만들 필요가 없는 입력(정렬·검사가 끝난 상태)."""

    passengers: tuple[Passenger, ...]       # (arrival_s, passenger_id) 순
    vehicles: dict[str, Vehicle]
    window: Window
    policy: Policy
    tt: TravelTimeTable
    drain_s: int


def drain_of(policy: Policy, window: Window) -> int:
    """관측 끝. 정책에 없으면 W_e + max_journey_s(모든 승객이 기한 안에 확정된다, §6.2)."""
    return policy.drain_s if policy.drain_s is not None else window.end_s + policy.max_journey_s


def _sorted_passengers(passengers: Sequence[Passenger], window: Window, drain_s: int) -> tuple[Passenger, ...]:
    io.check_passengers(passengers, window)
    pax = tuple(sorted(passengers, key=lambda p: (p.arrival_s, p.passenger_id)))
    if pax and pax[-1].arrival_s > drain_s:
        # drain 뒤 발생 승객은 generated 사건을 남길 수 없어 M07 V4가 실패한다(계약 I5)
        raise SimError(SimError.INPUT_INVALID, "drain_s is earlier than the last passenger arrival",
                       {"drain_s": drain_s, "arrival_s": pax[-1].arrival_s})
    return pax


def prepare(passengers: Sequence[Passenger], vehicles: Sequence[Vehicle], window: Window,
            policy: Policy, tt: TravelTimeTable) -> SimContext:
    if policy.allowed_transfers != 0:
        raise SimError(SimError.OUT_OF_SCOPE, "v1 supports allowed_transfers = 0 only",
                       {"allowed_transfers": policy.allowed_transfers})
    if policy.dwell_per_boarding_s or policy.dwell_per_alighting_s:
        # M07 network.json은 고정 일정만 표현한다. 1인당 정차로 실현 시각이 바뀌면 일정과 어긋난다(§3.3).
        raise SimError(SimError.OUT_OF_SCOPE, "M07 output needs a fixed schedule: per-passenger dwell must be 0",
                       {"dwell_per_boarding_s": policy.dwell_per_boarding_s,
                        "dwell_per_alighting_s": policy.dwell_per_alighting_s})
    drain_s = drain_of(policy, window)
    tt.check_window(window)
    io.check_vehicles_unique(vehicles)
    pax = _sorted_passengers(passengers, window, drain_s)
    return SimContext(pax, {v.vehicle_id: v for v in vehicles}, window, policy, tt, drain_s)


# ---------------------------------------------------------------- 시간표 → M07 운행편

def _not_executable(message: str, **details) -> SimError:
    return SimError(SimError.PLAN_INFEASIBLE, f"timetable is not executable as scheduled: {message}", details)


def service_schedule(timetable: Timetable, ctx: SimContext) -> tuple[ServiceTrip, ...]:
    """시간표를 M07 운행편(영업 leg 1개 + 복귀 공차)으로 바꾼다(m07-output-contract.md §2.1, I1–I3·I6).

    - cycle마다 영업 leg가 정확히 1개이고 첫 leg여야 한다. 아니면 OUT_OF_SCOPE(두 역 연결형 등, 가정 A26).
    - 예정 시각이 구간 시간·정차·회차와 맞아야 한다(고정 일정이므로 실행 중 밀리지 않는다).
      생성기가 만든 시간표는 항상 맞고, 운행안이 준 시간표가 어긋나면 PLAN_INFEASIBLE.
    """
    policy, tt = ctx.policy, ctx.tt
    by_id = {t.trip_id: t for t in timetable.trips}
    out: list[ServiceTrip] = []
    for duty in timetable.duties:
        vehicle = ctx.vehicles.get(duty.vehicle_id)
        if vehicle is None:
            raise SimError(SimError.PLAN_INFEASIBLE, "duty vehicle not in fleet", {"vehicle_id": duty.vehicle_id})
        trips = [by_id[x] for x in duty.trip_ids]
        prev: Trip | None = None
        for trip in trips:
            _check_trip_times(trip, policy, tt)
            if prev is not None:
                ready = prev.stop_times[-1].sched_s + (policy.turnaround_s if prev.in_service else 0)
                if trip.leg_index == 0:
                    ready += policy.cycle_rest_s
                if trip.stop_times[0].sched_s < ready:
                    raise _not_executable("trip starts before the vehicle is ready", trip_id=trip.trip_id,
                                          sched_s=trip.stop_times[0].sched_s, ready_s=ready)
            prev = trip
        cycles: dict[int, list[Trip]] = {}
        for trip in trips:
            cycles.setdefault(trip.cycle_index, []).append(trip)
        for cycle_index in sorted(cycles):
            legs = sorted(cycles[cycle_index], key=lambda t: t.leg_index)
            flags = [t.in_service for t in legs]
            if flags[0] is not True or any(flags[1:]):
                raise SimError(SimError.OUT_OF_SCOPE,
                               "M07 output supports one in-service leg per cycle, as the first leg",
                               {"route_id": legs[0].route_id, "vehicle_id": duty.vehicle_id,
                                "cycle_index": cycle_index, "in_service": flags})
            out.append(_service_trip(legs[0], legs[1:], vehicle, policy, tt))
    return tuple(sorted(out, key=lambda s: (s.vehicle_id, s.arrivals[0], s.trip_id)))


def _check_trip_times(trip: Trip, policy: Policy, tt: TravelTimeTable) -> None:
    dwell = policy.dwell_base_s if trip.in_service else 0
    st = trip.stop_times
    for a, b in zip(st, st[1:]):
        dep = a.sched_s + dwell
        expected = dep + tt.lookup(a.stop_id, b.stop_id, dep)
        if b.sched_s != expected:
            raise _not_executable("stop time differs from travel time + dwell", trip_id=trip.trip_id,
                                  stop_seq=b.stop_seq, sched_s=b.sched_s, expected_s=expected)


def _service_trip(service: Trip, deadheads: Sequence[Trip], vehicle: Vehicle, policy: Policy,
                  tt: TravelTimeTable) -> ServiceTrip:
    st = service.stop_times
    arrivals = tuple(s.sched_s for s in st)
    departures = tuple(s.sched_s + policy.dwell_base_s for s in st[:-1]) + (arrivals[-1],)
    distance = 0
    for trip in (service, *deadheads):
        distance += sum(tt.distance(a.stop_id, b.stop_id) for a, b in zip(trip.stop_times, trip.stop_times[1:]))
    return_s = deadheads[-1].stop_times[-1].sched_s if deadheads else arrivals[-1]
    return ServiceTrip(service.trip_id, service.route_id, vehicle.vehicle_id, vehicle.capacity,
                       tuple(s.stop_id for s in st), arrivals, departures, return_s, distance)


# ---------------------------------------------------------------- 실행

@dataclass(frozen=True)
class SimResult:
    events: list[Event]
    passengers: tuple[Passenger, ...]       # (arrival_s, passenger_id) 순
    refused: tuple[int, ...]                # passengers와 같은 순서. 만차로 남겨진 횟수
    refusal_events: int
    drain_s: int


def simulate(ctx: SimContext, schedule: Sequence[ServiceTrip],
             passengers: Sequence[Passenger] | None = None) -> SimResult:
    """일정을 실행해 사건 키 순서로 정렬된 M07 사건 목록을 돌려준다."""
    drain = ctx.drain_s
    pax = ctx.passengers if passengers is None else _sorted_passengers(passengers, ctx.window, drain)
    W, J = ctx.policy.max_wait_s, ctx.policy.max_journey_s
    trips = list(schedule)

    # ---- 키 목록: (time_s, phase, stable_id, a, b)
    visit_keys: list[tuple[int, int, str, int, int]] = []
    for ti, trip in enumerate(trips):
        for k, stop in enumerate(trip.stops):
            sid = trip.stable_id(k)
            if trip.arrivals[k] <= drain:
                visit_keys.append((trip.arrivals[k], PHASE_ALIGHT, sid, ti, k))
                visit_keys.append((trip.arrivals[k], PHASE_BOARD, sid, ti, k))
            if trip.departures[k] <= drain:
                visit_keys.append((trip.departures[k], PHASE_DEPART, sid, ti, k))
    visit_keys.sort()
    gen_keys = [(p.arrival_s, PHASE_GENERATE, "generate:" + p.passenger_id, i, 0) for i, p in enumerate(pax)]
    exp_keys = []
    for i, p in enumerate(pax):
        if p.arrival_s + J <= drain:
            exp_keys.append((p.arrival_s + J, PHASE_EXPIRE, "journey:" + p.passenger_id, i, 0))
        if p.arrival_s + W <= drain:
            exp_keys.append((p.arrival_s + W, PHASE_EXPIRE, "wait:" + p.passenger_id, i, 1))
    exp_keys.sort()

    # ---- 상태
    n = len(pax)
    physical = [WAITING] * n
    outcome: list[str | None] = [None] * n
    vehicle_of: list[str | None] = [None] * n
    refused = [0] * n
    refusal_events = 0
    queues: dict[str, list[int]] = {}
    load: dict[str, int] = {}
    alight_at: dict[tuple[int, int], list[int]] = {}   # (trip 순번, 정류장 순번) → 승객(승차 순)
    alight_cache: dict[tuple[int, int, str], int] = {}
    events: list[Event] = []
    emit = events.append

    def alight_index(ti: int, k: int, dest: str) -> int:
        key = (ti, k, dest)
        j = alight_cache.get(key)
        if j is None:
            stops = trips[ti].stops
            j = next((jj for jj in range(k + 1, len(stops)) if stops[jj] == dest), -1)
            alight_cache[key] = j
        return j

    for t, phase, sid, a, b in heapq.merge(visit_keys, gen_keys, exp_keys):
        if phase == PHASE_ALIGHT:
            trip = trips[a]
            vid = trip.vehicle_id
            occ = load.get(vid, 0)
            for idx in alight_at.pop((a, b), ()):
                occ -= 1
                physical[idx] = ARRIVED
                vehicle_of[idx] = None
                if outcome[idx] is None:
                    outcome[idx] = SERVED
                emit(Event(t, PHASE_ALIGHT, sid, "alighted", pax[idx].passenger_id, vid, occ))
            load[vid] = occ
            emit(Event(t, PHASE_ALIGHT, sid, "vehicle_arrived", None, vid, occ))
        elif phase == PHASE_GENERATE:
            p = pax[a]
            q = queues.get(p.origin)
            if q is None:
                queues[p.origin] = [a]
            else:
                q.append(a)
            emit(Event(t, PHASE_GENERATE, sid, "generated", p.passenger_id, None, None))
        elif phase == PHASE_BOARD:
            trip = trips[a]
            q = queues.get(trip.stops[b])
            if not q:
                continue
            vid = trip.vehicle_id
            occ = load.get(vid, 0)
            keep: list[int] = []
            for idx in q:
                if physical[idx] != WAITING or outcome[idx] is not None:
                    continue                                  # 이미 탔거나 기한 만료 → 줄에서 뺀다
                if alight_index(a, b, pax[idx].destination) < 0:
                    keep.append(idx)                          # 이 차는 목적지에 안 간다. 뒤 사람을 막지 않는다
                    continue
                if occ < trip.capacity:
                    occ += 1
                    physical[idx] = ONBOARD
                    vehicle_of[idx] = vid
                    alight_at.setdefault((a, alight_index(a, b, pax[idx].destination)), []).append(idx)
                    emit(Event(t, PHASE_BOARD, sid, "boarded", pax[idx].passenger_id, vid, occ))
                else:
                    refused[idx] += 1                         # 만차 거절(계약 §4.3)
                    refusal_events += 1
                    keep.append(idx)
            queues[trip.stops[b]] = keep
            load[vid] = occ
        elif phase == PHASE_DEPART:
            vid = trips[a].vehicle_id
            emit(Event(t, PHASE_DEPART, sid, "departed", None, vid, load.get(vid, 0)))
        else:  # PHASE_EXPIRE: b = 0 journey, 1 wait
            if outcome[a] is not None or (b == 1 and physical[a] != WAITING):
                continue
            outcome[a] = UNSERVED
            vid = vehicle_of[a] if physical[a] == ONBOARD else None
            emit(Event(t, PHASE_EXPIRE, sid, "expired", pax[a].passenger_id, vid,
                       None if vid is None else load.get(vid, 0)))
    return SimResult(events, pax, tuple(refused), refusal_events, drain)
