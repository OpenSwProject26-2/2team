"""C(M04 planning) 입력·운행안 → M05 시뮬레이터 입력 어댑터와 근사–시뮬 비교(simulator.md §7, §10).

- `to_sim_inputs`·`plan_dict`: C `Inputs`·`Plan`을 `scenario.json`·`travel_times.json`·`arrivals.jsonl`·
  `plan.json`으로 바꾼다(타입 변환만, 규칙 추가 없음).
- `run_plan`·`evaluation_port`: 어댑터 → 시간표 생성 → 시뮬레이션 → 독립 검증기 → 지표 → C `Assessment`.
- `curve_report`: 노선별 S(n)·한계효용과 같은 대수의 C 근사값.
- `compare_with_estimate`: 같은 운행안에서 C `evaluate()`와 시뮬레이션 결과를 나란히 놓는다(판정하지 않음).

C의 코드는 import만 하고 바꾸지 않는다. CLI::

    python -m busstrike.sim.adapters.planning_bridge --input tests/fixtures/planning/synthetic.json --out DIR
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from math import comb, isfinite
from pathlib import Path
from typing import Callable, Iterable, Sequence

from busstrike.evaluation.validator import VALIDATION_FILE, failed_checks, validate_run, write_report
from busstrike.planning.baselines import baseline
from busstrike.planning.candidates import candidates
from busstrike.planning.evaluator import evaluate
from busstrike.planning.integration import Assessment
from busstrike.planning.models import InputError, Inputs, Plan, Route, load_inputs, make_plan
from busstrike.planning.search import search

from .. import io
from ..arrivals import build_run_inputs, write_arrivals
from ..curve import fleet_curve, marginal_gains, overlap_ratios, route_clusters
from ..engine import SimContext, prepare
from ..models import Leg, Passenger, RouteSpec, SimError
from ..run import run
from ..traveltime import TravelTimeTable

BRIDGE_VERSION = "busstrike-sim-planning-bridge/0.1.0"
ARRIVAL_METHOD = "precomputed"      # C 수요는 이미 정수 인원·도착 시각이다(실현기를 거치지 않음)
SEED = "none"                       # 난수를 쓰지 않았다는 기록값(M00 RunInputs.seed는 string|integer)
VEHICLE_CLASS = "planning-uniform"  # C 첫 버전은 동일 조건 차량만 받는다
PASSENGER_KIND = "access"           # C 수요는 정류장→정류장. access/egress 구분이 없다
DEMAND_INDEX_FILE = "demand_index.json"


# ---------------------------------------------------------------- 입력 변환

def _segments(inputs: Inputs) -> dict:
    return {s.segment_id: s for s in inputs.segments}


def route_stops(inputs: Inputs, segment_ids: Sequence[str]) -> tuple[str, ...]:
    edges = _segments(inputs)
    try:
        first = edges[segment_ids[0]]
        return (first.origin,) + tuple(edges[s].destination for s in segment_ids)
    except (IndexError, KeyError):
        raise SimError(SimError.PLAN_INFEASIBLE, "route references unknown segments") from None


def route_spec(inputs: Inputs, route: Route, vehicle_count: int) -> RouteSpec:
    """C `Route`: outbound → 영업 leg, return_segments → 공차 leg(§0.3)."""
    return RouteSpec(route.route_id, (Leg(True, route_stops(inputs, route.outbound)),
                                      Leg(False, route_stops(inputs, route.return_segments))), vehicle_count)


def _leg_dicts(spec: RouteSpec) -> list[dict]:
    return [{"in_service": leg.in_service, "stop_ids": list(leg.stop_ids)} for leg in spec.legs]


def scenario_dict(inputs: Inputs) -> dict:
    p = inputs.policy
    return {
        "scenario_id": inputs.scenario_id,
        "service_date": inputs.service_date,
        "timezone": "Asia/Seoul",
        "network_snapshot_id": inputs.network_version,
        "demand_profile_id": inputs.demand_version,
        "strike_route_ids": [],
        "operating_window": {"start_s": p.start_s, "end_s": p.end_s},
        "fleet": {
            "authorized_count": len(inputs.fleet),
            "vehicles": [{
                "vehicle_id": v.vehicle_id,
                "vehicle_class": VEHICLE_CLASS,
                "seat_capacity": v.capacity,
                "standing_capacity": 0,
                "available_start_s": v.available_start_s,
                "available_end_s": v.available_end_s,
                "initial_stop_id": v.initial_stop,
                "end_stop_id": v.return_stop,
            } for v in sorted(inputs.fleet, key=lambda v: v.vehicle_id)],
        },
        "policy": {
            "max_wait_s": p.max_wait_s,
            "max_journey_s": p.max_travel_s,
            "allowed_transfers": 0,
            "arrival_method": ARRIVAL_METHOD,
            "dwell_base_s": p.dwell_s,
            "dwell_per_boarding_s": 0,
            "dwell_per_alighting_s": 0,
            "turnaround_s": p.turnaround_s,
            "cycle_rest_s": 0,
            "unknown_link": "error",
        },
    }


def travel_times_dict(inputs: Inputs) -> dict:
    """C 구간(고정 travel_s) → 시간대 1개짜리 프로필. 같은 정류장쌍에 값이 다른 구간이 있으면 거절."""
    pairs: dict[tuple[str, str], tuple[int, int, str]] = {}
    for s in sorted(inputs.segments, key=lambda x: x.segment_id):
        key = (s.origin, s.destination)
        old = pairs.get(key)
        if old is not None and old[:2] != (s.distance_m, s.travel_s):
            raise SimError(SimError.INPUT_INVALID, "parallel segments with different values for one stop pair",
                           {"segments": [old[2], s.segment_id]})
        pairs.setdefault(key, (s.distance_m, s.travel_s, s.segment_id))
    until = max([inputs.policy.end_s] + [v.available_end_s for v in inputs.fleet])
    return {
        "schema_version": 1,
        "network_snapshot_id": inputs.network_version,
        "valid_from_s": 0,
        "valid_until_s": until,
        "segments": [{"from_stop_id": a, "to_stop_id": b, "distance_m": d, "profile": [[0, t]]}
                     for (a, b), (d, t, _) in sorted(pairs.items())],
    }


def passengers_of(inputs: Inputs) -> tuple[list[Passenger], dict[str, str]]:
    """수요 묶음 → 1명씩. ID `c{수요 순번:06d}-{k:05d}`(수요 순번은 demand_id 정렬 순).

    같은 도착 시각이면 (arrival_s, passenger_id) 순 = C의 (arrival_s, demand_id) 순이 되도록 순번을 쓴다.
    두 번째 값은 ID 접두어 → demand_id 표.
    """
    p = inputs.policy
    out: list[Passenger] = []
    index: dict[str, str] = {}
    for i, d in enumerate(sorted(inputs.demands, key=lambda x: x.demand_id), start=1):
        prefix = f"c{i:06d}"
        index[prefix] = d.demand_id
        if d.passenger_count and not p.start_s <= d.arrival_s <= p.end_s:
            raise SimError(SimError.INPUT_INVALID, "demand arrival outside operating window",
                           {"demand_id": d.demand_id, "arrival_s": d.arrival_s})
        for k in range(1, d.passenger_count + 1):
            out.append(Passenger(f"{prefix}-{k:05d}", PASSENGER_KIND, (d.origin,), (d.destination,),
                                 d.arrival_s, p.max_wait_s))
    out.sort(key=lambda x: (x.arrival_s, x.passenger_id))
    return out, index


def plan_dict(plan: Plan, inputs: Inputs) -> dict:
    """C `Plan` → `plan.json`. `Plan.to_service_plan`(C의 배분 검사 포함)의 키를 그대로 두고 `routes`만
    M05 leg 형식으로 바꾼다. 출처 같은 기록용 키는 손대지 않고 옮길 뿐, 어댑터도 읽지 않는다(§1.6).
    """
    service_plan = plan.to_service_plan(inputs)
    routes = {r.route_id: r for r in inputs.routes}
    doc = dict(service_plan)
    doc["routes"] = [{"route_id": r["route_id"], "vehicle_count": r["vehicle_count"],
                      "legs": _leg_dicts(route_spec(inputs, routes[r["route_id"]], r["vehicle_count"]))}
                     for r in service_plan["routes"]]
    return doc


@dataclass(frozen=True)
class BridgeInputs:
    """운행안과 무관한 시뮬레이터 입력(모든 안이 같은 것을 받는다)."""

    input_bundle_id: str
    input_checksum: str
    scenario: dict
    travel_times: dict
    passengers: tuple[Passenger, ...]
    demand_index: dict[str, str]


def to_sim_inputs(inputs: Inputs) -> BridgeInputs:
    pax, index = passengers_of(inputs)
    return BridgeInputs(inputs.input_bundle_id, inputs.checksum, scenario_dict(inputs), travel_times_dict(inputs),
                        tuple(pax), index)


def sim_context(bridge: BridgeInputs) -> SimContext:
    sc = io.scenario_from_dict(bridge.scenario)
    tt = TravelTimeTable.from_dict(bridge.travel_times)
    return prepare(bridge.passengers, sc.vehicles, sc.window, sc.policy, tt)


def write_inputs(bridge: BridgeInputs, d: Path) -> dict[str, Path]:
    """scenario·travel_times·arrivals·run_inputs(+ demand_index)를 한 디렉터리에 쓴다."""
    d = Path(d)
    paths = {name: d / name for name in
             ("scenario.json", "travel_times.json", "arrivals.jsonl", "run_inputs.json", DEMAND_INDEX_FILE)}
    io.write_bytes(paths["scenario.json"], io.json_bytes(bridge.scenario))
    io.write_bytes(paths["travel_times.json"], io.json_bytes(bridge.travel_times))
    write_arrivals(bridge.passengers, paths["arrivals.jsonl"])
    io.write_bytes(paths[DEMAND_INDEX_FILE], io.json_bytes({
        "bridge_version": BRIDGE_VERSION, "planning_input_bundle_id": bridge.input_bundle_id,
        "planning_input_checksum": bridge.input_checksum, "demand_id_by_prefix": bridge.demand_index}))
    build_run_inputs(bridge.input_bundle_id, paths["scenario.json"], paths["arrivals.jsonl"],
                     paths["travel_times.json"], seed=SEED, bin_length_s=1, out_path=paths["run_inputs.json"])
    return paths


# ---------------------------------------------------------------- 실행 + 검증 + Assessment

@dataclass(frozen=True)
class BridgeRun:
    run_dir: Path
    run: dict
    metrics: dict
    validation: dict | None


def run_plan(plan: Plan, inputs: Inputs, out_dir: Path, *, validate: bool = True,
             bridge: BridgeInputs | None = None) -> BridgeRun:
    """한 운행안: 입력 파일 → `run()` → (검증기) → 지표. 모든 파일을 `out_dir` 한 곳에 둔다."""
    bridge = bridge or to_sim_inputs(inputs)
    if bridge.input_checksum != inputs.checksum:
        raise SimError(SimError.INPUT_INVALID, "bridge inputs were built from different planning inputs")
    out_dir = Path(out_dir)
    paths = write_inputs(bridge, out_dir)
    plan_path = out_dir / "plan.json"
    io.write_bytes(plan_path, io.json_bytes(plan_dict(plan, inputs)))
    result = run(paths["scenario.json"], paths["run_inputs.json"], plan_path, out_dir)
    metrics = io.read_json(out_dir / result["metrics_uri"])
    report = None
    if validate:
        report = validate_run(out_dir)
        write_report(report, out_dir / VALIDATION_FILE)
    return BridgeRun(out_dir, result, metrics, report)


def assessment_of(plan: Plan, inputs: Inputs, br: BridgeRun) -> Assessment:
    """§6.3·§8.3: served = satisfied, total = generated, p90 = wait_s.p90, distance = fleet.total_m."""
    p = br.metrics["passengers"]
    if p["generated"] != inputs.total_demand:
        raise SimError(SimError.INPUT_INVALID, "generated passengers differ from planning total demand",
                       {"generated": p["generated"], "total_demand": inputs.total_demand})
    report = br.validation
    return Assessment(
        plan_id=plan.plan_id,
        plan_revision=plan.revision,
        input_bundle_id=inputs.input_bundle_id,
        input_checksum=inputs.checksum,
        schedule_feasible=True,
        independently_verified=bool(report and report["verified"]),
        served=p["satisfied"],
        total=p["generated"],
        p90_wait_s=br.metrics["wait_s"]["p90"],
        distance_m=br.metrics["fleet"]["total_m"],
        violations=tuple(failed_checks(report)) if report else ("NOT_VALIDATED",),
    )


def _infeasible_assessment(plan: Plan, inputs: Inputs, exc: SimError) -> Assessment:
    return Assessment(plan.plan_id, plan.revision, inputs.input_bundle_id, inputs.checksum,
                      schedule_feasible=False, independently_verified=False, served=0,
                      total=inputs.total_demand, p90_wait_s=None, distance_m=0, violations=(exc.code,))


def make_evaluation_port(work_dir: Path | None = None, *,
                         validate: bool = True) -> Callable[[Plan, Inputs], Assessment]:
    """C `integration.EvaluationPort` 구현. `work_dir`를 주면 `work_dir/<plan_id>/`에 실행 파일을 남긴다.

    시간표를 만들 수 없는 안(`PLAN_INFEASIBLE`)은 `schedule_feasible=False`로 돌려주고, 그 밖의
    시뮬레이터 오류(입력·자료 누락)는 C 규약대로 숨기지 않고 던진다.
    """

    def port(plan: Plan, inputs: Inputs) -> Assessment:
        def once(d: Path) -> Assessment:
            try:
                br = run_plan(plan, inputs, d, validate=validate)
            except SimError as exc:
                if exc.code == SimError.PLAN_INFEASIBLE:
                    return _infeasible_assessment(plan, inputs, exc)
                raise
            return assessment_of(plan, inputs, br)

        if work_dir is not None:
            return once(Path(work_dir) / plan.plan_id)
        with tempfile.TemporaryDirectory() as d:
            return once(Path(d))

    return port


def evaluation_port(plan: Plan, inputs: Inputs) -> Assessment:
    """임시 디렉터리에서 실행·검증하고 Assessment만 돌려준다."""
    return make_evaluation_port()(plan, inputs)


# ---------------------------------------------------------------- S(n) 곡선

def _est_served(inputs: Inputs, route_id: str, n: int) -> int:
    return evaluate(inputs, make_plan(inputs, {route_id: n}, "curve-estimate")).served


def curve_report(inputs: Inputs, *, n_max: int | None = None, stop_when_saturated: bool = True,
                 bridge: BridgeInputs | None = None) -> dict:
    """C 후보 노선마다 S_sim(n)·한계효용과 같은 단독 배분의 C 근사 S_est(n)(§7.1·§7.3)."""
    bridge = bridge or to_sim_inputs(inputs)
    ctx = sim_context(bridge)
    accepted, rejected = candidates(inputs)
    n_max = len(inputs.fleet) if n_max is None else n_max
    if n_max > len(inputs.fleet):
        raise SimError(SimError.INPUT_INVALID, "n_max exceeds fleet size", {"n_max": n_max})
    specs = [route_spec(inputs, r, 0) for r in accepted]
    overlap = overlap_ratios(specs, ctx)
    routes = []
    for spec in specs:
        pts = fleet_curve(spec, n_max, ctx, stop_when_saturated=stop_when_saturated)
        est = [_est_served(inputs, spec.route_id, n) for n in range(n_max + 1)]
        sim_gain = marginal_gains(pts)
        sum_sim = sum(p.satisfied for p in pts[1:])
        sum_est = sum(est[1:])
        routes.append({
            "route_id": spec.route_id,
            "eligible": pts[0].eligible,
            "overlap_ratio": overlap[spec.route_id],
            "beta": 1.0 if sum_est == 0 else round(sum_sim / sum_est, 6),
            "points": [{
                "n": p.n,
                "sim_satisfied": p.satisfied,
                "sim_marginal": sim_gain[i],
                "est_served": est[i],
                "est_marginal": None if i == 0 else est[i] - est[i - 1],
                "diff": p.satisfied - est[i],
                "sim_transported": p.transported,
                "sim_wait_p90_s": p.wait_p90_s,
                "sim_total_m": p.total_m,
                "simulated": p.simulated,
            } for i, p in enumerate(pts)],
        })
    return {
        "bridge_version": BRIDGE_VERSION,
        "input_bundle_id": inputs.input_bundle_id,
        "input_checksum": inputs.checksum,
        "n_max": n_max,
        "total_demand": inputs.total_demand,
        "clusters": route_clusters(specs, ctx),
        "rejected_candidates": dict(sorted(rejected.items())),
        "routes": routes,
    }


# ---------------------------------------------------------------- 근사–시뮬 비교

def _nonzero(plan: Plan) -> tuple[tuple[str, int], ...]:
    return tuple((r, n) for r, n in plan.allocation if n)


def comparison_plans(inputs: Inputs, *, exhaustive_limit: int = 200,
                     include_search: bool = True) -> list[tuple[str, Plan]]:
    """(라벨, 운행안): 기준안 2개 + (개발 입력이면) 탐색 상위 3개 + 차량 미사용 포함 전수 배분(조합 수 ≤ 한도).

    라벨은 만든 방법이다(운행안 필드를 읽지 않는다). 같은 배분(0대 노선 제외)은 처음 것만 남긴다.
    C `search()`는 근사 평가를 최대 1,000번 부르므로 큰 입력에서는 `include_search=False`로 끈다.
    """
    plans: list[tuple[str, Plan]] = []
    for label, proportional in (("equal", False), ("proportional", True)):
        try:
            plans.append((label, baseline(inputs, proportional=proportional)))
        except InputError:
            pass
    if inputs.split == "development":
        if include_search:
            plans += [(f"search-top{i}", p.plan) for i, p in enumerate(search(inputs).top, start=1)]
        accepted, _ = candidates(inputs)
        if comb(len(inputs.fleet) + len(accepted), len(accepted)) <= exhaustive_limit:
            plans += [("exhaustive", p) for p in _all_allocations(inputs, accepted)]
    seen: set = set()
    out = []
    for label, p in plans:
        key = _nonzero(p)
        if key not in seen:
            seen.add(key)
            out.append((label, p))
    return out


def _all_allocations(inputs: Inputs, accepted) -> list[Plan]:
    ids = [r.route_id for r in accepted]
    out: list[Plan] = []

    def visit(i: int, remaining: int, alloc: dict[str, int]) -> None:
        if i == len(ids):
            out.append(make_plan(inputs, alloc, "exhaustive"))
            return
        for n in range(remaining + 1):
            visit(i + 1, remaining - n, {**alloc, ids[i]: n})

    visit(0, len(inputs.fleet), {})
    return out


def _avg_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """동률은 평균 순위. 표본 2개 미만이거나 한쪽 분산이 0이면 None."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _avg_ranks(xs), _avg_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return round(sxy / (sxx * syy) ** 0.5, 6)


