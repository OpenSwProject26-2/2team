"""레포 밖 실험 스크립트 guro_exp_work/routegen_integrated/pareto_1125/scripts/metrics.py를 옮겼다(지표 식은 그대로, 2026-11-25).
실행 하나의 지표(pareto_1125 PLAN.md 2절)와 날짜 묶음 집계. tour_1124의 fairness.py·baseline.py·ride.py 규칙을 구 인자·날짜 묶음으로 일반화했다.

실행 하나(`run_metrics`, passenger_outcomes를 지우기 전에 부른다)
- 탄 승객마다 동네 쪽 도보(walk_m ÷ 75m/분) + 대기(탑승 − 정류장 도착) + 차내(도착 − 탑승).
  허용 범위 안 = 도보 + 대기 ≤ 20분 그리고 대기 + 차내 ≤ 60분.
- 원래 버스 대비 = (도보 + 대기 + 차내) ÷ 원래 버스(대기 + 차내). 원래 OD 키에 기준이 있는 탄 승객만(baseline).
- 소외 정류장(보조): 그 날 동네 쪽 끝 정류장별 수요·탄 승객, 안의 셔틀 정류장(영업 정차).
- 노선별 탄 승객(vehicle_id → 노선, timetable.json).
- 사후 진단(diag.py: 닿음, 연결 없음 A/B, 순서 불일치 C, 노선별 배차).
날짜 묶음 집계(`aggregate`): 충족률 = Σ 도보 반영 탑승 ÷ Σ 셔틀 대상 수요, 소외율 = 1 − 형평 역 밖, 허용 범위 안 비율, 원래 버스 대비 중앙값 등.
"""
from __future__ import annotations

import csv
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from . import diag as DG

WALK_M_MIN = 75.0


def station_side(root: Path) -> set[str]:
    cand = set()
    for r in csv.DictReader((root / "network/station_stops.csv").open(encoding="utf-8")):
        if r["status"] == "ok":
            cand |= set(r["candidates"].split()) | {r["station_stop_id"]}
    return cand


def orig_rows(bundle_day: Path, cand: set[str]) -> dict:
    """aggregate_id → (동네 끝 정류장, 원래 OD 키 "o>d", 승객 수)."""
    out = {}
    for r in csv.DictReader((bundle_day / "demand_bins.csv").open(encoding="utf-8")):
        o, t = r["origin"], r["destination"]
        home = o if t in cand else (t if o in cand else None)
        out[r["aggregate_id"]] = (home, f"{o}>{t}", int(r["passenger_count"]))
    return out


def baseline(root: Path, bundles: Path) -> dict:
    """tour_1124/baseline.py와 같은 규칙: OD 키 → [대기 초(배차 ÷ 2), 차내 중앙값 초, 운행 수]. 평일 GTFS 06~10시 출발."""
    T0, T1 = 6 * 3600, 10 * 3600

    def sec(x):
        h, m, s = map(int, x.split(":"))
        return h * 3600 + m * 60 + s
    by_stop_station, cands = defaultdict(set), defaultdict(set)
    for r in csv.DictReader((root / "network/station_stops.csv").open(encoding="utf-8")):
        if r["status"] != "ok":
            continue
        cands[r["station_id"]] |= set(r["candidates"].split()) | {r["station_stop_id"]}
        by_stop_station[r["station_stop_id"]].add(r["station_id"])
    trips = defaultdict(list)
    for r in csv.DictReader((root / "gtfs/stop_times.txt").open(encoding="utf-8")):
        trips[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], sec(r["arrival_time"]), sec(r["departure_time"])))
    pos = {}
    for t, rows in trips.items():
        rows.sort()
        pos[t] = {s: (i, a, d) for i, (_, s, a, d) in enumerate(rows)}
    keys = set()
    for d in sorted(p for p in bundles.iterdir() if p.is_dir()):
        for r in csv.DictReader((d / "demand_bins.csv").open(encoding="utf-8")):
            keys.add((r["origin"], r["destination"]))
    by_first = defaultdict(list)
    for t, m in pos.items():
        for s in m:
            by_first[s].append(t)
    out = {}
    for o, d in sorted(keys):
        if d in by_stop_station:
            home, sts, acc = o, by_stop_station[d], True
        elif o in by_stop_station:
            home, sts, acc = d, by_stop_station[o], False
        else:
            continue
        side = set().union(*(cands[k] for k in sts))
        rides = []
        for t in by_first.get(home, ()):
            m = pos[t]
            hi, ha, hd = m[home]
            best = None
            for x in side:
                if x not in m:
                    continue
                xi, xa, xd = m[x]
                if acc and xi > hi and T0 <= hd < T1:
                    v = xa - hd
                elif not acc and xi < hi and T0 <= xd < T1:
                    v = ha - xd
                else:
                    continue
                best = v if best is None else min(best, v)
            if best is not None:
                rides.append(best)
        if rides:
            hw = (T1 - T0) / len(rides)
            out[f"{o}>{d}"] = [round(hw / 2), round(st.median(rides)), len(rides)]
    return out


