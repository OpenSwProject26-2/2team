"""시연 출발지 프리셋 고르기: 파업+셔틀이 셔틀을 쓰고 셔틀 없음보다 15분 이상 빠른 곳.

후보는 (1) 요인 실험 demo/origins.csv의 정류장 중 지금 확정안 셔틀이 서는 곳,
(2) 지하철역에서 800m 밖의 셔틀 정류장. 목적지는 그 셔틀 노선이 닿는 가장 가까운 역.
OTP 결과와 M05 시간표만 쓴다. 승객 단위 자료는 읽지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .mode_service import compare_three

KST = timezone(timedelta(hours=9))
STATION_STOP_M = 400


def distance_m(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def far_from_stations(lat: float, lon: float, stations: list[dict], radius_m: float) -> bool:
    return all(distance_m(lat, lon, s["lat"], s["lon"]) > radius_m for s in stations)


def seconds_of_day(value: str) -> int:
    moment = datetime.fromisoformat(value).astimezone(KST)
    return moment.hour * 3600 + moment.minute * 60 + moment.second


def m05_leg_check(leg: dict, timetable: dict, tolerance_s: int = 120) -> dict | None:
    """OTP 셔틀 구간 소요시간을 M05 시간표의 같은 구간(같은 노선·정류장·시각)과 비교한다."""
    route = (leg.get("route_id") or "").split("CR_R_", 1)[-1]
    origin = ((leg.get("from") or {}).get("stop_id") or "").split("CR_S_", 1)[-1]
    target = ((leg.get("to") or {}).get("stop_id") or "").split("CR_S_", 1)[-1]
    if not (route and origin and target and leg.get("start")):
        return None
    start = seconds_of_day(leg["start"])
    best = None
    for trip in timetable.get("trips") or []:
        if trip.get("route_id") != route or not trip.get("in_service"):
            continue
        ids = [st["stop_id"] for st in trip["stop_times"]]
        if origin not in ids:
            continue
        i = ids.index(origin)
        if target not in ids[i + 1:]:
            continue
        j = ids.index(target, i + 1)
        t0, t1 = trip["stop_times"][i]["sched_s"], trip["stop_times"][j]["sched_s"]
        gap = abs(t0 - start)
        if gap <= tolerance_s and (best is None or gap < best[0]):
            best = (gap, t1 - t0)
    if best is None:
        return {"route_id": route, "from": origin, "to": target, "m05_s": None,
                "otp_s": leg.get("duration_seconds"), "diff_s": None, "within_2min": False}
    diff = leg["duration_seconds"] - best[1]
    return {"route_id": route, "from": origin, "to": target, "m05_s": best[1],
            "otp_s": leg["duration_seconds"], "diff_s": diff, "within_2min": abs(diff) <= 120}


def pick_presets(evaluated: list[dict], min_gain_s: int = 900, count: int = 5) -> list[dict]:
    """셔틀을 쓰고, 셔틀 없음보다 min_gain_s 이상 빠른(또는 셔틀 없이는 길이 없는) 곳을 고른다."""
    good = []
    for row in evaluated:
        with_shuttle = row["best"].get("strike_shuttle")
        without = row["best"].get("strike_no_shuttle")
        if not with_shuttle or not with_shuttle.get("uses_shuttle"):
            continue
        gain = None if without is None else without["duration_seconds"] - with_shuttle["duration_seconds"]
        if gain is not None and gain < min_gain_s:
            continue
        good.append({**row, "gain_s": gain})
    good.sort(key=lambda r: (r["gain_s"] is not None, -(r["gain_s"] or 0), r["id"]))
    chosen, used = [], set()
    for row in good:
        if row["route_id"] not in used:
            chosen.append(row)
            used.add(row["route_id"])
        if len(chosen) == count:
            return chosen
    for row in good:
        if row not in chosen:
            chosen.append(row)
        if len(chosen) == count:
            break
    return chosen


def near_misses(evaluated: list[dict], chosen: list[dict], count: int) -> list[dict]:
    """기준(15분)에 못 미친 셔틀 이용 후보를 단축 큰 순으로, 출발 정류장이 겹치지 않게 고른다."""
    taken = {c["id"] for c in chosen}
    rows = [r for r in pick_presets(evaluated, -10 ** 9, len(evaluated)) if r["id"] not in taken]
    rows.sort(key=lambda r: (-(10 ** 6 if r["gain_s"] is None else r["gain_s"]), r["id"]))
    out, origins = [], set()
    for row in rows:
        origin = (row.get("origin") or {}).get("stop_id")
        if origin in origins:
            continue
        origins.add(origin)
        out.append(row)
        if len(out) == count:
            break
    return out


def shuttle_network(graph_dir: Path) -> tuple[dict, dict]:
    """파업 그래프 폴더의 셔틀 GTFS에서 정류장 좌표와 노선별 정류장 순서를 읽는다."""
    with zipfile.ZipFile(graph_dir / "shuttle.gtfs.zip") as feed:
        read = lambda name: list(csv.DictReader(io.StringIO(feed.read(name).decode("utf-8-sig"))))
        stops = {r["stop_id"]: {"id": r["stop_id"].split("CR_S_", 1)[-1], "name": r["stop_name"],
                                "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
                 for r in read("stops.txt")}
        trips = {r["trip_id"]: r["route_id"] for r in read("trips.txt")}
        names = {r["route_id"]: r.get("route_long_name") or r["route_id"] for r in read("routes.txt")}
        sequence: dict[str, list[str]] = {}
        for row in read("stop_times.txt"):
            route = trips[row["trip_id"]]
            seq = sequence.setdefault(route, [])
            if row["stop_id"] not in seq:
                seq.append(row["stop_id"])
    routes = {rid.split("CR_R_", 1)[-1]: {"name": names[rid], "stops": [stops[s] for s in seq]}
              for rid, seq in sequence.items()}
    return stops, routes


def read_stations(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [{"name": r["station_name"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
                for r in csv.DictReader(stream)]


def candidates(routes: dict, stations: list[dict], origins_csv: Path | None, radius_m: float) -> list[dict]:
    out, seen = [], set()
    preferred = []
    if origins_csv and origins_csv.is_file():
        with origins_csv.open(encoding="utf-8-sig", newline="") as stream:
            preferred = [r["stop_id"] for r in csv.DictReader(stream)]
    for rid, route in sorted(routes.items()):
        on_route = [s for s in stations if any(
            distance_m(s["lat"], s["lon"], st["lat"], st["lon"]) <= STATION_STOP_M for st in route["stops"])]
        if not on_route:
            continue
        for stop in route["stops"]:
            if not far_from_stations(stop["lat"], stop["lon"], stations, radius_m):
                continue
            index = route["stops"].index(stop)
            order = route["stops"][index + 1:] + route["stops"][:index]  # 순환 방향으로 뒤 정류장
            far_stops = [{"name": st["name"], "lat": st["lat"], "lon": st["lon"], "kind": "stop"}
                         for st in order[-len(order) // 2 - 2:-len(order) // 2 + 1] if st is not stop]
            for dest in on_route + far_stops:  # 노선이 닿는 역 + 순환 반대편 정류장
                key = (stop["id"], dest["name"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"id": f"{rid}-{stop['id']}-{dest['name']}", "route_id": rid,
                            "route_name": route["name"],
                            "origin": {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"],
                                       "stop_id": stop["id"]},
                            "destination": {"name": dest["name"] + ("" if dest.get("kind") == "stop" else "역"),
                                            "lat": dest["lat"], "lon": dest["lon"]},
                            "from_origins_csv": stop["id"] in preferred})
    out.sort(key=lambda c: (not c["from_origins_csv"], c["id"]))
    return out


def evaluate(cands: list[dict], departure_at: str, normal_dir: Path, normal_url: str,
             strike_dir: Path, strike_url: str, timetable: dict | None) -> list[dict]:
    rows = []
    for cand in cands:
        params = {"origin_lat": str(cand["origin"]["lat"]), "origin_lon": str(cand["origin"]["lon"]),
                  "destination_lat": str(cand["destination"]["lat"]),
                  "destination_lon": str(cand["destination"]["lon"]), "departure_at": departure_at}
        try:
            result = compare_three(params, normal_dir, normal_url, strike_dir, strike_url)
        except Exception as error:  # 한 후보 실패는 건너뛴다
            rows.append({**cand, "best": {}, "error": str(error)})
            continue
        best = result["best"]
        checks = []
        if timetable and best.get("strike_shuttle"):
            checks = [c for c in (m05_leg_check(leg, timetable) for leg in best["strike_shuttle"]["legs"]
                                  if leg["kind"] == "shuttle") if c]
        rows.append({**cand, "best": best, "m05_checks": checks,
                     "times_s": {k: (v["duration_seconds"] if v else None) for k, v in best.items()}})
    return rows


def run(active: dict, normal_dir: Path, normal_url: str, stations_csv: Path,
        origins_csv: Path | None, timetable_path: Path | None, out_paths: list[Path],
        departure_time: str = "08:00", radius_m: float = 800, min_gain_s: int = 900,
        count: int = 5, limit: int = 160) -> dict:
    graph_dir = Path(active["dir"])
    service_date = (active.get("service_date") or {}).get("gtfs")
    departure_at = f"{service_date}T{departure_time}:00+09:00"
    _, routes = shuttle_network(graph_dir)
    cands = candidates(routes, read_stations(stations_csv), origins_csv, radius_m)[:limit]
    timetable = json.loads(timetable_path.read_text(encoding="utf-8")) if timetable_path else None
    evaluated = evaluate(cands, departure_at, normal_dir, normal_url, graph_dir, active["url"], timetable)
    chosen = pick_presets([r for r in evaluated if r.get("best")], min_gain_s, count)
    slim = lambda r: {k: r.get(k) for k in ("id", "route_id", "route_name", "origin", "destination",
                                           "from_origins_csv", "times_s", "gain_s", "m05_checks")}
    payload = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"),
               "confirm_id": active.get("confirm_id"), "build_id": active.get("build_id"),
               "departure_at": departure_at,
               "criteria": {"far_from_station_m": radius_m, "min_gain_s": min_gain_s,
                            "requires_shuttle_leg": True, "count": count},
               "candidates_checked": len(evaluated), "presets": [slim(r) for r in chosen],
               "near_misses": [slim(r) for r in near_misses(
                   [r for r in evaluated if r.get("best")], chosen, max(0, count - len(chosen)))],
               "all_candidates": [slim({**r, "gain_s": (r["times_s"]["strike_no_shuttle"] - r["times_s"]["strike_shuttle"]
                                                       if r.get("times_s") and r["times_s"].get("strike_no_shuttle")
                                                       and r["times_s"].get("strike_shuttle") else None)})
                                  for r in evaluated if r.get("times_s")]}
    for path in out_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def presets_hook(raw: dict, config) -> callable:
    """apply_confirm 설정의 "presets" 항목으로 반영 뒤 프리셋 단계를 만든다."""
    options = raw["presets"]

    def hook(active: dict) -> dict:
        confirm_dir = Path(config.confirm_root) / config.gu / active["confirm_id"]
        outs = [Path(active["dir"]) / "presets.json"]
        if options.get("copy_to"):
            outs.append(Path(options["copy_to"]) / f"presets_{active['confirm_id']}.json")
        return run(active, Path(config.normal_dir), options["normal_url"], Path(options["stations_csv"]),
                   Path(options["origins_csv"]) if options.get("origins_csv") else None,
                   confirm_dir / "timetable.json", outs, options.get("departure_time", "08:00"))

    return hook


def main() -> None:
    parser = argparse.ArgumentParser(description="시연 출발지 프리셋 고르기")
    parser.add_argument("--config", type=Path, required=True, help="apply-config.json")
    args = parser.parse_args()
    from .apply_confirm import ApplyConfig, read_active
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    config = ApplyConfig.from_file(args.config)
    active = read_active(config.runtime)
    if not active:
        raise SystemExit("적용된 확정이 없습니다.")
    result = presets_hook(raw, config)(active)
    print(json.dumps({"presets": [(p["id"], p["times_s"], p["gain_s"]) for p in result["presets"]],
                      "checked": result["candidates_checked"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