def _inf(x: float | None) -> float:
    return float("inf") if x is None else x


def compare_with_estimate(inputs: Inputs, plans: Iterable[Plan | tuple[str, Plan]], *,
                          work_dir: Path | None = None, validate: bool = True) -> dict:
    """§7.3 비교표. 차이는 판정하지 않고 열로만 남긴다(diff = sim − est).

    `plans`는 `Plan` 또는 `(라벨, Plan)`. 라벨은 표에 그대로 싣는다.
    """
    bridge = to_sim_inputs(inputs)
    rows: list[dict] = []
    ranked_plans: list[tuple[Plan, object]] = []
    for item in plans:
        label, plan = item if isinstance(item, tuple) else (None, item)
        est = evaluate(inputs, plan)
        row: dict = {
            "plan_id": plan.plan_id,
            "label": label,
            "allocation": dict(_nonzero(plan)),
            "est_served": est.served,
            "est_mean_wait_s": None if est.mean_wait_s is None else round(est.mean_wait_s, 3),
            "est_distance_m": est.distance_m,
        }
        try:
            if work_dir is not None:
                br = run_plan(plan, inputs, Path(work_dir) / plan.plan_id, validate=validate, bridge=bridge)
            else:
                with tempfile.TemporaryDirectory() as d:
                    br = run_plan(plan, inputs, Path(d), validate=validate, bridge=bridge)
        except SimError as exc:
            if exc.code != SimError.PLAN_INFEASIBLE:
                raise
            row.update({"sim_error": exc.code, "sim_satisfied": None, "diff": None, "ratio": None})
            rows.append(row)
            ranked_plans.append((plan, est))
            continue
        m = br.metrics
        sat = m["passengers"]["satisfied"]
        causes = m["unserved"]["by_cause"]
        row.update({
            "sim_satisfied": sat,
            "sim_transported": m["passengers"]["transported"],
            "diff": sat - est.served,
            "ratio": None if est.served == 0 else round(sat / est.served, 6),
            "sim_mean_wait_s": m["wait_s"]["mean"],
            "sim_p90_wait_s": m["wait_s"]["p90"],
            "sim_total_m": m["fleet"]["total_m"],
            "distance_diff_m": m["fleet"]["total_m"] - est.distance_m,
            "sim_unserved_by_cause": causes,
            "sim_denied_passengers": m["denied"]["passengers"],
            "verified": None if br.validation is None else br.validation["verified"],
            "violations": None if br.validation is None else failed_checks(br.validation),
        })
        rows.append(row)
        ranked_plans.append((plan, est))

    order_est = sorted(range(len(rows)), key=lambda i: ranked_plans[i][1].rank(ranked_plans[i][0]))
    for pos, i in enumerate(order_est, start=1):
        rows[i]["rank_est"] = pos
    feasible = [i for i, r in enumerate(rows) if r["sim_satisfied"] is not None]
    order_sim = sorted(feasible, key=lambda i: (-rows[i]["sim_satisfied"], _inf(rows[i]["sim_p90_wait_s"]),
                                                rows[i]["sim_total_m"], ranked_plans[i][0].allocation))
    for i, r in enumerate(rows):
        r["rank_sim"] = None
    for pos, i in enumerate(order_sim, start=1):
        rows[i]["rank_sim"] = pos

    diffs = [rows[i]["diff"] for i in feasible]
    worst = max(feasible, key=lambda i: (abs(rows[i]["diff"]), -i), default=None)
    sim_best = rows[order_sim[0]]["plan_id"] if order_sim else None
    est_top3 = {rows[i]["plan_id"] for i in order_est[:3]}
    summary = {
        "plans": len(rows),
        "sim_feasible": len(feasible),
        "mae": None if not diffs else round(sum(abs(d) for d in diffs) / len(diffs), 6),
        "bias": None if not diffs else round(sum(diffs) / len(diffs), 6),
        "max_abs_diff": None if worst is None else abs(rows[worst]["diff"]),
        "max_abs_diff_plan_id": None if worst is None else rows[worst]["plan_id"],
        "spearman_served": spearman([rows[i]["est_served"] for i in feasible],
                                    [rows[i]["sim_satisfied"] for i in feasible]),
        "est_best_plan_id": rows[order_est[0]]["plan_id"] if rows else None,
        "sim_best_plan_id": sim_best,
        "sim_best_in_est_top3": None if sim_best is None else sim_best in est_top3,
        "distance_mismatch_plans": sum(1 for i in feasible if rows[i]["distance_diff_m"] != 0),
        "unverified_plans": sum(1 for i in feasible if rows[i]["verified"] is False),
    }
    return {
        "bridge_version": BRIDGE_VERSION,
        "input_bundle_id": inputs.input_bundle_id,
        "input_checksum": inputs.checksum,
        "total_demand": inputs.total_demand,
        "rows": rows,
        "summary": summary,
    }