BAND_NAMES = ("0-400", "400-800", "800+")


def home_bands(home_stops, nb_far: dict, st_stops: set[str], far_m: float = 800.0) -> dict[str, str]:
    """동네 정류장 → 역 지정 정류장까지 도로 도보 거리 구간(equity.far_stops와 같은 이웃·같은 경계, 800+ = 형평 역 밖 집합)."""
    out = {}
    for s in home_stops:
        if s in st_stops:
            d = 0.0
        else:
            ds = [w for x, w in nb_far.get(s, {s: 0.0}).items() if x in st_stops]
            d = min(ds) if ds else None
        out[s] = ("0-400" if d is not None and d <= 400 + 1e-9 else
                  "400-800" if d is not None and d <= far_m + 1e-9 else "800+")
    return out


def run_metrics(rd: Path, walk_m: dict, orig: dict, base: dict, root: Path, nb: dict,
                bands: dict | None = None, st_stops: set | None = None) -> dict:
    """rd = run_matrix 실행 폴더(…/strike_<날짜>/f<대수>). passenger_outcomes가 있어야 한다."""
    pm = json.loads((rd / "plan_m05.json").read_text(encoding="utf-8"))
    # 노선별 탄 승객: 승객 결과에 차량 ID가 없어서, 탄 승객의 (탄, 내린) 정류장을 한 영업 구간 안에서 순서대로 지나는 노선에
    # 1 ÷ (그런 노선 수)씩 나눠 센다(diag.py의 닿음과 같은 판정)
    legpos = {r["route_id"]: [{s: i for i, s in enumerate(l["stop_ids"])} for l in r["legs"] if l["in_service"]]
              for r in pm["routes"]}
    cache_rt: dict = {}

    def routes_of(o, d):
        if (o, d) not in cache_rt:
            cache_rt[(o, d)] = [rid for rid, ps in legpos.items() if any(o in q and d in q and q[o] < q[d] for q in ps)]
        return cache_rt[(o, d)]
    pstops = sorted({s for r in pm["routes"] for leg in r["legs"] if leg["in_service"] for s in leg["stop_ids"]})
    dem_home: dict[str, int] = defaultdict(int)
    for home, _, c in orig.values():
        if home:
            dem_home[home] += c
    served_home: dict[str, int] = defaultdict(int)
    by_route: dict[str, float] = defaultdict(float)
    n = ok = 0
    ratios, door = [], []
    pid_ratio: dict[str, float] = {}                 # 공통 승객 기준용: 승객 ID(시뮬레이터 passenger_id, 카드 ID 아님) → 시간 비
    band = {b: [0, 0] for b in BAND_NAMES + ("out",)}   # 구간 → [수요(실행 승객), 도보 반영 탑승]
    for p in csv.DictReader((rd / "run/passenger_outcomes.csv").open(encoding="utf-8")):
        agg = json.loads(p["passenger_id"])[0]
        bk = None
        if bands is not None:
            okey0 = orig.get(agg, (None, "", 0))[1]
            if okey0:
                o0, d0 = okey0.split(">")
                h0 = d0 if (o0 in st_stops and d0 not in st_stops) else o0     # equity.home_end
                bk = bands.get(h0, "out")
            else:
                bk = "out"
            band[bk][0] += 1
        if p["outcome"] != "served":
            continue
        home, okey, _ = orig.get(agg, (None, "", 0))
        if home:
            served_home[home] += 1
        rs_ = routes_of(p["origin"], p["destination"]) or ["?"]
        for rid in rs_:
            by_route[rid] += 1 / len(rs_)
        walk = walk_m.get(agg, 0.0) / WALK_M_MIN * 60
        wait = int(p["boarding_s"]) - int(p["time_s"])
        ride = int(p["arrival_s"]) - int(p["boarding_s"])
        n += 1
        ok += (walk + wait <= 1200) and (wait + ride <= 3600)
        door.append(round(walk + wait + ride))
        if bk is not None and wait + int(round(walk_m.get(agg, 0.0) / WALK_M_MIN * 60)) <= 1200:   # reach.served_with_walk
            band[bk][1] += 1
        b = base.get(okey)
        if b:
            ratios.append(round((walk + wait + ride) / (b[0] + b[1]), 4))
            pid_ratio[p["passenger_id"]] = ratios[-1]
    return {"served_n": n, "within": ok, "ratios": ratios, "door_s": door, "plan_stops": pstops,
            "pid_ratio": pid_ratio, "band": band if bands is not None else None,
            "dem_home": dict(dem_home), "served_home": dict(served_home), "by_route": dict(by_route),
            "diag": DG.diagnose(rd, root, nb)}


