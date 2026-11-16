"""비교 입력 `plan.json`(experiments/plans/schema.md, PR #37) → M05 운행안.

비교 입력은 노선(방문 순서대로 쓴 정류장)과 노선별 대수만 준다(프로토콜 C3). 이 모듈은
1. schema.md §3의 검사 S1–S6과 망 일치(C6)를 확인하고,
2. 노선마다 영업 leg 1개 + 종점→기점 공차 leg 1개인 M05 노선으로 바꾼다.

배차 시각은 만들지 않는다. 모든 안이 같은 시간표 생성기(`timetable.normalize_plan`)를 지난다.
비교 입력에는 출처가 없어야 하므로 허용한 키 밖의 키는 전부 거절한다(프로토콜 C5).

공차 복귀 경로는 비교 입력에 없는 값이라 여기서 정한다(해석 지점). 규칙: 망의 구간만 써서
운영 시작 시각의 구간 시간 합이 가장 작은 경로, 같으면 정류장열이 사전순으로 앞선 경로.
모든 안에 같은 규칙을 쓴다. 경로가 없으면 그 노선은 변환 실패(`RETURN`)다.

CLI: python -m busstrike.sim.adapters.comparison_plan --plan P --scenario S --travel-times T --out OUT
     종료 코드 0 변환 성공(OUT에 M05 plan.json) · 1 검사 실패(OUT을 쓰지 않음) · 2 입력 오류.
     보고서(JSON)는 표준 출력으로 낸다. conversion.md의 "검사 결과"에 옮겨 적는다.
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path
from typing import Any

from .. import io
from ..models import Scenario, SimError
from ..traveltime import TravelTimeTable

PLAN_KEYS = frozenset({"plan_id", "revision", "network_snapshot_id", "routes"})
ROUTE_KEYS = frozenset({"route_id", "stops", "vehicles"})
CHECKS = ("S1", "S2", "S3", "S4", "S5", "S6")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _format_errors(raw: Any) -> list[dict]:
    """형식(키·타입)과 망 일치. 여기서 걸리면 S 검사를 하지 않는다."""
    if not isinstance(raw, dict):
        return [{"check": "FORMAT", "detail": "plan.json must be an object"}]
    errs = []
    extra = sorted(set(raw) - PLAN_KEYS)
    if extra:
        errs.append({"check": "FORMAT", "detail": "unexpected keys (C5: no origin or notes in plan.json)",
                     "keys": extra})
    missing = sorted(PLAN_KEYS - set(raw))
    if missing:
        errs.append({"check": "FORMAT", "detail": "missing keys", "keys": missing})
    if "plan_id" in raw and not (isinstance(raw["plan_id"], str) and raw["plan_id"]):
        errs.append({"check": "FORMAT", "detail": "plan_id must be a non-empty string"})
    if "revision" in raw and not (_is_int(raw["revision"]) and raw["revision"] >= 0):
        errs.append({"check": "FORMAT", "detail": "revision must be int >= 0"})
    if "network_snapshot_id" in raw and not isinstance(raw["network_snapshot_id"], str):
        errs.append({"check": "FORMAT", "detail": "network_snapshot_id must be a string"})
    routes = raw.get("routes")
    if "routes" in raw and not (isinstance(routes, list) and routes):
        errs.append({"check": "FORMAT", "detail": "routes must be a non-empty list"})
        return errs
    for i, r in enumerate(routes or []):
        where = {"route_index": i}
        if not isinstance(r, dict):
            errs.append(dict(where, check="FORMAT", detail="route must be an object"))
            continue
        if set(r) != ROUTE_KEYS:
            errs.append(dict(where, check="FORMAT", detail="route keys must be route_id, stops, vehicles",
                             keys=sorted(r)))
            continue
        if not (isinstance(r["route_id"], str) and r["route_id"]):
            errs.append(dict(where, check="FORMAT", detail="route_id must be a non-empty string"))
        if not (isinstance(r["stops"], list) and all(isinstance(s, str) and s for s in r["stops"])):
            errs.append(dict(where, check="FORMAT", detail="stops must be a list of non-empty strings"))
    return errs


def _return_path(tt: TravelTimeTable, a: str, b: str, t: int) -> tuple[str, ...] | None:
    """a → b 공차 경로. 시각 t의 구간 시간 합 최소, 같으면 정류장열 사전순."""
    heap: list[tuple[int, tuple[str, ...]]] = [(0, (a,))]
    done: set[str] = set()
    while heap:
        cost, path = heapq.heappop(heap)
        here = path[-1]
        if here in done:
            continue
        if here == b:
            return path
        done.add(here)
        for nxt in tt.successors(here):
            if nxt not in done:
                heapq.heappush(heap, (cost + tt.lookup(here, nxt, t), path + (nxt,)))
    return None


def check_plan(raw: Any, tt: TravelTimeTable, scenario: Scenario) -> dict:
    """schema.md §3 검사 보고서. `ok`가 참이면 `routes`에 공차 경로가 있다."""
    errs = _format_errors(raw)
    if not errs and raw["network_snapshot_id"] != tt.network_snapshot_id:
        errs.append({"check": "C6", "detail": "network_snapshot_id differs from travel_times",
                     "plan": raw["network_snapshot_id"], "network": tt.network_snapshot_id})
    if errs:
        return {"ok": False, "checks": {}, "failures": errs, "routes": []}

    known = tt.stop_ids()
    fails: list[dict] = []
    ids = [r["route_id"] for r in raw["routes"]]
    for rid in sorted({x for x in ids if ids.count(x) > 1}):
        fails.append({"check": "S6", "route_id": rid, "detail": "route_id is not unique"})
    for r in raw["routes"]:
        rid, stops = r["route_id"], r["stops"]
        missing = [s for s in stops if s not in known]
        if missing:
            fails.append({"check": "S1", "route_id": rid, "detail": "stops not in network", "stops": missing})
        dup = sorted({s for s in stops if stops.count(s) > 1})
        if dup:
            fails.append({"check": "S2", "route_id": rid, "detail": "stop visited twice", "stops": dup})
        if len(stops) < 2:
            fails.append({"check": "S3", "route_id": rid, "detail": "route needs at least 2 stops"})
        gaps = [[a, b] for a, b in zip(stops, stops[1:]) if not tt.has(a, b)]
        if gaps:
            fails.append({"check": "S4", "route_id": rid, "detail": "no travel time between stops",
                          "segments": gaps})
        if not (_is_int(r["vehicles"]) and r["vehicles"] >= 1):
            fails.append({"check": "S5", "route_id": rid, "detail": "vehicles must be int >= 1"})
    counts = [r["vehicles"] for r in raw["routes"]]
    if all(_is_int(c) for c in counts) and sum(counts) != len(scenario.vehicles):
        fails.append({"check": "S5", "detail": "sum of vehicles differs from available fleet (C2)",
                      "assigned": sum(counts), "available": len(scenario.vehicles)})

    routes = []
    if not fails:
        for r in raw["routes"]:
            stops = tuple(r["stops"])
            back = _return_path(tt, stops[-1], stops[0], scenario.window.start_s)
            if back is None:
                fails.append({"check": "RETURN", "route_id": r["route_id"],
                              "detail": "no network path from last stop back to first stop"})
                continue
            routes.append({"route_id": r["route_id"], "return_stops": list(back),
                           "return_distance_m": sum(tt.distance(a, b) for a, b in zip(back, back[1:]))})
    checks = {c: ("fail" if any(f["check"] == c for f in fails) else "pass") for c in CHECKS}
    checks["RETURN"] = "fail" if any(f["check"] == "RETURN" for f in fails) else (
        "pass" if routes else "not_run")
    return {"ok": not fails, "checks": checks, "failures": fails, "routes": routes}


def to_engine_plan(raw: dict, tt: TravelTimeTable, scenario: Scenario) -> tuple[dict, dict]:
    """(M05 plan.json 문서, 검사 보고서). 검사에 실패하면 `PLAN_INFEASIBLE`을 던진다."""
    report = check_plan(raw, tt, scenario)
    if not report["ok"]:
        raise SimError(SimError.PLAN_INFEASIBLE, "comparison plan failed conversion checks",
                       {"failures": report["failures"]})
    back = {r["route_id"]: r["return_stops"] for r in report["routes"]}
    plan = {
        "plan_id": raw["plan_id"],
        "revision": raw["revision"],
        "scenario_id": scenario.scenario_id,
        "status": "draft",
        "routes": [{"route_id": r["route_id"], "vehicle_count": r["vehicles"],
                    "legs": [{"in_service": True, "stop_ids": list(r["stops"])},
                             {"in_service": False, "stop_ids": back[r["route_id"]]}]}
                   for r in raw["routes"]],
        "trips": [],
        "vehicle_duties": [],
    }
    return plan, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m busstrike.sim.adapters.comparison_plan")
    ap.add_argument("--plan", required=True, type=Path, help="비교 입력 plan.json (schema.md §1)")
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--travel-times", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="M05 plan.json 경로")
    args = ap.parse_args(argv)
    try:
        scenario = io.scenario_from_dict(io.read_json(args.scenario))
        tt = TravelTimeTable.from_dict(io.read_json(args.travel_times))
        raw = io.read_json(args.plan)
        report = check_plan(raw, tt, scenario)
        report["plan_sha256"] = io.sha256_file(args.plan)
        if report["ok"]:
            plan, _ = to_engine_plan(raw, tt, scenario)
            io.write_bytes(args.out, io.json_bytes(plan))
    except SimError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "details": exc.details},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
