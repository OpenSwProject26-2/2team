"""승객 결과·지표(simulator.md §6.3, m07-output-contract.md §2.5·§2.6·§4.5–§4.8).

지표는 엔진 안에서 세지 않고 **사건 로그 + 입력(일정·승객)**에서 계산한다. 로그로 알 수 없는 만차 거절
횟수만 엔진 결과(`SimResult.refused`)에서 받는다(M07 로그에는 거절 사건이 없다). 실수 값은 M07 검증기가
`==`로 비교하므로 계약에 적힌 식(`served / total`, `sum(waits) / len(waits)`)을 그대로 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Mapping, Sequence

from .engine import ARRIVED, CENSORED, ONBOARD, SERVED, UNSERVED, WAITING, SimResult
from .models import Event, ServiceTrip

METRICS_SCHEMA_VERSION = 2
REASONS = ("no_connection", "service_end", "journey", "wait")
UNKNOWN_AREA = "unknown"
PROVISIONAL = "독립 검증 전 잠정 (provisional, not independently verified)"


@dataclass(frozen=True)
class Outcome:
    """`passenger_outcomes.csv` 1행."""

    passenger_id: str
    origin: str
    destination: str
    time_s: int
    outcome: str
    reason: str | None
    physical: str
    vehicle_id: str | None
    boarding_s: int | None
    arrival_s: int | None
    refused: int
    board_vehicle_id: str | None   # CSV에 쓰지 않음. M6 집계용


class Connectivity:
    """출발지 방문 i, 목적지 방문 j > i가 같은 운행편에 있는지와 그런 운행편의 출발지 도착 시각(§4.6)."""

    def __init__(self, schedule: Sequence[ServiceTrip]):
        self._index: list[tuple[ServiceTrip, dict[str, list[int]]]] = []
        for trip in schedule:
            pos: dict[str, list[int]] = {}
            for k, s in enumerate(trip.stops):
                pos.setdefault(s, []).append(k)
            self._index.append((trip, pos))
        self._cache: dict[tuple[str, str], tuple[int, ...]] = {}

    def origin_arrivals(self, origin: str, destination: str) -> tuple[int, ...]:
        key = (origin, destination)
        found = self._cache.get(key)
        if found is None:
            times = []
            for trip, pos in self._index:
                o, d = pos.get(origin), pos.get(destination)
                if o and d:
                    times.extend(trip.arrivals[i] for i in o if i < d[-1])
            found = self._cache[key] = tuple(sorted(times))
        return found

    def reason(self, origin: str, destination: str, time_s: int, drain_s: int, expiry: str) -> str:
        arrivals = self.origin_arrivals(origin, destination)
        if not arrivals:
            return "no_connection"
        if not any(time_s <= t <= drain_s for t in arrivals):
            return "service_end"
        return expiry


def passenger_outcomes(result: SimResult, schedule: Sequence[ServiceTrip]) -> list[Outcome]:
    """사건 로그에서 승객별 결과를 정한다(§4.5). 행은 passenger_id 문자열 순."""
    boarded: dict[str, Event] = {}
    alighted: dict[str, int] = {}
    expired: dict[str, Event] = {}
    for e in result.events:
        if e.kind == "boarded":
            boarded[e.passenger_id] = e
        elif e.kind == "alighted":
            alighted[e.passenger_id] = e.time_s
        elif e.kind == "expired":
            expired[e.passenger_id] = e
    conn = Connectivity(schedule)
    rows: list[Outcome] = []
    for p, refused in sorted(zip(result.passengers, result.refused), key=lambda x: x[0].passenger_id):
        pid = p.passenger_id
        board = boarded.get(pid)
        arrival = alighted.get(pid)
        physical = ARRIVED if arrival is not None else (ONBOARD if board is not None else WAITING)
        exp = expired.get(pid)
        if exp is not None:
            outcome = UNSERVED
            reason = conn.reason(p.origin, p.destination, p.arrival_s, result.drain_s, exp.stable_id.split(":", 1)[0])
        elif arrival is not None:
            outcome, reason = SERVED, None
        else:
            outcome, reason = CENSORED, None
        rows.append(Outcome(pid, p.origin, p.destination, p.arrival_s, outcome, reason, physical,
                            board.vehicle_id if physical == ONBOARD else None,
                            None if board is None else board.time_s, arrival, refused,
                            None if board is None else board.vehicle_id))
    return rows


# ---------------------------------------------------------------- 지표

def nearest_rank_p90(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, ceil(0.9 * len(ordered))) - 1]


def wait_summary(waits: Sequence[int], threshold_s: int | None) -> dict:
    n = len(waits)
    over = None if threshold_s is None else sum(1 for w in waits if w > threshold_s)
    return {"n": n,
            "mean_s": (sum(waits) / n) if n else None,
            "p90_s": nearest_rank_p90(waits),
            "over_threshold_n": over,
            "over_threshold_rate": (over / n) if (n and over is not None) else None,
            "threshold_s": threshold_s}


def headway_report(schedule: Sequence[ServiceTrip], max_headway_s: int) -> dict:
    """§4.8 R12: 노선·정류장별 연속 출발 간격."""
    by_route_stop: dict[tuple[str, str], list[int]] = {}
    for trip in schedule:
        for stop, dep in zip(trip.stops, trip.departures):
            by_route_stop.setdefault((trip.route_id, stop), []).append(dep)
    max_gap: dict[str, int] = {}
    violations = []
    for (route, stop), deps in sorted(by_route_stop.items()):
        deps.sort()
        for before, after in zip(deps, deps[1:]):
            gap = after - before
            if gap > max_gap.get(route, -1):
                max_gap[route] = gap
            if gap > max_headway_s:
                violations.append({"route": route, "stop": stop, "from_s": before, "to_s": after, "gap_s": gap})
    return {"rule": "R12", "max_headway_s": max_headway_s, "max_gap_by_route_s": dict(sorted(max_gap.items())),
            "violation_count": len(violations), "pass": not violations, "violations": violations,
            "not_checked": "window-edge gaps; routes with a single departure per stop are omitted"}


def compute_metrics(result: SimResult, outcomes: Sequence[Outcome], schedule: Sequence[ServiceTrip], *,
                    stop_area: Mapping[str, str], wait_threshold_s: int | None,
                    max_headway_s: int | None) -> dict:
    drain = result.drain_s
    tally = {SERVED: 0, UNSERVED: 0, CENSORED: 0}
    reasons = {r: 0 for r in REASONS}
    not_boarded = {r: 0 for r in REASONS}
    refused_by = {SERVED: 0, UNSERVED: 0, CENSORED: 0}
    physical = {WAITING: 0, ONBOARD: 0, ARRIVED: 0}
    areas: dict[str, dict[str, int]] = {}
    served_waits: list[int] = []
    boarded_waits: list[int] = []
    boarded_by_vehicle: dict[str, int] = {}
    served_by_vehicle: dict[str, int] = {}
    for o in outcomes:
        tally[o.outcome] += 1
        physical[o.physical] += 1
        if o.outcome == UNSERVED:
            reasons[o.reason] += 1
            if o.boarding_s is None:
                not_boarded[o.reason] += 1
        if o.refused:
            refused_by[o.outcome] += 1
        a = areas.setdefault(stop_area.get(o.origin, UNKNOWN_AREA),
                             {"total": 0, SERVED: 0, UNSERVED: 0, CENSORED: 0})
        a["total"] += 1
        a[o.outcome] += 1
        if o.boarding_s is not None:
            wait = o.boarding_s - o.time_s
            boarded_waits.append(wait)
            boarded_by_vehicle[o.board_vehicle_id] = boarded_by_vehicle.get(o.board_vehicle_id, 0) + 1
            if o.outcome == SERVED:
                served_waits.append(wait)
                served_by_vehicle[o.board_vehicle_id] = served_by_vehicle.get(o.board_vehicle_id, 0) + 1

    total = sum(tally.values())
    served, censored = tally[SERVED], tally[CENSORED]
    if total == 0:
        rate = lower = upper = None
    elif censored == 0:
        rate = lower = upper = served / total
    else:
        rate, lower, upper = None, served / total, (served + censored) / total

    by_area = {area: {"total": b["total"], "served": b[SERVED], "unserved": b[UNSERVED], "censored": b[CENSORED],
                      "served_rate": b[SERVED] / b["total"]}
               for area, b in sorted(areas.items())}
    worst = min(((v["served_rate"], area) for area, v in by_area.items()), default=None)

    # M6: 차량별(§4.7). 출발 시 탑승 인원은 departed 사건에서 읽는다.
    dep_occ = {e.stable_id: e.occupancy for e in result.events if e.kind == "departed"}
    vehicles: dict[str, dict] = {}
    for trip in schedule:
        row = vehicles.setdefault(trip.vehicle_id, {
            "vehicle_id": trip.vehicle_id, "trips_completed": 0, "boarded": 0, "served_alighted": 0,
            "service_time_s": 0, "distance_m": 0, "empty_time_s": 0})
        if trip.return_s <= drain:
            row["trips_completed"] += 1
            row["service_time_s"] += trip.return_s - trip.arrivals[0]
            row["distance_m"] += trip.distance_m
            row["empty_time_s"] += trip.return_s - trip.departures[-1]
        for k in range(len(trip.stops) - 1):
            if dep_occ.get(trip.stable_id(k)) == 0 and trip.arrivals[k + 1] <= drain:
                row["empty_time_s"] += trip.arrivals[k + 1] - trip.departures[k]
    for vid, row in vehicles.items():
        row["boarded"] = boarded_by_vehicle.get(vid, 0)
        row["served_alighted"] = served_by_vehicle.get(vid, 0)

    metrics = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "label": PROVISIONAL,
        "scope_mode": "single_bus_leg",
        "M1": {"served": served, "unserved": tally[UNSERVED], "censored": censored, "total": total,
               "rate": rate, "lower": lower, "upper": upper,
               "note": "rate is NA when total=0; with censored>0 report lower/upper only"},
        "M2": {"unserved_by_reason": reasons, "reason_priority": list(REASONS),
               "refusal_events": result.refusal_events, "passengers_refused_by_outcome": refused_by},
        "M3": {"served": dict(wait_summary(served_waits, wait_threshold_s),
                              population="served (boarded and arrived by deadline)"),
               "boarded": dict(wait_summary(boarded_waits, wait_threshold_s),
                               population="boarded (includes later journey-unserved and censored onboard)"),
               "not_boarded_unserved_by_reason": not_boarded},
        "M5": {"by_origin_area": by_area,
               "min_served_rate": None if worst is None else {"area": worst[1], "rate": worst[0]}},
        "M6": {"vehicles": [vehicles[v] for v in sorted(vehicles)],
               "empty_distance_m": None, "empty_distance_note": "NA: no per-segment empty distance (contract §2.6)"},
        "physical_at_drain": physical,
        "drain_s": drain,
    }
    if max_headway_s is not None:
        metrics["G"] = {"R12": headway_report(schedule, max_headway_s)}
    return metrics
