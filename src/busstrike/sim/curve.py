"""노선별 대수 곡선 S(n)과 정류장 공유 클러스터(simulator.md §7.1·§7.2).

파일을 쓰지 않고 엔진을 메모리에서 반복 실행한다. 모든 점은 `normalize_plan` → `service_schedule` →
`simulate` → `compute_metrics`를 지나므로 디렉터리 단위 `run()`과 같은 규칙이다(공정성 §1.6).
충족 인원은 M07 M1.served다(여정 기한 안에 목적지 하차).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .engine import SimContext, service_schedule, simulate
from .metrics import compute_metrics, passenger_outcomes
from .models import Passenger, PlanSpec, RouteSpec, SimError, Vehicle
from .timetable import normalize_plan


@dataclass(frozen=True)
class CurvePoint:
    """대수 n 한 점. `simulated=False`면 포화 뒤 채운 점이라 대기·거리는 없다(§7.1)."""

    n: int
    satisfied: int
    transported: int
    eligible: int
    wait_p90_s: int | None
    total_m: int | None
    vehicles_used: int | None
    simulated: bool = True


def service_patterns(route: RouteSpec) -> tuple[tuple[str, ...], ...]:
    return tuple(leg.stop_ids for leg in route.legs if leg.in_service)


def _servable(patterns: Sequence[tuple[str, ...]], p: Passenger) -> bool:
    """정적 판정(계약 §4.6 no_connection의 반대): 어떤 영업 leg에서 origin 뒤에 destination이 온다."""
    for stops in patterns:
        for k, s in enumerate(stops[:-1]):
            if s == p.origin and p.destination in stops[k + 1:]:
                return True
    return False


def eligible_passengers(route: RouteSpec, ctx: SimContext) -> tuple[Passenger, ...]:
    """노선 r의 영업 leg로 정적 서비스 가능한 승객(ctx 순서 유지). 대수와 무관하다."""
    patterns = service_patterns(route)
    cache: dict[tuple[str, str], bool] = {}
    out = []
    for p in ctx.passengers:
        key = (p.origin, p.destination)
        ok = cache.get(key)
        if ok is None:
            ok = cache[key] = _servable(patterns, p)
        if ok:
            out.append(p)
    return tuple(out)


def _reference_fleet(ctx: SimContext, count: int) -> list[Vehicle]:
    ref = sorted(ctx.vehicles.values(), key=lambda v: v.vehicle_id)
    if count > len(ref):
        raise SimError(SimError.PLAN_INFEASIBLE, "requested vehicles exceed available fleet",
                       {"requested": count, "available": len(ref)})
    return ref[:count]


def _simulate_point(routes: Sequence[RouteSpec], vehicles: Sequence[Vehicle], passengers: Sequence[Passenger],
                    ctx: SimContext, plan_id: str) -> CurvePoint:
    plan = PlanSpec(plan_id, "curve", 1, "draft", tuple(routes))
    timetable = normalize_plan(plan, vehicles, ctx.window, ctx.policy, ctx.tt)
    schedule = service_schedule(timetable, ctx)
    result = simulate(ctx, schedule, passengers)
    metrics = compute_metrics(result, passenger_outcomes(result, schedule), schedule, stop_area={},
                              wait_threshold_s=None, max_headway_s=None)
    return CurvePoint(
        n=len(vehicles),
        satisfied=metrics["M1"]["served"],
        transported=metrics["physical_at_drain"]["arrived"],
        eligible=len(passengers),
        wait_p90_s=metrics["M3"]["boarded"]["p90_s"],
        total_m=sum(v["distance_m"] for v in metrics["M6"]["vehicles"]),
        vehicles_used=len(metrics["M6"]["vehicles"]),
    )


def fleet_curve(route: RouteSpec, n_max: int, ctx: SimContext, *,
                stop_when_saturated: bool = True) -> list[CurvePoint]:
    """S_r(n), n = 0 … n_max. 노선을 혼자, 그 노선으로 갈 수 있는 승객만 넣어 돌린다.

    차량은 ctx의 fleet을 vehicle_id 순으로 정렬한 앞 n대. 값은 다듬지 않는다(단조·오목 아닐 수 있음).
    `stop_when_saturated`면 eligible 전원이 충족된 뒤의 n은 실행하지 않고 인원만 이어 쓴다.
    """
    if isinstance(n_max, bool) or not isinstance(n_max, int) or n_max < 0:
        raise SimError(SimError.INPUT_INVALID, "n_max must be int >= 0")
    ref = _reference_fleet(ctx, n_max)
    pax = eligible_passengers(route, ctx)
    points: list[CurvePoint] = []
    for n in range(n_max + 1):
        prev = points[-1] if points else None
        if stop_when_saturated and prev is not None and prev.satisfied == len(pax):
            points.append(CurvePoint(n, prev.satisfied, prev.transported, len(pax), None, None, None, False))
            continue
        spec = replace(route, vehicle_count=n, vehicle_ids=None)
        points.append(_simulate_point([spec], ref[:n], pax, ctx, f"curve-{route.route_id}-n{n}"))
    return points


def marginal_gains(points: Sequence[CurvePoint]) -> list[int | None]:
    """한계효용: S(n) − S(n−1). n=0은 None."""
    return [None if i == 0 else points[i].satisfied - points[i - 1].satisfied for i in range(len(points))]


def route_clusters(routes: Sequence[RouteSpec], ctx: SimContext) -> list[list[str]]:
    """공통 eligible 승객이 있는 노선끼리 묶은 연결 성분(§7.2). 성분·목록 모두 route_id 순."""
    ids = sorted(r.route_id for r in routes)
    parent = {r: r for r in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for r in sorted(routes, key=lambda x: x.route_id):
        for p in eligible_passengers(r, ctx):
            first = owner.setdefault(p.passenger_id, r.route_id)
            a, b = find(first), find(r.route_id)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups: dict[str, list[str]] = {}
    for r in ids:
        groups.setdefault(find(r), []).append(r)
    return sorted(groups.values(), key=lambda g: g[0])


def overlap_ratios(routes: Sequence[RouteSpec], ctx: SimContext) -> dict[str, float | None]:
    """|다른 노선과 겹치는 eligible 승객| / |eligible[r]| (eligible 0이면 None)."""
    eligible = {r.route_id: {p.passenger_id for p in eligible_passengers(r, ctx)} for r in routes}
    count: dict[str, int] = {}
    for ids in eligible.values():
        for pid in ids:
            count[pid] = count.get(pid, 0) + 1
    out: dict[str, float | None] = {}
    for rid in sorted(eligible):
        ids = eligible[rid]
        shared = sum(1 for pid in ids if count[pid] > 1)
        out[rid] = None if not ids else round(shared / len(ids), 6)
    return out


def cluster_eval(routes: Sequence[RouteSpec], allocation: Mapping[str, int], ctx: SimContext) -> CurvePoint:
    """클러스터(또는 노선 묶음) 전체를 한 번에 실행. 승객은 묶음 중 한 노선이라도 갈 수 있는 사람만.

    `allocation`에 없는 노선은 0대. 차량은 fleet 앞에서부터 route_id 순으로 배정된다(§1.4).
    """
    specs = []
    for r in sorted(routes, key=lambda x: x.route_id):
        n = allocation.get(r.route_id, 0)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise SimError(SimError.INPUT_INVALID, "allocation must be int >= 0", {"route_id": r.route_id})
        specs.append(replace(r, vehicle_count=n, vehicle_ids=None))
    unknown = set(allocation) - {r.route_id for r in routes}
    if unknown:
        raise SimError(SimError.INPUT_INVALID, "allocation has routes outside the cluster",
                       {"route_ids": sorted(unknown)})
    ref = _reference_fleet(ctx, sum(s.vehicle_count for s in specs))
    seen: set[str] = set()
    pax: list[Passenger] = []
    for r in specs:
        for p in eligible_passengers(r, ctx):
            if p.passenger_id not in seen:
                seen.add(p.passenger_id)
                pax.append(p)
    pax.sort(key=lambda p: (p.arrival_s, p.passenger_id))
    plan_id = "cluster-" + "-".join(f"{s.route_id}x{s.vehicle_count}" for s in specs)
    return _simulate_point(specs, ref, pax, ctx, plan_id)
