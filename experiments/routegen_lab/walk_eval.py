"""걷기(도보 R m)를 넣은 평가와 "형평 먼저" 생성을 한 구에서 돌린다(hub_sweep 결과 위에서).

    python3 experiments/routegen_lab/walk_eval.py --sweep <hub_sweep2/<구>> --region <region> --osm <구_roads.json> \
        --gu <slug> --out <저장소 밖>/walk_eval/<구> [--radius 400] [--targets 0.6,0.7,0.8] [--jobs 6]

안(plan)
- muni: 지자체안(`<sweep>/muni/plan.json`, eval_municipal이 망에 맞춘 것). 그 대수에서만.
- s12: 기존 우리 안(허브 135·정차 12, hub_sweep 결과 그대로).
- walk: 같은 후보(허브 135·정차 12)로, 배분기가 걷기를 반영해 덮는 수요를 셈(형평 조건 없음).
- covXX: 형평 먼저 — 걸어서 R m 안 수요가 XX%가 될 때까지 덮는 범위가 넓은 노선을 최소 대수(배차 20분 이하)로 고정하고,
  남은 대수를 걷기 반영 배분기로 채운다. 예산 안에서 목표에 못 닿으면 닿는 데까지.
- covXXrL: 형평 먼저에서 노선 점수 = 새로 덮는 수요 + L × 새로 탈 수 있게 되는 수요(--lambdas). L = 0이 covXX.
--only로 안 이름을 주면 그 안만 만들고 돌려 기존 walk_eval.json에 더한다(걷기 없음 결과는 runs_nowalk/<안>-f<대수>/에).
시뮬레이터 가정(모든 안 같음)
- nowalk: 지금까지와 같다(승객은 원래 정류장에서만 탄다).
- walk: 평가 규칙(설정 파일 eval 절, `reach.EvalRule`, 걷기 반경 = --radius)으로 탈 수 없는 승객을 규칙 안의 (탈, 내릴)
  정류장으로 옮긴다(목적 역 대체 · 역 도착 = 역 주변 정류장 목록 · 동네 쪽 걷기). 안마다 수요 묶음(demand_bins)의 승하차
  정류장만 바꾸고 run_inputs를 다시 만든다. 도보 시간은 넣지 않는다(9개 조합·도보 시간은 eval_rules.py).
  `--legacy-rule`: 2단계 전 규칙(역 고정, 역 쪽도 지정 정류장 R m 안 걷기, `walk.reassign`).
설계(덮는 OD 키: 배분기·형평 먼저의 탈 수 있는 수요, 생성기 후보의 덮개)도 같은 규칙을 쓴다(`--legacy-rule`이면 예전 그대로).
망·흡수는 hub_sweep과 같다(도로 망, 정밀 흡수 기본 가정). 지자체안이 쓰는 무정차 구간을 담은 `road_muni.json`을 모든 안에 쓴다
(다른 안은 그 구간을 쓰지 않는다).
`--muni 이름=<eval_municipal 결과 폴더>`(여러 번): 지자체안 변형(이음 측정 방식·운행 시작 시각 등)을 그 폴더의 plan.json과
묶음(`<폴더>/{exp,test}/bundles`, 그 변형의 road_muni.json·시나리오)으로 따로 돌린다(걷기 없음 `runs_nowalk/<이름>-f<대수>/`,
걷기 `runs_walk/<이름>-f<대수>/`). 이때 우리 안은 지자체안 구간이 없는 `<sweep>/net/road.json` 묶음으로 돈다
(변환기의 공차 복귀 경로가 지자체안 무정차 구간을 쓰지 않게).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import allocate as A  # noqa: E402
from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen import generate as G  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen import walk as WK  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, residual_rows  # noqa: E402
from busstrike.planning.routegen.reach import Reach, StationIndex  # noqa: E402
from busstrike.sim import io as sio  # noqa: E402
from busstrike.sim.arrivals import build_run_inputs  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

from roadnet import RoadNet  # noqa: E402
from run_networks import RUN_MATRIX, bundles_for  # noqa: E402
from walknet import walk_neighbors  # noqa: E402

VARIANT = "precise_affected_maeul_gg"
HEAD = ["aggregate_id", "origin", "destination", "bin_start_s", "bin_width_s", "passenger_count"]


def write_plan(path: Path, pid: str, snap: str, routes: list[tuple[list[str], int]]) -> Path:
    doc = {"plan_id": pid, "revision": 1, "network_snapshot_id": snap,
           "routes": [{"route_id": f"r{i:02d}", "stops": list(s), "vehicles": v} for i, (s, v) in enumerate(routes, 1)]}
    path.parent.mkdir(parents=True, exist_ok=True)
    sio.write_bytes(path, sio.json_bytes(doc))
    return path


def walk_bundles(src: Path, dst: Path, routes: list[list[str]], nb: dict, st_stops: set[str], fleets: list[int],
                 reach: Reach | None = None) -> dict:
    """src 묶음들을 복사하고 demand_bins를 걷기 재배정한 뒤 run_inputs를 다시 만든다(reach가 있으면 그 규칙, 없으면 예전 규칙)."""
    agg = {"direct": 0, "walked": 0, "none": 0, "walk_m": 0.0}
    for b in sorted(src.iterdir()):
        if not b.is_dir():
            continue
        t = dst / b.name
        if t.exists():
            shutil.rmtree(t)
        shutil.copytree(b, t)
        with open(b / "demand_bins.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if reach is None:
            new, s = WK.reassign(rows, routes, nb, st_stops)
        else:
            new, _, s = reach.reassign(rows, routes)
            for k in ("same_station", "alt_station"):
                agg[k] = agg.get(k, 0) + s[k]
        new.sort(key=lambda r: (r["origin"], r["destination"], int(r["bin_start_s"])))
        with open(t / "demand_bins.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(HEAD)
            for i, r in enumerate(new, 1):
                w.writerow([f"od{i:06d}-t{int(r['bin_start_s']):06d}", r["origin"], r["destination"], r["bin_start_s"],
                            r["bin_width_s"], r["passenger_count"]])
        for k in ("direct", "walked", "none"):
            agg[k] += s[k]
        agg["walk_m"] += s["walk_m_avg"] * s["walked"]
        for fl in fleets:
            ri = json.loads((b / f"run_inputs_f{fl}.json").read_text(encoding="utf-8"))
            build_run_inputs(ri["input_bundle_id"] + "-walk", t / f"scenario_f{fl}.json", t / "demand_bins.csv",
                             t / "travel_times.json", ri["seed"], t / f"run_inputs_f{fl}.json")
    n = len(list(src.iterdir()))
    return {"direct": round(agg["direct"] / n, 1), "walked": round(agg["walked"] / n, 1), "none": round(agg["none"] / n, 1),
            **{k: round(agg[k] / n, 1) for k in ("same_station", "alt_station") if k in agg},
            "walk_m_avg": round(agg["walk_m"] / agg["walked"], 1) if agg["walked"] else 0.0}


def finished(out: Path, n: int) -> bool:
    """out/runs.json에 n건이 있고 모두 M07 통과면 끝난 것(다시 돌 때 건너뛴다)."""
    try:
        rs = json.loads((out / "runs.json").read_text(encoding="utf-8"))["runs"]
    except (OSError, ValueError, KeyError):
        return False
    return len(rs) >= n and all(r.get("verify_pass") is True for r in rs)


def run(bundles: Path, out: Path, plans: list[str], jobs: int) -> None:
    if finished(out, len(plans) * len(list(bundles.iterdir()))):
        print(f"건너뜀(이미 끝남): {out}", flush=True)
        return
    cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(bundles), "--out", str(out), "--src", str(REPO / "src"),
           "--jobs", str(jobs)]
    for p in plans:
        cmd += ["--plan", p]
    subprocess.run(cmd, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", required=True, type=Path)
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--params", type=Path, default=HERE.parent / "gu_params.json", help="구별 설정 파일(walk 절)")
    ap.add_argument("--radius", type=float, default=None, help="도보 반경 m(비우면 설정 파일)")
    ap.add_argument("--targets", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--lambdas", default=None, help="형평 먼저 점수에서 탈 수 있는 수요의 무게 L(쉼표로 여러 개, 비우면 설정 파일)")
    ap.add_argument("--only", default="", help="이 안 이름만(쉼표). 비우면 전부")
    ap.add_argument("--multi", action="store_true", default=None, help="여러 역 경유 피더 후보를 더한다(안 이름 끝에 m)")
    ap.add_argument("--near-discount", type=float, default=None,
                    help="형평 먼저 단계에서 역(지정 정류장) 도보 --near-m 안 수요의 무게 배수(1이면 할인 없음, 안 이름 끝에 d)")
    ap.add_argument("--near-m", type=float, default=None)
    ap.add_argument("--muni", action="append", default=[], help="지자체안 변형 이름=eval_municipal 결과 폴더(여러 번)")
    ap.add_argument("--legacy-rule", action="store_true", help="2단계 전 규칙(역 고정, 역 쪽 지정 정류장 걷기)으로 설계·평가")
    ap.add_argument("--lines", type=Path, default=HERE.parent / "station_lines.csv", help="역 → 호선·지선 꼬리표 표")
    args = ap.parse_args()
    muni_dirs = {k: Path(v).resolve() for k, v in (x.split("=", 1) for x in args.muni)}
    cfg = gu_config.load(args.params, args.gu)
    wc = cfg.walk                                     # 명령줄 값이 있으면 그것, 없으면 구별 설정 파일
    args.radius = args.radius if args.radius is not None else float(wc.get("radius_m", 400))
    args.near_m = args.near_m if args.near_m is not None else float(wc.get("near_m", 800))
    args.near_discount = args.near_discount if args.near_discount is not None else float(wc.get("near_discount", 1.0))
    args.multi = args.multi if args.multi is not None else bool(wc.get("multi", False))
    args.lambdas = args.lambdas or ",".join(str(x) for x in wc.get("lambdas", [0]))
    args.targets = args.targets or ",".join(str(x) for x in wc.get("targets", [0.6, 0.7, 0.8]))
    only = set(filter(None, args.only.split(",")))
    want = lambda label: not only or label in only
    sw, out = args.sweep.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = args.region / f"{args.gu}_0610_v4"
    info = D.check_root(root, "exp")
    hs = json.loads((sw / "hub_sweep.json").read_text(encoding="utf-8"))
    fleets = hs["fleets"]
    base = D.load_network(root)
    st_stops = {x for s in base.stations.values() for x in (s.access_stop, s.egress_stop) if x}

    # 도보 이웃(도로를 따라)
    rn = RoadNet(args.osm, root)
    nb = walk_neighbors(rn, args.radius)
    # 망: 지자체 무정차 구간까지 담은 도로 망
    tt_path = sw / "net" / "road.json" if muni_dirs else sw / "muni" / "road_muni.json"
    doc = json.loads(tt_path.read_text(encoding="utf-8"))
    turns_p = sw / "net" / "turns.json"
    if not turns_p.exists():
        rn2 = RoadNet(args.osm, root)
        rn2.build()
        turns_p.write_text(json.dumps(rn2.turn_edges(), separators=(",", ":")), encoding="utf-8")
    net = D.Network(TravelTimeTable.from_dict(json.loads((sw / "net" / "road.json").read_text(encoding="utf-8"))),
                    base.stops, base.stations, json.loads(turns_p.read_text(encoding="utf-8")))
    snap = doc["network_snapshot_id"]

    # 수요(정밀 흡수 뒤, 실험 주간) · 동네 정류장 무게(그 구 안)
    rows = D.load_flows(root)
    res, _ = Absorber(root, cfg.absorb_params()).absorb(rows, True, True)
    demand = D.build_demand(residual_rows(rows, res), "affected")
    home = {s for s, x in base.stops.items() if x.gu == info["gu"]}
    weight = {s: w for s, w in WK.stop_weights(demand.od, st_stops).items() if s in home}
    # 역 근처(지정 정류장 도보 near_m 안): 지하철까지 걸어갈 수 있어 형평 우선순위를 낮출 수 있다
    nb_near = walk_neighbors(rn, args.near_m, set(weight))
    near = {k for k in weight if any(x in st_stops for x in nb_near.get(k, {}))}
    weight_far = {k: v for k, v in weight.items() if k not in near}
    weight_eq = {k: v * (args.near_discount if k in near else 1.0) for k, v in weight.items()}
    dsfx = "d" if args.near_discount < 1.0 else ""

    # 후보(허브 135·정차 12) + 걷기 반영 덮는 OD
    scen = D.read_scenario(next((sw / "exp" / "bundles").iterdir()), fleets[0])
    p = cfg.walk_params(home_gu=info["gu"], multi=args.multi)
    sfx = "m" if args.multi else ""
    keys = sorted(demand.od)
    reach = None
    if not args.legacy_rule:
        rule = cfg.eval_rule(walk_radius_m=args.radius)
        ix = StationIndex.from_root(root, args.lines)
        ix.add_flows(rows)
        troot = args.region / f"{args.gu}_0610_v4_test"
        if troot.exists():
            ix.add_flows(D.load_flows(troot))
        reach = Reach(ix, rule, nb, keys)
    gres = G.generate(net, demand, scen, p, reach=reach)
    pool = G.pools(gres.candidates, p.jaccard)["ours"][0]
    by = {c.route_id: c for c in pool}
    wkeys = {c.route_id: (WK.walk_cover_keys(list(c.stops), keys, nb) if reach is None else reach.cover_keys(c.stops))
             for c in pool}
    log = {"gu": info["gu"], "radius_m": args.radius, "fleets": fleets, "pool": len(pool),
           "rule": "legacy" if reach is None else reach.rule.record(),
           "params": {**cfg.record(), "used": {"radius_m": args.radius, "near_m": args.near_m,
                                                "near_discount": args.near_discount, "multi": args.multi,
                                                "lambdas": args.lambdas, "targets": args.targets}},
           "near_m": args.near_m, "near_share": round(sum(weight[k] for k in near) / sum(weight.values()), 4),
           "pool_multi": sum(1 for c in pool if c.kind == "multi"),
           "pool_cover_direct": round(sum(demand.od_total(k) for k in {k for c in pool for k in c.cover_keys}) / demand.total(), 4),
           "pool_cover_walk": round(sum(demand.od_total(k) for k in {k for ks in wkeys.values() for k in ks}) / demand.total(), 4),
           "plans": {}, "walk_stats": {}, "absorbed": hs["absorbed"], "affected_total": hs["affected_total"],
           "absorbed_test": hs["absorbed_test"], "affected_total_test": hs["affected_total_test"]}
    if only and (out / "walk_eval.json").exists():
        old = json.loads((out / "walk_eval.json").read_text(encoding="utf-8"))
        log["plans"], log["walk_stats"] = old["plans"], old["walk_stats"]
    od_total = {k: demand.od_total(k) for k in keys}
    ride = {rid: set(ks) for rid, ks in wkeys.items()}

    plan_specs: dict[str, dict[int, Path]] = {}

    def record(label: str, fleet: int, routes: list[tuple[list[str], int]], extra: dict) -> None:
        if not want(label):
            return
        path = write_plan(out / "plans" / f"{label}-f{fleet}.json", f"plan-{label}-f{fleet}", snap, routes)
        plan_specs.setdefault(label, {})[fleet] = path
        stops = {s for r, _ in routes for s in r}
        log["plans"][f"{label}-f{fleet}"] = {"label": label, "fleet": fleet, "routes": len(routes),
                                             "vehicles": sum(v for _, v in routes), "stops": len(stops),
                                             "coverage": round(WK.coverage(stops, nb, weight), 4),
                                             "coverage_far": round(WK.coverage(stops, nb, weight_far), 4),
                                             "coverage_direct": round(WK.coverage(stops, {}, weight), 4), **extra,
                                             "route_list": [{"stops": r, "vehicles": v} for r, v in routes]}

    # muni
    muni_src: dict[str, Path] = {}
    for lab, mdir in (muni_dirs.items() if muni_dirs else [("muni", sw / "muni")]):
        mp = json.loads((mdir / "plan.json").read_text(encoding="utf-8"))
        mf = sum(r["vehicles"] for r in mp["routes"])
        mrec = json.loads((mdir / "muni.json").read_text(encoding="utf-8")) if (mdir / "muni.json").exists() else {}
        record(lab, mf, [(r["stops"], r["vehicles"]) for r in mp["routes"]],
               {"muni_dir": str(mdir), **{k: mrec.get(k) for k in ("links_mode", "service_start", "plan_revision")}}
               if muni_dirs else {})
        if muni_dirs:
            muni_src[lab] = mdir
    # s12(기존)
    for pl in hs["plans"]:
        if pl.get("config") == "hub135_s12":
            d = json.loads(Path(pl["file"]).read_text(encoding="utf-8"))
            record("s12", pl["fleet"], [(r["stops"], r["vehicles"]) for r in d["routes"]], {})
    # walk / covXX
    groups = {c.route_id: "·".join(c.hubs) for c in pool}
    cands = [(c.route_id, list(c.stops), c.cycle_s) for c in pool]
    for f in fleets:
        if want("walk" + sfx):
            m = A.Model([(rid, cyc, wkeys[rid]) for rid, _, cyc in cands], demand.od, A.AllocParams(), groups)
            a = A.allocate(m, f)
            record("walk" + sfx, f, [(list(by[k].stops), v) for k, v in sorted(a["n"].items())], {"approx": round(a["value"], 1)})
        for t in [float(x) for x in args.targets.split(",")]:
            for lam in [float(x) for x in args.lambdas.split(",")]:
                label = f"cov{int(round(t * 100))}" + (f"r{lam:g}" if lam else "") + sfx + dsfx
                if not want(label):
                    continue
                m = A.Model([(rid, cyc, wkeys[rid]) for rid, _, cyc in cands], demand.od, A.AllocParams(), groups)
                cf = WK.coverage_first([(rid, seq, m.n0[rid]) for rid, seq, _ in cands], nb, weight_eq, t, f,
                                       ride, od_total, lam)
                m.fixed = cf["fixed"]
                a = A.allocate(m, f)
                record(label, f, [(list(by[k].stops), v) for k, v in sorted(a["n"].items())],
                       {"approx": round(a["value"], 1), "target": t, "lambda": lam,
                        "stage1_coverage": round(cf["coverage"], 4), "stage1_ride": cf["ride"],
                        "stage1_vehicles": cf["vehicles"], "stage1_routes": len(cf["fixed"]),
                        "near_discount": args.near_discount})
    (out / "walk_eval.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in log["plans"].items():
        if only and v["label"] not in only:
            continue
        print(k, {x: v[x] for x in v if x != "route_list"}, flush=True)

    # 시뮬레이션: nowalk(모든 안 한 번에) · walk(안마다 재배정 묶음)
    for split in ("exp", "test"):
        nb_dir = out / "bundles_nowalk" / split
        if not nb_dir.exists():
            bundles_for(sw / split, nb_dir.parent / f"_{split}", tt_path, VARIANT, fleets)
            (nb_dir.parent / f"_{split}" / "bundles").rename(nb_dir)
            shutil.rmtree(nb_dir.parent / f"_{split}", ignore_errors=True)
        specs = [f"{lab}:{f}={path}" for lab, fs in sorted(plan_specs.items()) for f, path in sorted(fs.items())
                 if lab not in muni_src]
        for lab in sorted(set(plan_specs) & set(muni_src)):       # 지자체안 변형: 그 변형의 묶음으로 따로
            for f, path in sorted(plan_specs[lab].items()):
                run(muni_src[lab] / split / "bundles", out / "runs_nowalk" / f"{lab}-f{f}" / split, [f"{lab}:{f}={path}"],
                    args.jobs)
        if only:   # 기존 색인을 덮어쓰지 않도록 안마다 따로
            for lab, fs in sorted(plan_specs.items()):
                if lab in muni_src:
                    continue
                for f, path in sorted(fs.items()):
                    run(nb_dir, out / "runs_nowalk" / f"{lab}-f{f}" / split, [f"{lab}:{f}={path}"], args.jobs)
        elif specs:
            run(nb_dir, out / "runs_nowalk" / split, specs, args.jobs)
        for lab, fs in sorted(plan_specs.items()):
            for f, path in sorted(fs.items()):
                routes = [r["stops"] for r in json.loads(path.read_text(encoding="utf-8"))["routes"]]
                wdir = out / "bundles_walk" / f"{lab}-f{f}" / split
                if finished(out / "runs_walk" / f"{lab}-f{f}" / split, len(list(nb_dir.iterdir()))) \
                        and f"{lab}-f{f}-{split}" in log["walk_stats"]:
                    continue
                src_b, fl_b = (muni_src[lab] / split / "bundles", [f]) if lab in muni_src else (nb_dir, fleets)
                log["walk_stats"][f"{lab}-f{f}-{split}"] = walk_bundles(src_b, wdir, routes, nb, st_stops, fl_b, reach)
                run(wdir, out / "runs_walk" / f"{lab}-f{f}" / split, [f"{lab}:{f}={path}"], args.jobs)
                shutil.rmtree(wdir, ignore_errors=True)          # 묶음은 크다. 결과만 남긴다
                (out / "walk_eval.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "walk_eval.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
