"""지표 계산(simulator.md §6.3). 엔진 상태가 아니라 로그 + 입력에서만 계산한다."""

from __future__ import annotations

from typing import Iterable, Sequence

from .models import Event, Passenger, Policy, Timetable, Vehicle
from .traveltime import TravelTimeTable

CAUSES = ("FULL", "JOURNEY_EXCEEDED", "NO_ROUTE_AT_STOP", "NO_ROUTE_TO_DEST", "SERVICE_ENDED", "WAIT_EXCEEDED")
METRICS_SCHEMA_VERSION = 1


def _ratio(num: int, den: int, digits: int = 6) -> float | None:
    return None if den == 0 else round(num / den, digits)


def _mean(values: Sequence[int]) -> float | None:
    return None if not values else round(sum(values) / len(values), 3)


def nearest_rank(sorted_values: Sequence[int], p: int) -> int | None:
    """오름차순 N개 중 ceil(p·N/100)번째(1부터)."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = max(1, -(-p * n // 100))
    return sorted_values[rank - 1]


def stats(values: Iterable[int]) -> dict:
    v = sorted(values)
    return {
        "count": len(v),
        "mean": _mean(v),
        "p50": nearest_rank(v, 50),
        "p90": nearest_rank(v, 90),
        "p95": nearest_rank(v, 95),
        "max": v[-1] if v else None,
    }


def compute_metrics(events: Sequence[Event], passengers: Sequence[Passenger], timetable: Timetable,
                    vehicles: Sequence[Vehicle], policy: Policy, tt: TravelTimeTable) -> dict:
    vehicle_by_id = {v.vehicle_id: v for v in vehicles}
    trips_by_id = {t.trip_id: t for t in timetable.trips}

    board: dict[str, Event] = {}
    alight: dict[str, int] = {}
    outcome: dict[str, tuple[str, str]] = {}
    denied_pids: set[str] = set()
    trip_arrives: dict[str, list[Event]] = {}
    vehicle_times: dict[str, list[int]] = {}
    boardings_by_vehicle: dict[str, int] = {}
    left_behind_total = 0
    for e in events:
        typ = e.type
        if typ == "arrive":
            trip_arrives.setdefault(e.trip_id, []).append(e)
            vehicle_times.setdefault(e.vehicle_id, []).append(e.t)
            left_behind_total += e.left_behind
        elif typ == "depart":
            vehicle_times.setdefault(e.vehicle_id, []).append(e.t)
        elif typ == "board":
            board[e.passenger_id] = e
            boardings_by_vehicle[e.vehicle_id] = boardings_by_vehicle.get(e.vehicle_id, 0) + 1
        elif typ == "alight":
            alight[e.passenger_id] = e.t
        elif typ == "denied":
            denied_pids.add(e.passenger_id)
        elif typ in ("no_service", "abandon", "left_waiting"):
            outcome[e.passenger_id] = (typ, e.cause)

    ordered = sorted(passengers, key=lambda p: p.passenger_id)
    satisfied: set[str] = set()
    for p in ordered:
        if p.passenger_id in alight and alight[p.passenger_id] - p.arrival_s <= policy.max_journey_s:
            satisfied.add(p.passenger_id)

    def block(group: Sequence[Passenger]) -> dict:
        gen = len(group)
        tr = sum(1 for p in group if p.passenger_id in alight)
        sat = sum(1 for p in group if p.passenger_id in satisfied)
        kinds = [outcome.get(p.passenger_id, ("", ""))[0] for p in group]
        return {
            "generated": gen,
            "transported": tr,
            "satisfied": sat,
            "abandoned": kinds.count("abandon"),
            "no_service": kinds.count("no_service"),
            "left_waiting": kinds.count("left_waiting"),
            "transport_rate": _ratio(tr, gen),
            "satisfaction_rate": _ratio(sat, gen),
        }

    passengers_block = block(ordered)
    passengers_block["by_kind"] = {k: block([p for p in ordered if p.kind == k]) for k in ("access", "egress")}

    # ---- 대기·차내·총 이동
    waits = [board[p.passenger_id].t - p.arrival_s for p in ordered if p.passenger_id in board]
    in_vehicle = [alight[p.passenger_id] - board[p.passenger_id].t for p in ordered if p.passenger_id in alight]
    journey = [alight[p.passenger_id] - p.arrival_s for p in ordered if p.passenger_id in alight]

    # ---- 미충족 원인
    by_cause = {c: 0 for c in CAUSES}
    by_stop: dict[tuple[str, str], int] = {}
    for p in ordered:
        if p.passenger_id in alight:
            if p.passenger_id in satisfied:
                continue
            cause = "JOURNEY_EXCEEDED"
        else:
            cause = outcome[p.passenger_id][1]
        by_cause[cause] += 1
        key = (p.origin_stop_ids[0], cause)
        by_stop[key] = by_stop.get(key, 0) + 1

    # ---- trip·차량·노선
    route_of_vehicle = {vid: r.route_id for r in timetable.routes for vid in r.vehicle_ids}
    trip_rows: list[dict] = []
    vehicle_acc: dict[str, dict] = {}
    executed_cycles: dict[str, set[int]] = {}
    planned_cycles: dict[str, set[int]] = {}
    for t in timetable.trips:
        planned_cycles.setdefault(t.vehicle_id, set()).add(t.cycle_index)
        arrives = sorted(trip_arrives.get(t.trip_id, ()), key=lambda e: e.stop_seq)
        if not arrives:
            continue
        executed_cycles.setdefault(t.vehicle_id, set()).add(t.cycle_index)
        acc = vehicle_acc.setdefault(t.vehicle_id, {"revenue_m": 0, "deadhead_m": 0, "revenue_s": 0, "deadhead_s": 0})
        stops = t.stop_ids
        dist = sum(tt.distance(stops[e.stop_seq - 1], stops[e.stop_seq]) for e in arrives if e.stop_seq > 0)
        secs = arrives[-1].t - arrives[0].t
        if t.in_service:
            acc["revenue_m"] += dist
            acc["revenue_s"] += secs
            v = vehicle_by_id[t.vehicle_id]
            max_load = max(e.load for e in arrives)
            trip_rows.append({
                "trip_id": t.trip_id,
                "route_id": t.route_id,
                "vehicle_id": t.vehicle_id,
                "boardings": sum(e.boarded for e in arrives),
                "max_load": max_load,
                "load_factor_seat": _ratio(max_load, v.seat_capacity),
                "load_factor_total": _ratio(max_load, v.capacity),
                "start_delay_s": arrives[0].t - t.stop_times[0].sched_s,
                "end_s": arrives[-1].t,
            })
        else:
            acc["deadhead_m"] += dist
            acc["deadhead_s"] += secs

    vehicle_rows: list[dict] = []
    for vid in sorted(route_of_vehicle):
        acc = vehicle_acc.get(vid, {"revenue_m": 0, "deadhead_m": 0, "revenue_s": 0, "deadhead_s": 0})
        times = vehicle_times.get(vid)
        first = min(times) if times else None
        last = max(times) if times else None
        run_cycles = len(executed_cycles.get(vid, ()))
        vehicle_rows.append({
            "vehicle_id": vid,
            "route_id": route_of_vehicle[vid],
            "revenue_m": acc["revenue_m"],
            "deadhead_m": acc["deadhead_m"],
            "first_s": first,
            "last_s": last,
            "span_s": None if times is None else last - first,
            "revenue_s": acc["revenue_s"],
            "deadhead_s": acc["deadhead_s"],
            "boardings": boardings_by_vehicle.get(vid, 0),
            "cycles_run": run_cycles,
            "cycles_cancelled": len(planned_cycles.get(vid, ())) - run_cycles,
        })
    used = [r for r in vehicle_rows if r["first_s"] is not None]
    total_boardings = sum(r["boardings"] for r in vehicle_rows)
    revenue_m = sum(r["revenue_m"] for r in vehicle_rows)
    deadhead_m = sum(r["deadhead_m"] for r in vehicle_rows)
    fleet = {
        "vehicles_used": len(used),
        "total_m": revenue_m + deadhead_m,
        "revenue_m": revenue_m,
        "deadhead_m": deadhead_m,
        "total_span_s": sum(r["span_s"] for r in used),
        "boardings_per_vehicle": _ratio(total_boardings, len(used), 3),
    }

    route_waits: dict[str, list[int]] = {}
    route_satisfied: dict[str, int] = {}
    for p in ordered:
        e = board.get(p.passenger_id)
        if e is None:
            continue
        rid = trips_by_id[e.trip_id].route_id
        route_waits.setdefault(rid, []).append(e.t - p.arrival_s)
        if p.passenger_id in satisfied:
            route_satisfied[rid] = route_satisfied.get(rid, 0) + 1
    route_rows: list[dict] = []
    for r in timetable.routes:
        rows = [x for x in trip_rows if x["route_id"] == r.route_id]
        route_rows.append({
            "route_id": r.route_id,
            "vehicles": len(r.vehicle_ids),
            "trips_run": len(rows),
            "boardings": sum(x["boardings"] for x in rows),
            "satisfied": route_satisfied.get(r.route_id, 0),
            "mean_wait_s": _mean(route_waits.get(r.route_id, [])),
            "max_load_factor_total": max((x["load_factor_total"] for x in rows), default=None),
        })

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "plan_id": timetable.plan_id,
        "plan_revision": timetable.plan_revision,
        "scope_mode": "single_bus_leg",
        "passengers": passengers_block,
        "wait_s": stats(waits),
        "in_vehicle_s": stats(in_vehicle),
        "journey_s": stats(journey),
        "denied": {"boardings": left_behind_total, "passengers": len(denied_pids)},
        "unserved": {
            "by_cause": by_cause,
            "by_stop": [{"stop_id": s, "cause": c, "count": n} for (s, c), n in sorted(by_stop.items())],
        },
        "vehicles": vehicle_rows,
        "fleet": fleet,
        "trips": trip_rows,
        "routes": route_rows,
    }