def aggregate(rs: list[dict], ms: list[dict], gu_stops: set[str], nb: dict) -> dict:
    """rs = results.json 줄(M07 통과, 같은 안·날짜 묶음), ms = 같은 날짜의 run_metrics."""
    k = len(rs)
    sw = sum(r["served_walk"] for r in rs)
    tot = sum(r["total_sim"] for r in rs)
    out = {"days": k, "dates": sorted(r["date"] for r in rs), "served_walk": sw / k, "served": sum(r["served"] for r in rs) / k,
           "total_sim": tot / k, "fill_rate": sw / tot if tot else 0.0,
           "eq_total": rs[0]["equity"]["total"], "eq_far": rs[0]["equity"]["far"], "isolation": 1 - rs[0]["equity"]["far"],
           "reach": sum(r["reassign"]["reachable"] for r in rs) / k,
           "wait": sum(r["unserved_by_reason"].get("wait", 0) for r in rs) / k,
           "end": sum(r["unserved_by_reason"].get("service_end", 0) for r in rs) / k,
           "journey": sum(r["unserved_by_reason"].get("journey", 0) for r in rs) / k,
           "noconn_sim": sum(r["unserved_by_reason"].get("no_connection", 0) for r in rs) / k,
           "refused_unserved": sum(r.get("refused_unserved", 0) for r in rs) / k}
    out["conv"] = out["served"] / out["reach"] if out["reach"] else 0.0
    out["check_total"] = round(out["total_sim"] - (out["served"] + out["wait"] + out["end"] + out["journey"]
                                                   + out["noconn_sim"]), 3)
    if not ms:
        return out
    n = sum(m["served_n"] for m in ms)
    ratios = [x for m in ms for x in m["ratios"]]
    door = [x for m in ms for x in m["door_s"]]
    out.update({"served_n": n, "within_share": sum(m["within"] for m in ms) / n if n else 0.0,
                "vs_bus_n": len(ratios), "vs_bus_median": st.median(ratios) if ratios else None,
                "vs_bus_le1": sum(x <= 1 for x in ratios) / len(ratios) if ratios else None,
                "door_mean_min": st.mean(door) / 60 if door else None})
    dem = defaultdict(float)
    served = defaultdict(int)
    for m in ms:
        for s, c in m["dem_home"].items():
            dem[s] += c / len(ms)
        for s, c in m["served_home"].items():
            served[s] += c
    target = sorted(s for s in dem if s in gu_stops and dem[s] >= 20)
    pst = set(ms[0]["plan_stops"])
    A = {s for s in target if not any(x in pst for x in nb.get(s, {s: 0.0}))}
    B = {s for s in target if served[s] == 0}
    out.update({"iso_target": len(target), "iso_A": len(A), "iso_B": len(B), "iso_stops": len(A | B)})
    d = [m["diag"] for m in ms]
    g = lambda fn: sum(fn(x) for x in d) / len(d)
    out["noconn"] = g(lambda x: x["no_connection_why"].get("A", 0) + x["no_connection_why"].get("B", 0))
    out["order"] = g(lambda x: x["no_connection_why"].get("C", 0))
    out["check_diag"] = round(out["noconn"] + out["order"] - out["noconn_sim"], 3)
    for x in ("routes", "hw_mean", "hw_max", "share_hw_over_20", "cycle_sum_min", "reach_per_cycle_min_median"):
        out[x] = d[0]["summary"][x]
    by_route = defaultdict(int)
    for m in ms:
        for r, c in m["by_route"].items():
            by_route[r] += c
    out["served_by_route"] = {r: c / len(ms) for r, c in sorted(by_route.items())}
    return out
