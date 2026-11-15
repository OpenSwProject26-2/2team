"""디렉터리 단위 실행(simulator.md §0.2, §6.2).

scenario.json + run_inputs.json(→ arrivals.jsonl, travel_times.json) + plan.json
→ out_dir/{timetable.json, events.jsonl, metrics.json, run.json}
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from . import io
from .arrivals import input_checksum
from .engine import prepare, simulate
from .eventlog import dumps_events
from .metrics import METRICS_SCHEMA_VERSION, compute_metrics
from .models import Policy, SimError, Vehicle, Window
from .timetable import normalize_plan
from .traveltime import TravelTimeTable

SIM_VERSION = "busstrike-sim/0.1.0"
TIMETABLE_RULE_VERSION = 1
LOG_SCHEMA_VERSION = 1

TIMETABLE_FILE = "timetable.json"
EVENTS_FILE = "events.jsonl"
METRICS_FILE = "metrics.json"
RUN_FILE = "run.json"


def config_hash(policy: Policy, window: Window, vehicles: Sequence[Vehicle]) -> str:
    doc = {
        "sim_version": SIM_VERSION,
        "timetable_rule_version": TIMETABLE_RULE_VERSION,
        "log_schema_version": LOG_SCHEMA_VERSION,
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "policy": {
            "dwell_base_s": policy.dwell_base_s,
            "dwell_per_boarding_s": policy.dwell_per_boarding_s,
            "dwell_per_alighting_s": policy.dwell_per_alighting_s,
            "turnaround_s": policy.turnaround_s,
            "cycle_rest_s": policy.cycle_rest_s,
            "max_journey_s": policy.max_journey_s,
            "allowed_transfers": policy.allowed_transfers,
        },
        "operating_window": {"start_s": window.start_s, "end_s": window.end_s},
        "fleet": [asdict(v) for v in sorted(vehicles, key=lambda v: v.vehicle_id)],
    }
    return io.sha256_bytes(io.canonical_json(doc).encode("utf-8"))


def _mismatch(name: str) -> SimError:
    return SimError(SimError.INPUT_INVALID, f"{name} does not match run_inputs.json", {"field": name})


def run(scenario_path: Path, run_inputs_path: Path, plan_path: Path, out_dir: Path) -> dict:
    """한 번 실행하고 run.json 내용을 돌려준다. 같은 입력이면 모든 출력 파일이 바이트 단위로 같다."""
    scenario_path, run_inputs_path, plan_path, out_dir = map(Path, (scenario_path, run_inputs_path, plan_path, out_dir))
    scenario_bytes = scenario_path.read_bytes()
    scenario = io.scenario_from_dict(io.read_json(scenario_path))
    ri = io.read_json(run_inputs_path)
    if not isinstance(ri, dict) or not isinstance(ri.get("arrival_meta"), dict):
        raise SimError(SimError.INPUT_INVALID, "run_inputs.json needs arrival_meta")
    base = run_inputs_path.parent
    uris = [ri.get("arrival_realization_uri"), ri.get("travel_time_realization_uri")]
    if not all(isinstance(u, str) and u for u in uris):
        raise SimError(SimError.INPUT_INVALID, "run_inputs.json needs both realization URIs")
    arrivals_path, tt_path = (base / u for u in uris)
    if not arrivals_path.is_file() or not tt_path.is_file():
        raise SimError(SimError.INPUT_INVALID, "realization file not found")

    arrivals_sha = io.sha256_file(arrivals_path)
    tt_sha = io.sha256_file(tt_path)
    scenario_sha = io.sha256_bytes(scenario_bytes)
    if ri.get("scenario_id") != scenario.scenario_id:
        raise _mismatch("scenario_id")
    if ri.get("scenario_sha256") != scenario_sha:
        raise _mismatch("scenario_sha256")
    if ri["arrival_meta"].get("sha256") != arrivals_sha:
        raise _mismatch("arrival_meta.sha256")
    if ri.get("travel_time_sha256") != tt_sha:
        raise _mismatch("travel_time_sha256")
    checksum = input_checksum(arrivals_sha, tt_sha, scenario_sha)
    if ri.get("checksum") != checksum:
        raise _mismatch("checksum")

    passengers = [io.passenger_from_dict(x) for x in io.read_jsonl(arrivals_path)]
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
    events = simulate(ctx, timetable)
    metrics = compute_metrics(events, ctx.passengers, timetable, scenario.vehicles, scenario.policy, tt)

    timetable_sha = io.write_bytes(out_dir / TIMETABLE_FILE, io.json_bytes(io.timetable_to_dict(timetable)))
    events_sha = io.write_bytes(out_dir / EVENTS_FILE, dumps_events(events))
    metrics_sha = io.write_bytes(out_dir / METRICS_FILE, io.json_bytes(metrics))
    plan_sha = io.sha256_bytes(plan_bytes)
    cfg = config_hash(scenario.policy, scenario.window, scenario.vehicles)
    result = {
        "run_id": "run-" + io.sha256_bytes(f"{plan_sha}|{checksum}|{cfg}".encode("utf-8"))[:20],
        "plan_id": plan.plan_id,
        "plan_revision": plan.revision,
        "input_bundle_id": ri.get("input_bundle_id"),
        "metrics_uri": METRICS_FILE,
        "event_log_uri": EVENTS_FILE,
        "timetable_uri": TIMETABLE_FILE,
        "config_hash": cfg,
        "input_checksum": checksum,
        "plan_sha256": plan_sha,
        "timetable_sha256": timetable_sha,
        "event_log_sha256": events_sha,
        "metrics_sha256": metrics_sha,
        "timetable_origin": timetable.origin,
        "sim_version": SIM_VERSION,
    }
    io.write_bytes(out_dir / RUN_FILE, io.json_bytes(result))
    return result
