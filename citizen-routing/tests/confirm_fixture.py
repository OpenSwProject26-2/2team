"""테스트용 합성 확정 묶음(계약 형식). 실제 승객·카드 정보 없음."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

NAMES = {"scenario": "scenario.json", "plan": "plan.json", "timetable": "timetable.json",
         "m07_report": "verifier_report.json", "stops": "stops.csv"}
STOPS = {"A": (37.5, 127.0), "B": (37.5027, 127.0), "C": (37.5054, 127.0)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trip(trip_id, start, in_service, stops, leg, cycle=0):
    return {"trip_id": trip_id, "route_id": "r01", "vehicle_id": "V001", "cycle_index": cycle,
            "leg_index": leg, "in_service": in_service,
            "stop_times": [{"stop_seq": i, "stop_id": s, "sched_s": start + 120 * i}
                           for i, s in enumerate(stops)]}


def write_bundle(root: Path, confirm_id: str = "c1", gu: str = "guro", *,
                 service_date: str = "2026-10-28", closing: list[str] | None = None,
                 closing_step_s: int = 120, status: str = "draft", plan_extra: dict | None = None,
                 strike_ids: list[str] | None = None, current: bool = True) -> Path:
    out = root / gu / confirm_id
    out.mkdir(parents=True, exist_ok=True)
    scenario = {
        "scenario_id": "sc-test", "service_date": service_date, "timezone": "Asia/Seoul",
        "network_snapshot_id": "net-test", "demand_profile_id": "dp-test",
        "strike_route_ids": strike_ids if strike_ids is not None else ["r1"],
        "operating_window": {"start_s": 25200, "end_s": 36000},
        "fleet": {"authorized_count": 1, "vehicles": [{
            "vehicle_id": "V001", "vehicle_class": "charter45", "seat_capacity": 45,
            "standing_capacity": 0, "available_start_s": 25200, "available_end_s": 36000,
            "initial_stop_id": None, "end_stop_id": None}]},
        "policy": {"max_wait_s": 1200, "max_journey_s": 3600, "allowed_transfers": 0,
                   "arrival_method": "systematic", "dwell_base_s": 10, "dwell_per_boarding_s": 0,
                   "dwell_per_alighting_s": 0, "turnaround_s": 180, "cycle_rest_s": 0,
                   "unknown_link": "error"},
        "stop_area": {"A": "구로구", "B": "구로구", "C": "구로구"},
    }
    closing = ["C", "A"] if closing is None else closing
    plan = {"plan_id": "plan-test", "revision": 1, "scenario_id": "sc-test", "status": status,
            "routes": [{"route_id": "r01", "vehicle_count": 1, "legs": [
                {"in_service": True, "stop_ids": ["A", "B", "C"]},
                {"in_service": False, "stop_ids": closing}]}],
            "trips": [], "vehicle_duties": [], **(plan_extra or {})}
    trips = []
    for cycle in range(2):
        base = 25200 + cycle * 600
        trips.append(trip(f"r01-V001-{cycle:03d}-0", base, True, ["A", "B", "C"], 0, cycle))
        dead = trip(f"r01-V001-{cycle:03d}-1", base + 240, False, closing, 1, cycle)
        for i, st in enumerate(dead["stop_times"]):
            st["sched_s"] = base + 240 + closing_step_s * i
        trips.append(dead)
    timetable = {"schema_version": 1, "origin": "generated", "plan_id": "plan-test",
                 "plan_revision": 1, "routes": [{"route_id": "r01", "period_s": 600,
                                                 "vehicle_ids": ["V001"]}],
                 "trips": trips, "duties": [{"vehicle_id": "V001", "route_id": "r01",
                                             "trip_ids": [t["trip_id"] for t in trips],
                                             "start_s": 25200, "end_s": 26400}]}
    report = {"pass": True, "failed_checks": [], "fully_verified": False,
              "not_run_checks": [], "not_verified_checks": ["V12"]}
    (out / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    (out / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (out / "timetable.json").write_text(json.dumps(timetable), encoding="utf-8")
    (out / "verifier_report.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "stops.csv").write_text(
        "stop_id,stop_lat,stop_lon,stop_name\n"
        + "".join(f"{k},{v[0]},{v[1]},합성 {k}\n" for k, v in STOPS.items()), encoding="utf-8")
    confirm = {"schema_version": 1, "confirm_id": confirm_id,
               "confirmed_at": "2026-11-27T12:00:00+09:00", "gu": gu, "fleet": 1,
               "strike": {"mode": "full", "ratio": 1.0, "seed": None}, "run_id": "run-test",
               "plan_id": "plan-test", "revision": 1, "plan_sha256": sha(out / "plan.json"),
               "algo_name": "합성 순환", "metrics": {"served_rate": 0.3, "equity_far": 0.5,
                                                  "isolation": 0.5, "refused_rate": 0.1,
                                                  "time_ratio_rode": 1.2},
               "m07": {"pass": True, "fully_verified": False, "failed_checks": []},
               "code_commit": "0" * 40, "input_sha256": "1" * 64,
               "files": {k: {"path": v, "sha256": sha(out / v)} for k, v in NAMES.items()}}
    (out / "confirm.json").write_text(json.dumps(confirm, ensure_ascii=False), encoding="utf-8")
    if current:
        (root / gu / "CURRENT").write_text(confirm_id + "\n", encoding="utf-8")
    return out
