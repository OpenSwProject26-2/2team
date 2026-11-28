"""실행 하나의 사후 진단(pareto_1125 PLAN.md 2절, 레포 밖 diag.py를 옮겼다. 규칙 그대로). 규칙은 reeval_1122/route_diag/route_diag.py와 같고 구마다 망 파일을 읽게 일반화했다.

- 닿음(노선 기준) = 한 영업 구간 안에서 o가 d보다 앞에 서는 노선이 있는 승객(재배정 뒤 정류장 쌍, passenger_outcomes).
- 안 닿고 못 탄 승객의 이유(원래 정류장 쌍 기준): A 동네 쪽 400m 안에 셔틀 정류장 없음 / B 역 쪽(그 역 주변 정류장)에 서는 노선 없음 /
  C 둘 다 있으나 같은 노선·같은 영업 구간에서 순서·노선이 맞지 않음.
- 노선별 닿는 수요 = 그 노선이 닿게 하는 승객 수(여러 노선이 닿으면 노선마다 다 센다, PLAN 작업자 결정 9). ÷ 순환 시간(분, 시간표 period_s).
- 배차 = period_s ÷ 대수(분).
"""
from __future__ import annotations

import csv
import json
import statistics as st
from collections import Counter
from pathlib import Path


def _stations(root: Path):
    net = root / "network"
    stop_station, desig = {}, {}
    for r in csv.DictReader((net / "station_stops.csv").open(encoding="utf-8")):
        if r["status"] != "ok":
            continue
        for s in r["candidates"].split():
            stop_station.setdefault(s, set()).add(r["station_name"])
        desig[r["station_stop_id"]] = r["station_name"]
        stop_station.setdefault(r["station_stop_id"], set()).add(r["station_name"])
    return stop_station, desig


_CACHE: dict = {}


def diagnose(rd: Path, root: Path, nb: dict) -> dict:
    if str(root) not in _CACHE:
        _CACHE[str(root)] = _stations(root)
    STOP_ST, DESIG = _CACHE[str(root)]
    pm = json.loads((rd / "plan_m05.json").read_text(encoding="utf-8"))
    tt = json.loads((rd / "run/timetable.json").read_text(encoding="utf-8"))
    legs = {r["route_id"]: [l["stop_ids"] for l in r["legs"] if l["in_service"]] for r in pm["routes"]}
    pos = {rid: [{s: i for i, s in enumerate(l)} for l in ls] for rid, ls in legs.items()}
    served_stops = {s for ls in legs.values() for l in ls for s in l}
    cache: dict = {}

    def reach(o, d):
        k = (o, d)
        if k not in cache:
            cache[k] = [rid for rid, ps in pos.items() if any(o in p and d in p and p[o] < p[d] for p in ps)]
        return cache[k]

    per = Counter()
    nc = Counter()
    c = Counter()
    for p in csv.DictReader((rd / "run/passenger_outcomes.csv").open(encoding="utf-8")):
        o, d = p["origin"], p["destination"]
        rs = reach(o, d)
        c["total"] += 1
        c["served"] += p["outcome"] == "served"
        for rid in rs:
            per[rid] += 1
        if rs:
            c["reach_route"] += 1
            continue
        if p["outcome"] == "served":
            c["served_no_route"] += 1
            continue
        access = d in STOP_ST or d in DESIG
        home, st_side = (o, d) if access else (d, o)
        home_ok = any(s in served_stops for s in nb.get(home, {home: 0.0}))
        names = STOP_ST.get(st_side, set()) | ({DESIG[st_side]} if st_side in DESIG else set())
        st_ok = any(STOP_ST.get(s, set()) & names for s in served_stops)
        nc["A" if not home_ok else ("B" if not st_ok else "C")] += 1
    routes = []
    for r in tt["routes"]:
        nv = len(r["vehicle_ids"])
        if not nv:
            continue
        cyc = r["period_s"] / 60
        routes.append({"route": r["route_id"], "vehicles": nv, "cycle_min": round(cyc, 2), "headway_min": round(cyc / nv, 2),
                       "reach": per[r["route_id"]], "reach_per_cycle_min": round(per[r["route_id"]] / cyc, 3),
                       "stops": sum(len(l) for l in legs.get(r["route_id"], []))})
    hw = [x["headway_min"] for x in routes]
    return {"counts": dict(c), "no_connection_why": dict(nc), "routes": routes,
            "summary": {"routes": len(routes), "hw_mean": round(st.mean(hw), 2), "hw_max": round(max(hw), 2),
                        "share_hw_over_20": round(sum(h > 20 + 1e-9 for h in hw) / len(hw), 3),
                        "cycle_sum_min": round(sum(x["cycle_min"] for x in routes), 1),
                        "reach_per_cycle_min_median": round(st.median(x["reach_per_cycle_min"] for x in routes), 3)}}
