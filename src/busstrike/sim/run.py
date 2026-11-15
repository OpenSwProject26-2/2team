"""디렉터리 단위 실행(simulator.md §0.2, §6, m07-output-contract.md §1).

scenario.json + run_inputs.json(→ demand_bins.csv, travel_times.json) + plan.json
→ out_dir/scenario/{network.json, demand_bins.csv, scenario_manifest.json}
  out_dir/run/{events.jsonl, passenger_outcomes.csv, metrics.json, measurement.json, realization.jsonl,
               timetable.json}

`timetable.json`은 GTFS 내보내기용 보조 파일이다. M07 검증기는 이름을 모르는 파일을 읽지 않는다(계약 §1).
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from . import io
from .arrivals import GENERATOR, generate, input_checksum, realization_bytes
from .engine import SimContext, SimResult, prepare, service_schedule, simulate
from .eventlog import dumps_events, outcomes_bytes
from .metrics import METRICS_SCHEMA_VERSION, PROVISIONAL, compute_metrics, passenger_outcomes
from .models import BinRow, Policy, Scenario, ServiceTrip, SimError, Timetable, Vehicle, Window
from .timetable import normalize_plan
from .traveltime import TravelTimeTable

SIM_VERSION = "busstrike-sim/0.2.0"
TIMETABLE_RULE_VERSION = 1
LOG_SCHEMA_VERSION = 2          # 2 = M07 사건 로그

SCENARIO_DIR, RUN_DIR = "scenario", "run"
NETWORK_FILE, DEMAND_FILE, MANIFEST_FILE = "network.json", "demand_bins.csv", "scenario_manifest.json"
EVENTS_FILE, OUTCOMES_FILE, METRICS_FILE = "events.jsonl", "passenger_outcomes.csv", "metrics.json"
MEASUREMENT_FILE, REALIZATION_FILE, TIMETABLE_FILE = "measurement.json", "realization.jsonl", "timetable.json"


def config_hash(policy: Policy, window: Window, vehicles: Sequence[Vehicle]) -> str:
    doc = {
        "sim_version": SIM_VERSION,
        "timetable_rule_version": TIMETABLE_RULE_VERSION,
        "log_schema_version": LOG_SCHEMA_VERSION,
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "policy": {
            "max_wait_s": policy.max_wait_s,
            "max_journey_s": policy.max_journey_s,
            "dwell_base_s": policy.dwell_base_s,
            "dwell_per_boarding_s": policy.dwell_per_boarding_s,
            "dwell_per_alighting_s": policy.dwell_per_alighting_s,
            "turnaround_s": policy.turnaround_s,
            "cycle_rest_s": policy.cycle_rest_s,
            "allowed_transfers": policy.allowed_transfers,
            "drain_s": policy.drain_s,
            "max_headway_s": policy.max_headway_s,
            "wait_threshold_s": policy.wait_threshold_s,
        },
        "operating_window": {"start_s": window.start_s, "end_s": window.end_s},
        "fleet": [asdict(v) for v in sorted(vehicles, key=lambda v: v.vehicle_id)],
    }
    return io.sha256_bytes(io.canonical_json(doc).encode("utf-8"))


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=Path(__file__).resolve().parent, capture_output=True,
                              text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip()


def engine_env() -> dict:
    """엔진 커밋(M07 보고서가 인용한다, 계약 §2.7). 실행 시각·호스트·경로는 넣지 않는다(§0.2).

    `dirty`는 추적 파일의 변경만 본다. 같은 체크아웃에서 두 번 실행하면 같은 값이다. git이 없으면 null.
    """
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit or None, "dirty": None if status is None else bool(status),
            "sim_version": SIM_VERSION,
            "note": "엔진 저장소 커밋과 추적 파일 변경 여부만 기록한다(실행 시각·호스트·경로 없음)"}


# ---------------------------------------------------------------- M07 문서

def network_doc(schedule: Sequence[ServiceTrip], scenario: Scenario, timetable: Timetable) -> dict:
    """`scenario/network.json`(계약 §2.1)."""
    capacities = {v.capacity for v in scenario.vehicles}
    spec = {"name": "busstrike-sim", "plan_id": timetable.plan_id, "plan_revision": timetable.plan_revision,
            "scenario_id": scenario.scenario_id, "service_start_s": scenario.window.start_s,
            "service_end_s": scenario.window.end_s,
            "provenance": "M05 simulator schedule (simulation, not observed operation)"}
    if len(capacities) == 1:
        spec["capacity"] = capacities.pop()           # 계약 I7: 정원이 하나일 때만 R11 기준값을 쓴다
    stops = sorted({s for t in schedule for s in t.stops})
    routes: dict[str, list[str]] = {}
    for t in schedule:
        routes.setdefault(t.route_id, list(t.stops))
    return {
        "spec": spec,
        "seed": None,
        "stops": stops,
        "routes": dict(sorted(routes.items())),
        "stop_area": dict(scenario.stop_area),
        "route_of": {t.trip_id: t.route_id for t in sorted(schedule, key=lambda t: t.trip_id)},
        "trips": [{"trip_id": t.trip_id, "vehicle_id": t.vehicle_id, "capacity": t.capacity,
                   "stops": list(t.stops), "arrivals": list(t.arrivals), "departures": list(t.departures),
                   "return_s": t.return_s, "distance_m": t.distance_m} for t in schedule],
    }


def write_m07(out_dir: Path, *, scenario: Scenario, timetable: Timetable, schedule: Sequence[ServiceTrip],
              rows: Sequence[BinRow], demand_bytes: bytes, seed, result: SimResult, extra: dict) -> dict:
    """M07 폴더를 쓰고 measurement.json 내용을 돌려준다. 실행 시각·호스트·절대 경로는 넣지 않는다."""
    out_dir = Path(out_dir)
    scen, run_dir = out_dir / SCENARIO_DIR, out_dir / RUN_DIR
    network_sha = io.write_bytes(scen / NETWORK_FILE, io.json_bytes(network_doc(schedule, scenario, timetable)))
    demand_sha = io.write_bytes(scen / DEMAND_FILE, demand_bytes)
    manifest = {
        "sha256": {DEMAND_FILE: demand_sha, NETWORK_FILE: network_sha},
        "scenario_id": scenario.scenario_id, "plan_id": timetable.plan_id, "plan_revision": timetable.plan_revision,
        "rows": len(rows), "passengers": sum(r.passenger_count for r in rows), "trips": len(schedule),
        "stop_visits": sum(len(t.stops) for t in schedule), "stops": len({s for t in schedule for s in t.stops}),
        "provenance": "synthetic or prepared aggregate inputs; no card identifiers",
    }
    io.write_bytes(scen / MANIFEST_FILE, io.json_bytes(manifest))

    in_order = generate(rows, seed)                    # M07 realization 순서(aggregate_id, index)
    real_bytes, real_checksum = realization_bytes(rows, seed, in_order)
    real_sha = io.write_bytes(run_dir / REALIZATION_FILE, real_bytes)
    log = dumps_events(result.events)
    log_sha = io.write_bytes(run_dir / EVENTS_FILE, log)
    outcomes = passenger_outcomes(result, schedule)
    out_bytes = outcomes_bytes(outcomes)
    out_sha = io.write_bytes(run_dir / OUTCOMES_FILE, out_bytes)
    policy = scenario.policy
    metrics = compute_metrics(result, outcomes, schedule, stop_area=dict(scenario.stop_area),
                              wait_threshold_s=policy.wait_threshold_s, max_headway_s=policy.max_headway_s)
    metrics_sha = io.write_bytes(run_dir / METRICS_FILE, io.json_bytes(metrics))
    timetable_sha = io.write_bytes(run_dir / TIMETABLE_FILE, io.json_bytes(io.timetable_to_dict(timetable)))

    measurement = {
        "status": "ok",
        "label": PROVISIONAL,
        "scenario": "../" + SCENARIO_DIR,
        "scenario_manifest": manifest,
        "arrival_seed": seed,
        "generator": GENERATOR,
        "realization_checksum": real_checksum,
        "config": {"max_wait_s": policy.max_wait_s, "max_journey_s": policy.max_journey_s,
                   "drain_s": result.drain_s},
        "drain_rule": "policy.drain_s" if policy.drain_s is not None else "operating_window.end_s + max_journey_s",
        "policy": {"name": scenario.scenario_id, "max_wait_s": policy.max_wait_s,
                   "max_journey_s": policy.max_journey_s, "max_headway_s": policy.max_headway_s,
                   "wait_threshold_s": policy.wait_threshold_s, "dwell_base_s": policy.dwell_base_s,
                   "turnaround_s": policy.turnaround_s, "cycle_rest_s": policy.cycle_rest_s},
        "passengers": len(result.passengers),
        "event_count": len(result.events),
        "log_bytes": len(log), "log_sha256": log_sha,
        "outcome_bytes": len(out_bytes), "outcome_sha256": out_sha, "outcome_order": "passenger_id",
        "metrics_sha256": metrics_sha,
        "realization_sha256": real_sha,
        "timetable_uri": TIMETABLE_FILE, "timetable_sha256": timetable_sha, "timetable_origin": timetable.origin,
        "metrics_uri": METRICS_FILE, "event_log_uri": EVENTS_FILE,
        "sim_version": SIM_VERSION,
        "env": engine_env(),
    }
    measurement.update(extra)
    io.write_bytes(run_dir / MEASUREMENT_FILE, io.json_bytes(measurement))
    return measurement


# ---------------------------------------------------------------- 실행

def _mismatch(name: str) -> SimError:
    return SimError(SimError.INPUT_INVALID, f"{name} does not match run_inputs.json", {"field": name})


def run(scenario_path: Path, run_inputs_path: Path, plan_path: Path, out_dir: Path) -> dict:
    """한 번 실행하고 measurement.json 내용을 돌려준다. 같은 입력이면 모든 출력 파일이 바이트 단위로 같다."""
    scenario_path, run_inputs_path, plan_path, out_dir = map(Path, (scenario_path, run_inputs_path, plan_path, out_dir))
    scenario_bytes = scenario_path.read_bytes()
    scenario = io.scenario_from_dict(io.read_json(scenario_path))
    ri = io.read_json(run_inputs_path)
    if not isinstance(ri, dict) or not isinstance(ri.get("arrival_meta"), dict):
        raise SimError(SimError.INPUT_INVALID, "run_inputs.json needs arrival_meta")
    base = run_inputs_path.parent
    uris = [ri.get("demand_bins_uri"), ri.get("travel_time_realization_uri")]
    if not all(isinstance(u, str) and u for u in uris):
        raise SimError(SimError.INPUT_INVALID, "run_inputs.json needs demand_bins_uri and travel_time_realization_uri")
    demand_path, tt_path = (base / u for u in uris)
    if not demand_path.is_file() or not tt_path.is_file():
        raise SimError(SimError.INPUT_INVALID, "input file not found")

    demand_bytes = demand_path.read_bytes()
    demand_sha = io.sha256_bytes(demand_bytes)
    tt_sha = io.sha256_file(tt_path)
    scenario_sha = io.sha256_bytes(scenario_bytes)
    seed = ri.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise SimError(SimError.INPUT_INVALID, "run_inputs.seed must be int or string")
    if ri.get("scenario_id") != scenario.scenario_id:
        raise _mismatch("scenario_id")
    if ri.get("scenario_sha256") != scenario_sha:
        raise _mismatch("scenario_sha256")
    if ri["arrival_meta"].get("sha256") != demand_sha:
        raise _mismatch("arrival_meta.sha256")
    if ri["arrival_meta"].get("generator") != GENERATOR:
        raise _mismatch("arrival_meta.generator")
    if ri.get("travel_time_sha256") != tt_sha:
        raise _mismatch("travel_time_sha256")
    checksum = input_checksum(demand_sha, seed, tt_sha, scenario_sha)
    if ri.get("checksum") != checksum:
        raise _mismatch("checksum")

    rows = io.read_demand_bins(demand_path)
    passengers = generate(rows, seed)
    if ri["arrival_meta"].get("passenger_count") != len(passengers):
        raise _mismatch("arrival_meta.passenger_count")
    tt = TravelTimeTable.from_dict(io.read_json(tt_path))
    if tt.network_snapshot_id != scenario.network_snapshot_id:
        raise SimError(SimError.INPUT_INVALID, "travel_times network_snapshot_id differs from scenario")

    plan_bytes = plan_path.read_bytes()
    plan = io.plan_from_dict(io.read_json(plan_path))
    if plan.scenario_id != scenario.scenario_id:
        raise SimError(SimError.INPUT_INVALID, "plan.scenario_id differs from scenario")

    ctx = prepare(passengers, scenario.vehicles, scenario.window, scenario.policy, tt)
    timetable = normalize_plan(plan, scenario.vehicles, scenario.window, scenario.policy, tt)
    schedule = service_schedule(timetable, ctx)
    result = simulate(ctx, schedule)

    plan_sha = io.sha256_bytes(plan_bytes)
    cfg = config_hash(scenario.policy, scenario.window, scenario.vehicles)
    extra = {
        "run_id": "run-" + io.sha256_bytes(f"{plan_sha}|{checksum}|{cfg}".encode("utf-8"))[:20],
        "plan_id": plan.plan_id,
        "plan_revision": plan.revision,
        "input_bundle_id": ri.get("input_bundle_id"),
        "input_checksum": checksum,
        "plan_sha256": plan_sha,
        "config_hash": cfg,
    }
    return write_m07(out_dir, scenario=scenario, timetable=timetable, schedule=schedule, rows=rows,
                     demand_bytes=demand_bytes, seed=seed, result=result, extra=extra)


def simulate_context(ctx: SimContext, timetable: Timetable, passengers=None) -> tuple[tuple[ServiceTrip, ...], SimResult]:
    """메모리 실행(곡선용): 시간표 → M07 운행편 → 실행."""
    schedule = service_schedule(timetable, ctx)
    return schedule, simulate(ctx, schedule, passengers)
