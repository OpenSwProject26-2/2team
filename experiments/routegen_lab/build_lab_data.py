"""실험실 화면이 읽는 레이어 파일을 만든다(`run_networks.py` 다음에 실행).

    python3 experiments/routegen_lab/build_lab_data.py --region <zip_v4/region> --osm <guro_roads.json> \
        --gu guro --lab <run_networks --out 폴더>

    python3 experiments/routegen_lab/build_lab_data.py --region <region> --osm <구_roads.json> --gu <slug> \
        --sweep <hub_sweep2/<slug>> --boundaries <seoul_boundaries.json>      # 구별 실험(hub_sweep) 결과로

`<lab>/layers/`(또는 `<sweep>/layers/`)에 쓴다.
- meta.json: 구, 대수, 변형, 지도 범위, 도로망 통계
- stops.json: 정류장 좌표·이름, stations.json: 망 안의 모든 지하철역(지정 정류장, 변형별 잔여 수요. 수요 0인 역 포함)
- bus_roads.json: 버스 통행 가능 도로(=1) 선, boundaries.json: 시·군·구 경계(OSM admin_level 6, 있을 때)
- strike_routes.json: 파업 노선(노선·패턴마다 정류장이 가장 많은 운행편), absorbing_routes.json: 마을·경기 흡수 노선
- demand.json: 변형별 정류장 잔여 수요(5일 평균, 하루 인원). 역 쪽이 아닌 동네 정류장 기준
- segpaths.json: 도로 망 구간마다 도로 모양(원래 구간은 매칭 경로, 새 구간은 버스 도로 경로)
- results.json: 사전 계산 안(망 × 변형 × 대수, --sweep이면 생성 설정 × 대수)의 노선·근사·시뮬레이터 결과(실험·테스트),
  차량별 하루 동선(실험 주간 가운데 날)
- municipal.json(--sweep, `<sweep>/muni`가 있을 때): 지자체 셔틀안 노선·대수·시뮬레이터 결과
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

from busstrike.api.routegen_lab import VARIANT_FLAGS, stop_residual, vehicle_schedule  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, AbsorbParams, residual_rows  # noqa: E402


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


CONFIG_NAMES = {"walk": "걷기 반영 배분", "walkm": "걷기 반영 + 여러 역 경유"}


def config_name(label: str) -> str:
    if label in CONFIG_NAMES:
        return CONFIG_NAMES[label]
    if label.startswith("cov"):
        disc = label.endswith("d")
        core = label[:-1] if disc else label
        multi = core.endswith("m")
        core = core[:-1] if multi else core
        t, _, lam = core[3:].partition("r")
        return (f"형평 {t}%" + (f" + 탈 수요 ×{lam}" if lam else "") + (" + 여러 역 경유" if multi else "")
                + (" + 역 근처 할인" if disc else ""))
    return label


def add_walk(args, lab, out, info, net, rn, segpaths, demand, results, log, sched_date) -> dict:
    """걷기 400m: 도보 이웃 레이어(walk.json), 모든 안의 덮는 범위·다른 구 정차, walk_eval 안 추가. 지자체안 걷기 결과를 돌려준다."""
    from walknet import walk_neighbors
    nb = walk_neighbors(rn, 400.0)
    home = info["gu"]
    weight = {k: v for k, v in demand.get("precise_affected_maeul_gg", {}).items()
              if k in net.stops and net.stops[k].gu == home}
    tot = sum(weight.values()) or 1.0
    st_stops = {x for s_ in net.stations.values() for x in (s_.access_stop, s_.egress_stop) if x}
    nb800 = walk_neighbors(rn, 800.0, set(weight))
    far = {k for k in weight if not any(x in st_stops for x in nb800.get(k, {}))}   # 역 지정 정류장 도보 800m 밖
    tot_far = sum(weight[k] for k in far) or 1.0
    dump(out / "walk.json", {"radius_m": 400, "weight": weight, "far_m": 800, "far": sorted(far),
                             "nb": {k: sorted(x for x in nb.get(k, {k: 0.0}) if x in net.stops) for k in weight}})

    def coverage(stops) -> float:
        ss = set(stops)
        return round(sum(w for k, w in weight.items() if any(x in ss for x in nb.get(k, {k: 0.0}))) / tot, 4)

    def coverage_far(stops) -> float:
        ss = set(stops)
        return round(sum(weight[k] for k in far if any(x in ss for x in nb.get(k, {k: 0.0}))) / tot_far, 4)

    def outside(stops) -> int:
        return sum(1 for s in set(stops) if s in net.stops and net.stops[s].gu != home)

    st_name = {}
    for k, s_ in net.stations.items():
        for x in (s_.access_stop, s_.egress_stop):
            if x:
                st_name.setdefault(x, s_.name)
    for r in results:
        stops = [s for rt in r["routes"] for s in rt["stops"]]
        r["coverage_walk"], r["outside_stops"], r["coverage_far"] = coverage(stops), outside(stops), coverage_far(stops)
    by_cfg = {(r.get("config"), r["fleet"]): r for r in results}
    muni = {}
    for wd in [Path(x) for x in args.walk_eval.split(",") if x]:
        wl = json.loads((wd / "walk_eval.json").read_text(encoding="utf-8"))

        def summ(label, f, mode, split):
            cands = [wd / f"runs_{mode}" / f"{label}-f{f}" / split / "runs.json"]
            if mode == "nowalk":
                cands.append(wd / "runs_nowalk" / split / "runs.json")
            for c in cands:
                got = runs_summary(c, f, label)
                if got:
                    return got
            return None
        for key, p in wl["plans"].items():
            label, f = p["label"], p["fleet"]
            ws = {split: wl.get("walk_stats", {}).get(f"{label}-f{f}-{split}") for split in ("exp", "test")}
            extra = {"exp_walk": summ(label, f, "walk", "exp"), "test_walk": summ(label, f, "walk", "test"), "walk_stats": ws}
            if label == "muni":
                muni = {**extra, "coverage_walk": p["coverage"],
                        "coverage_far": coverage_far([s for rt in p["route_list"] for s in rt["stops"]])}
                continue
            if label == "s12" and ("hub135_s12", f) in by_cfg:
                by_cfg[("hub135_s12", f)].update(extra)
                continue
            routes = []
            for i, rt in enumerate(p["route_list"], 1):
                stops = rt["stops"]
                hubs = list(dict.fromkeys(st_name[s] for s in stops if s in st_name))
                kind = "multi" if len(hubs) > 1 else ("access" if stops[-1] in st_name else "egress" if stops[0] in st_name else "multi")
                routes.append({"id": f"r{i:02d}", "stops": stops, "vehicles": rt["vehicles"], "kind": kind, "hubs": hubs,
                               "cycle_s": None, "approx": None, "outside": outside(stops),
                               "coords": concat([segpaths.get(f"{a}>{b}") or rn.path_any(a, b) for a, b in zip(stops, stops[1:])])})
            run_dir = wd / "runs_walk" / f"{label}-f{f}" / "exp" / label / f"precise_affected_maeul_gg_{sched_date}" / f"f{f}" / "run"
            results.append({"net": "road", "variant": "affected_maeul_gg", "run_variant": "precise_affected_maeul_gg",
                            "config": label, "config_name": config_name(label), "walk_eval": True, "absorb": "precise",
                            "absorbed": log["absorbed"], "absorbed_test": log["absorbed_test"], "fleet": f, "cover": False,
                            "covered_groups": [], "approx": p.get("approx"), "pool_cover": None, "demand_total": None,
                            "routes": routes, "schedule_date": sched_date, "schedule": vehicle_schedule(run_dir),
                            "schedule_assumption": "walk",
                            "exp": summ(label, f, "nowalk", "exp"), "test": summ(label, f, "nowalk", "test"), **extra,
                            "coverage_walk": p["coverage"], "outside_stops": outside([s for rt in routes for s in rt["stops"]]),
                            "coverage_far": coverage_far([s for rt in routes for s in rt["stops"]])})
            log["configs"].setdefault(label, {"name": config_name(label)})
    return muni


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--lab", type=Path, help="run_networks.py --out 폴더")
    ap.add_argument("--sweep", type=Path, help="hub_sweep.py --out 폴더(구별 실험). --lab 대신. hub_sweep.json이 아직 없으면 미리보기"
                    "(지도 레이어만, 사전 계산 안 없음)")
    ap.add_argument("--muni-plan", type=Path, help="지자체안 plan.json(평가 전이라도 지도에 노선을 그린다)")
    ap.add_argument("--boundaries", type=Path, help="fetch_osm.py --boundaries 출력(시·군·구 경계)")
    ap.add_argument("--walk-eval", default="", help="walk_eval.py --out 폴더들(쉼표). 그 안들을 사전 계산 안으로 더하고 "
                    "걷기 400m 가정 결과(exp_walk·test_walk)를 붙인다(--sweep일 때)")
    args = ap.parse_args()
    root = args.region / f"{args.gu}_0610_v4"
    info = D.check_root(root, "exp")
    if bool(args.lab) == bool(args.sweep):
        ap.error("--lab와 --sweep 중 하나만 준다")
    lab = (args.lab or args.sweep).resolve()
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
    ab = Absorber(root, AbsorbParams())
    ndays = len({r.date for r in rows})
    for v in ("affected_maeul", "affected_gg", "affected_maeul_gg"):
        res, _ = ab.absorb(rows, *VARIANT_FLAGS[v])
        demand["precise_" + v] = stop_residual(residual_rows(rows, res), station_stops, ndays)
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
    sweep = bool(args.sweep)
    preview = sweep and not (lab / "hub_sweep.json").exists()
    log = ({"plans": [], "configs": {}, "absorbed": None, "absorbed_test": None} if preview else
           json.loads((lab / ("hub_sweep.json" if sweep else "run_networks.json")).read_text(encoding="utf-8")))
    if sweep:
        (lab / "net").mkdir(parents=True, exist_ok=True)
        if not (lab / "net" / "road.json").exists():
            (lab / "net" / "road.json").write_text(json.dumps(rn.road_doc, separators=(",", ":")), encoding="utf-8")
        if not (lab / "net" / "turns.json").exists():
            (lab / "net" / "turns.json").write_text(json.dumps(rn.turn_edges(), separators=(",", ":")), encoding="utf-8")
        for p in log["plans"]:
            p.update({"absorb": "precise", "absorbed": log["absorbed"], "absorbed_test": log["absorbed_test"]})
    sched_date = D.SPLITS["exp"]["dates"][2]
    results = []
    for p in log["plans"]:
        plan = json.loads(Path(p["file"]).read_text(encoding="utf-8"))
        vdir = lab / p["config"] if sweep else lab / p["net"] / p["variant"]
        routes = []
        for r in plan["routes"]:
            meta = p["routes"][r["route_id"]]
            routes.append({"id": r["route_id"], "stops": r["stops"], "vehicles": r["vehicles"], **meta,
                           "coords": concat([segpaths.get(f"{a}>{b}") or rn.path_any(a, b)
                                             for a, b in zip(r["stops"], r["stops"][1:])])})
        results.append({"net": p["net"], "variant": p["variant"].removeprefix("precise_").split("-")[0],
                        "run_variant": p["variant"].split("-")[0], "config": p.get("config"),
                        "absorb": p.get("absorb", "bundle"), "absorbed": p.get("absorbed"), "absorbed_test": p.get("absorbed_test"),
                        "fleet": p["fleet"], "cover": p.get("cover", False),
                        "covered_groups": p.get("covered_groups", []), "approx": p["approx"],
                        "pool_cover": p["pool_cover"], "demand_total": p["demand_total"], "routes": routes,
                        "schedule_date": sched_date,
                        "schedule": vehicle_schedule(vdir / "runs_exp" / p.get("label", "ours") / f"{p['variant'].split('-')[0]}_{sched_date}" / f"f{p['fleet']}" / "run"),
                        "exp": runs_summary(vdir / "runs_exp" / "runs.json", p["fleet"], p.get("label", "ours")),
                        "test": runs_summary(vdir / "runs_test" / "runs.json", p["fleet"], p.get("label", "ours"))})
    walk_muni = {}
    if sweep and not preview:
        walk_muni = add_walk(args, lab, out, info, net, rn, segpaths, demand, results, log, sched_date)
    dump(out / "results.json", results)
    mdir = lab / "muni"
    if sweep and not (mdir / "muni.json").exists() and args.muni_plan and args.muni_plan.exists():
        # 평가 전: 전사본 노선만 그린다(망에 없는 정류장은 지도에서만 뺀다)
        mp = json.loads(args.muni_plan.read_text(encoding="utf-8"))
        routes = []
        for r in mp["routes"]:
            st_ = [x for x in r["stops"] if x in net.stops]
            if len(st_) >= 2:
                routes.append({"id": r["route_id"], "stops": st_, "vehicles": r["vehicles"],
                               "coords": concat([segpaths.get(f"{a}>{b}") or rn.path_any(a, b) for a, b in zip(st_, st_[1:])])})
        f = sum(r["vehicles"] for r in mp["routes"])
        dump(out / "municipal.json", {"fleet": f, "plan_vehicles": f, "routes": routes, "dropped_stops": {}, "dropped_routes": [],
                                      "added_segments": 0, "absorbed": None, "absorbed_test": None, "exp": None, "test": None,
                                      "pending": True})
    elif sweep and (mdir / "muni.json").exists():
        mj = json.loads((mdir / "muni.json").read_text(encoding="utf-8"))
        mp = json.loads((mdir / "plan.json").read_text(encoding="utf-8"))
        f = mj["fleet"]
        routes = [{"id": r["route_id"], "stops": r["stops"], "vehicles": r["vehicles"],
                   "coords": concat([segpaths.get(f"{a}>{b}") or rn.path_any(a, b) for a, b in zip(r["stops"], r["stops"][1:])])}
                  for r in mp["routes"]]
        dump(out / "municipal.json", {"fleet": f, "plan_vehicles": mj["plan_vehicles"], "routes": routes,
                                      "dropped_stops": mj["dropped_stops"], "dropped_routes": mj["dropped_routes"],
                                      "added_segments": mj["added_segments"], "absorbed": log["absorbed"],
                                      "absorbed_test": log["absorbed_test"],
                                      "exp": runs_summary(mdir / "runs_exp" / "runs.json", f, "muni"),
                                      "test": runs_summary(mdir / "runs_test" / "runs.json", f, "muni"), **walk_muni})

    lons = [s.lon for s in net.stops.values()]
    lats = [s.lat for s in net.stops.values()]
    dump(out / "meta.json", {"gu": info["gu"], "gu_key": args.gu, "fleets": info["fleets"], "variants": list(D.VARIANTS),
                             "network_snapshot_id": info["network_snapshot_id"],
                             "bbox": [min(lons), min(lats), max(lons), max(lats)], "roadnet": stats,
                             "dates_exp": D.SPLITS["exp"]["dates"], "dates_test": D.SPLITS["test"]["dates"],
                             **({"mode": "sweep", "region_root": str(root.resolve()), "nets": ["road"],
                                 "configs": log["configs"], "results": not preview} if sweep else {"nets": ["orig", "road"]})})
    print(json.dumps({"layers": sorted(p.name for p in out.iterdir()), "results": len(results), "strike_patterns": len(strike),
                      "absorbing": len(absorbing)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