# ---------------------------------------------------------------- 표 렌더링

def _cell(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "예" if x else "아니오"
    if isinstance(x, float):
        return f"{x:g}" if isfinite(x) else "—"
    if isinstance(x, dict):
        return ", ".join(f"{k}={v}" for k, v in x.items()) or "(없음)"
    return str(x)


def _table(header: Sequence[str], rows: Iterable[Sequence]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return lines


def render_markdown(curves: dict, comparison: dict) -> str:
    out = [
        "# 시뮬레이터–C 근사 비교 리포트",
        "",
        f"- 입력: `{comparison['input_bundle_id']}` (checksum `{comparison['input_checksum'][:12]}…`), "
        f"평가 수요 {comparison['total_demand']}명",
        f"- 생성기: `{curves['bridge_version']}`. 시뮬레이션 결과이며 실제 운행 성능이 아니다. 차이는 판정하지 않는다.",
        "",
        "## 노선별 대수 곡선 S(n)",
        "",
        f"클러스터(공통 승객으로 묶인 노선): {curves['clusters']}",
    ]
    if curves["rejected_candidates"]:
        out.append(f"C가 제외한 후보: {curves['rejected_candidates']}")
    for r in curves["routes"]:
        out += ["", f"### {r['route_id']} — eligible {r['eligible']}명, 겹침 비율 {_cell(r['overlap_ratio'])}, "
                    f"β = {_cell(r['beta'])}", ""]
        out += _table(["n", "S_sim(n)", "ΔS_sim", "S_est(n)", "ΔS_est", "sim−est", "수송", "대기 P90(s)",
                       "운행거리(m)", "실행"],
                      ([p["n"], p["sim_satisfied"], p["sim_marginal"], p["est_served"], p["est_marginal"],
                        p["diff"], p["sim_transported"], p["sim_wait_p90_s"], p["sim_total_m"], p["simulated"]]
                       for p in r["points"] if p["n"] >= 1))
    s = comparison["summary"]
    out += ["", "## 같은 운행안의 C 근사 vs 시뮬레이션", ""]
    out += _table(["plan_id", "라벨", "배분", "est 충족", "sim 충족", "sim−est", "비율", "est 평균대기",
                   "sim 평균대기", "sim P90", "est 거리", "sim 거리", "rank est", "rank sim", "검증",
                   "sim 미충족 원인(0 제외)"],
                  ([r["plan_id"], r["label"], r["allocation"], r["est_served"], r["sim_satisfied"], r["diff"],
                    r["ratio"], r["est_mean_wait_s"], r.get("sim_mean_wait_s"), r.get("sim_p90_wait_s"),
                    r["est_distance_m"], r.get("sim_total_m"), r["rank_est"], r["rank_sim"], r.get("verified"),
                    {k: v for k, v in (r.get("sim_unserved_by_cause") or {}).items() if v}]
                   for r in comparison["rows"]))
    out += ["", "요약(판정 아님)", ""]
    out += [f"- {k}: {_cell(v)}" for k, v in s.items()]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m busstrike.sim.adapters.planning_bridge",
                                     description="C 입력으로 S(n) 곡선과 근사–시뮬 비교표를 만든다")
    parser.add_argument("--input", required=True, help="C planning 입력 JSON(합성만)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--n-max", type=int, default=None)
    parser.add_argument("--exhaustive-limit", type=int, default=200)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--no-search", action="store_true", help="C 탐색 상위안을 비교에서 뺀다(큰 입력용)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        inputs = load_inputs(args.input)
        bridge = to_sim_inputs(inputs)
        curves = curve_report(inputs, n_max=args.n_max, bridge=bridge)
        plans = comparison_plans(inputs, exhaustive_limit=args.exhaustive_limit, include_search=not args.no_search)
        comparison = compare_with_estimate(inputs, plans, work_dir=out / "runs", validate=not args.no_validate)
    except (InputError, SimError, OSError) as exc:   # OSError는 C CLI와 같이 DATA_INCOMPLETE
        print(json.dumps({"code": getattr(exc, "code", "DATA_INCOMPLETE"), "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    io.write_bytes(out / "curves.json", io.json_bytes(curves))
    io.write_bytes(out / "comparison.json", io.json_bytes(comparison))
    io.write_bytes(out / "report.md", render_markdown(curves, comparison).encode("utf-8"))
    print(json.dumps(comparison["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
