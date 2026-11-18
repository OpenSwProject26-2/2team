"""실험실 화면이 읽는 레이어 파일을 만든다(`run_networks.py` 다음에 실행).

    python3 experiments/routegen_lab/build_lab_data.py --region <zip_v4/region> --osm <guro_roads.json> \
        --gu guro --lab <run_networks --out 폴더>

`<lab>/layers/`에 쓴다.
- meta.json: 구, 대수, 변형, 지도 범위, 도로망 통계
- stops.json: 정류장 좌표·이름, stations.json: 망 안의 모든 지하철역(지정 정류장, 변형별 잔여 수요. 수요 0인 역 포함)
- bus_roads.json: 버스 통행 가능 도로(=1) 선, boundaries.json: 시·군·구 경계(OSM admin_level 6, 있을 때)
- strike_routes.json: 파업 노선(노선·패턴마다 정류장이 가장 많은 운행편), absorbing_routes.json: 마을·경기 흡수 노선
- demand.json: 변형별 정류장 잔여 수요(5일 평균, 하루 인원). 역 쪽이 아닌 동네 정류장 기준
- segpaths.json: 도로 망 구간마다 도로 모양(원래 구간은 매칭 경로, 새 구간은 버스 도로 경로)
- results.json: 사전 계산 안(망 × 변형 × 대수)의 노선·근사·시뮬레이터 결과(실험·테스트), 차량별 하루 동선(실험 주간 가운데 날)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import data as D  # noqa: E402

from roadnet import RoadNet  # noqa: E402

from busstrike.api.routegen_lab import vehicle_schedule  # noqa: E402


def dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def concat(parts: list[list[list[float]]]) -> list[list[float]]:
    out: list[list[float]] = []
    for p in parts:
        out.extend(p if not out else p[1:] if out[-1] == p[0] else p)
    return out


def runs_summary(runs_json: Path, fleet: int, label: str = "ours") -> dict | None:
    if not runs_json.exists():
        return None
    ms = []
    for r in json.loads(runs_json.read_text(encoding="utf-8"))["runs"]:
        if r["label"] == label and r["fleet"] == fleet:
            if r.get("verify_pass") is not True:
                return {"error": r.get("reason", "M07 실패")}
            ms.append(json.loads((Path(r["dir"]) / "run" / "metrics.json").read_text(encoding="utf-8")))
    if not ms:
        return None
    reasons = ("no_connection", "service_end", "wait", "journey")
    return {"days": len(ms), "rate": round(100 * st.mean(m["M1"]["served"] / m["M1"]["total"] for m in ms), 2),
            "served": round(st.mean(m["M1"]["served"] for m in ms), 1),
            "total": round(st.mean(m["M1"]["total"] for m in ms), 1),
            "unserved": {k: round(st.mean(m["M2"]["unserved_by_reason"].get(k, 0) for m in ms), 1) for k in reasons},
            "refusals": round(st.mean(m["M2"]["refusal_events"] for m in ms), 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--lab", required=True, type=Path)
    ap.add_argument("--boundaries", type=Path, help="fetch_osm.py --boundaries 출력(시·군·구 경계)")
    args = ap.parse_args()
    root = args.region / f"{args.gu}_0610_v4"
    info = D.check_root(root, "exp")
    lab = args.lab.resolve()
    out = lab / "layers"
    out.mkdir(parents=True, exist_ok=True)

    rn = RoadNet(args.osm, root)
    stats = rn.build()
    net = D.load_network(root)
    names = {r["stop_id"]: r["stop_name"] for r in csv.DictReader(open(root / "network/stops.csv", encoding="utf-8"))}
    dump(out / "stops.json", {k: [round(s.lon, 6), round(s.lat, 6), names.get(k, k), s.gu] for k, s in net.stops.items()})

    rows = D.load_flows(root)
    demand = {}
    st_totals = {}
    station_stops = {x for s in net.stations.values() for x in (s.access_stop, s.egress_stop) if x}
    for v in D.VARIANTS:
        d = D.build_demand(rows, v)
        by = defaultdict(float)
        for (o, dd), hs in d.od.items():
            s = dd if o in station_stops and dd not in station_stops else o
            by[s] += sum(hs)
        demand[v] = {k: round(x, 2) for k, x in sorted(by.items()) if x >= 0.05}
        st_totals[v] = d.station_total()
    dump(out / "demand.json", demand)
    stations = []
    for k, s in sorted(net.stations.items()):
        tot = {v: round(st_totals[v].get(k, 0.0), 1) for v in D.VARIANTS}
        stations.append({"id": k, "name": s.name, "lon": round(s.lon, 6), "lat": round(s.lat, 6),
                         "access": s.access_stop, "egress": s.egress_stop, "demand": tot})
    dump(out / "stations.json", stations)

    dump(out / "bus_roads.json", rn.bus_roads_geojson())
    bounds = []
    if args.boundaries and args.boundaries.exists():
        for e in json.loads(args.boundaries.read_text(encoding="utf-8"))["elements"]:
            lines = [[[round(g["lon"], 6), round(g["lat"], 6)] for g in m["geometry"]]
                     for m in e.get("members", []) if m["type"] == "way" and m.get("role") == "outer" and m.get("geometry")]
            if not lines:
                continue
            pts = [c for ln in lines for c in ln]
            name = e["tags"].get("name", "")
            bounds.append({"name": name, "home": name == info["gu"], "lines": lines,
                           "label": [round(sum(c[0] for c in pts) / len(pts), 6), round(sum(c[1] for c in pts) / len(pts), 6)]})
    dump(out / "boundaries.json", bounds)
    segpaths = {f"{a}>{b}": rn.path_any(a, b) for (a, b) in sorted(rn.seg_path)}
    dump(out / "segpaths.json", segpaths)

    # 파업 노선: 패턴(trip_id의 _p<n>_)마다 정류장이 가장 많은 운행편
    reg = {r["route_id"]: r for r in csv.DictReader(open(root / "registry/strike_route_registry.csv", encoding="utf-8"))}
    rnames = {r["route_id"]: r["route_short_name"] for r in csv.DictReader(open(root / "gtfs/routes.txt", encoding="utf-8"))}
    trips = {r["trip_id"]: r["route_id"] for r in csv.DictReader(open(root / "gtfs/trips.txt", encoding="utf-8"))}
    seq = defaultdict(list)
    for r in csv.DictReader(open(root / "gtfs/stop_times.txt", encoding="utf-8")):
        seq[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
    best: dict[tuple[str, str], list[str]] = {}
    for tid, xs in seq.items():
        rid = trips.get(tid)
        if rid is None or rid not in reg:
            continue
        pat = tid.split("_")[1] if "_" in tid else "p0"
        stops = [s for _, s in sorted(xs) if s in rn.stops]
        if len(stops) >= 2 and len(stops) > len(best.get((rid, pat), [])):
            best[(rid, pat)] = stops
    strike = []
    for (rid, pat), stops in sorted(best.items()):
        strike.append({"id": rid, "pattern": pat, "name": rnames.get(rid, rid), "type": reg[rid]["route_type"],
                       "stops": stops, "coords": concat([rn.path_any(a, b) for a, b in zip(stops, stops[1:])])})
    dump(out / "strike_routes.json", strike)

    absorbing = []
    for r in csv.DictReader(open(root / "network/absorbing_routes.csv", encoding="utf-8")):
        stops = [s for s in r["stops"].split() if s in rn.stops]
        ordered = r.get("order_known") == "True"
        coords = concat([rn.path_any(a, b) for a, b in zip(stops, stops[1:])]) if ordered and len(stops) >= 2 else []
        absorbing.append({"id": r["route_id"], "name": r["route_name"], "mode": r["mode"], "order_known": ordered,
                          "stops": stops, "coords": coords})
    dump(out / "absorbing_routes.json", absorbing)

    # 사전 계산 결과
    log = json.loads((lab / "run_networks.json").read_text(encoding="utf-8"))
    sched_date = D.SPLITS["exp"]["dates"][2]
    results = []
    for p in log["plans"]:
        plan = json.loads(Path(p["file"]).read_text(encoding="utf-8"))
        vdir = lab / p["net"] / p["variant"]
        routes = []
        for r in plan["routes"]:
            meta = p["routes"][r["route_id"]]
            routes.append({"id": r["route_id"], "stops": r["stops"], "vehicles": r["vehicles"], **meta,
                           "coords": concat([segpaths.get(f"{a}>{b}") or rn.path_any(a, b)
                                             for a, b in zip(r["stops"], r["stops"][1:])])})
        results.append({"net": p["net"], "variant": p["variant"], "fleet": p["fleet"], "cover": p.get("cover", False),
                        "covered_groups": p.get("covered_groups", []), "approx": p["approx"],
                        "pool_cover": p["pool_cover"], "demand_total": p["demand_total"], "routes": routes,
                        "schedule_date": sched_date,
                        "schedule": vehicle_schedule(vdir / "runs_exp" / p.get("label", "ours") / f"{p['variant']}_{sched_date}" / f"f{p['fleet']}" / "run"),
                        "exp": runs_summary(vdir / "runs_exp" / "runs.json", p["fleet"], p.get("label", "ours")),
                        "test": runs_summary(vdir / "runs_test" / "runs.json", p["fleet"], p.get("label", "ours"))})
    dump(out / "results.json", results)

    lons = [s.lon for s in net.stops.values()]
    lats = [s.lat for s in net.stops.values()]
    dump(out / "meta.json", {"gu": info["gu"], "gu_key": args.gu, "fleets": info["fleets"], "variants": list(D.VARIANTS),
                             "network_snapshot_id": info["network_snapshot_id"],
                             "bbox": [min(lons), min(lats), max(lons), max(lats)], "roadnet": stats,
                             "dates_exp": D.SPLITS["exp"]["dates"], "dates_test": D.SPLITS["test"]["dates"]})
    print(json.dumps({"layers": sorted(p.name for p in out.iterdir()), "results": len(results), "strike_patterns": len(strike),
                      "absorbing": len(absorbing)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
