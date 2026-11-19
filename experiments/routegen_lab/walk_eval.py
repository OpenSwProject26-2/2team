"""걷기(도보 R m)를 넣은 안 만들기와 평가를 한 구에서 돌린다(hub_sweep 결과 위에서).

    python3 experiments/routegen_lab/walk_eval.py --sweep <hub_sweep2/<구>> --region <region> --osm <구_roads.json> \
        --gu <slug> --out <저장소 밖>/walk_eval/<구> [--radius 400] [--service-start 07:00,06:00] [--no-run] [--jobs 6]

안(plan)
- muni: 지자체안(`<sweep>/muni/plan.json`, eval_municipal이 망에 맞춘 것). 그 대수에서만.
- s12: 기존 우리 안(허브 135·정차 12, hub_sweep 결과 그대로).
- walk: 같은 후보(허브 135·정차 12)로, 배분기가 평가 규칙으로 덮는 수요를 셈(형평 하한 없음).
- eqHH(3단계 기본): 같은 후보로, 배분기(`allocate`)가 형평 하한(탈 수 있는 수요 비율 전체·역 800m 밖 ≥ 지자체안 값,
  `equity` 설정)과 노선 대당 하한(근사 대당 탑승 ≥ `equity.route_min_per_vehicle`)을 지키며 근사 탑승을 최대로 한다.
  HH = 운행 시작 시각(07이면 07:00 시작: 근사가 6시 수요를 태우지 못한다고 본다). 하한을 못 맞추면 경고하고 기록한다.
  근사의 정원 항은 구간별 최대 재차 인원(`cap_mode="segment"`, spans = 평가 규칙의 재배정 짝).
- frHH: 같은 후보·같은 근사·같은 운행 시작 시각으로 하한 없이(형평 하한·노선 대당 하한 끔) 근사 탑승 최대(s12 설정의 비교 안).
- 순환(3b단계, `--loop`, 설정 walk.generate.loop): 후보가 한 바퀴 순환 정차열이다(`generate` 6b, `loop.py`).
  안 이름에 방식 꼬리표를 붙인다: demand = "c"(예: eqmc07, frmc07, walkmc), deadhead(공차 경로를 영업으로) = "cd".
  `--loop both`면 생성을 한 번 하고 방식마다 후보 풀(중복 제거)을 따로 만들어 두 방식의 안을 모두 만든다.
  안 기록(`route_info`)에 노선마다 꺾는 정류장 순번·고리 닫기 공차(초·정류장)·방향별 편도 시간과 정차 수를 남긴다.
- (재현용) covXX / covXXrL: 예전 "형평 먼저"(`walk.coverage_first`). `--targets`를 줄 때만 만든다. 기본 경로는 쓰지 않는다.
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
from busstrike.planning.routegen.equity import EquityIndex  # noqa: E402
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


def code_state() -> dict:
    """커밋·작업 폴더 변경 여부·생성기/배분기 코드 해시(결과에 남긴다)."""
    def git(*a):
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False).stdout.strip()
    rg = REPO / "src" / "busstrike" / "planning" / "routegen"
    return {"commit": git("rev-parse", "HEAD"), "tracked_changes": bool(git("status", "--porcelain", "--untracked-files=no")),
            "sha256": {f.name: sio.sha256_file(f) for f in sorted(rg.glob("*.py"))} | {HERE.name: sio.sha256_file(HERE)}}


def _hhmm(x: str) -> int:
    h, m = x.split(":")
    return int(h) * 3600 + int(m) * 60


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
    ap.add_argument("--service-start", default=None, help="안 만들기 운행 시작 시각(쉼표로 여러 개, 비우면 설정 파일 walk.service_starts)")
    ap.add_argument("--equity-floor", default=None, help='형평 하한: muni / none / "전체,역800m밖"(비율). 비우면 설정 파일 equity.floor')
    ap.add_argument("--floor-combos", default="base", choices=("base", "all"),
                    help="형평 하한을 걸 평가 조합: base = 기본 조합 하나(기본), all = 9개 조합 모두(안 이름 eqa…)")
    ap.add_argument("--route-min", type=float, default=None, help="노선 근사 대당 탑승 하한(비우면 설정 파일 equity.route_min_per_vehicle)")
    ap.add_argument("--cap-mode", default="segment", choices=list(A.CAP_MODES), help="근사 정원 항")
    ap.add_argument("--no-run", action="store_true", help="안만 만들고 시뮬레이션은 하지 않는다(평가는 eval_rules.py)")
    ap.add_argument("--lines", type=Path, default=HERE.parent / "station_lines.csv", help="역 → 호선·지선 꼬리표 표")
    ap.add_argument("--loop", default=None, choices=("none", "demand", "deadhead", "both"),
                    help="순환 후보 방식(비우면 설정 파일 walk.generate.loop, none = 예전 편도 + 공차 복귀)")
    ap.add_argument("--gen-jobs", type=int, default=8, help="순환 돌아오는 길 만들기 프로세스 수(결과는 같다)")
    ap.add_argument("--free", action="store_true", help="하한 없는 비교 안 frHH도 안으로 쓴다(기록은 늘 남긴다)")
    ap.add_argument("--gen", default="", help='생성 설정 덮어쓰기 JSON(예: \'{"loop_max_stops": null, "loop_max_stops_total": 20}\')')
    args = ap.parse_args()
    muni_dirs = {k: Path(v).resolve() for k, v in (x.split("=", 1) for x in args.muni)}
    cfg = gu_config.load(args.params, args.gu)
    wc = cfg.walk                                     # 명령줄 값이 있으면 그것, 없으면 구별 설정 파일
    args.radius = args.radius if args.radius is not None else float(wc.get("radius_m", 400))
    args.near_m = args.near_m if args.near_m is not None else float(wc.get("near_m", 800))
    args.near_discount = args.near_discount if args.near_discount is not None else float(wc.get("near_discount", 1.0))
    args.multi = args.multi if args.multi is not None else bool(wc.get("multi", False))
    args.lambdas = args.lambdas or ",".join(str(x) for x in wc.get("lambdas", [0]))
    args.targets = args.targets or ",".join(str(x) for x in wc.get("targets", []))   # 비면 형평 먼저(예전)를 만들지 않는다
    starts = [x for x in (args.service_start or ",".join(wc.get("service_starts", ["06:00"]))).split(",") if x]
    eqc = cfg.equity
    floor_cfg = args.equity_floor if args.equity_floor is not None else eqc["floor"]
    route_min = args.route_min if args.route_min is not None else float(eqc["route_min_per_vehicle"])
    far_m = float(eqc["far_m"])
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
    nb_near = walk_neighbors(rn, max(args.near_m, far_m), home)
    near = {k for k in weight if any(x in st_stops and w <= args.near_m + 1e-9 for x, w in nb_near.get(k, {}).items())}
    weight_far = {k: v for k, v in weight.items() if k not in near}
    weight_eq = {k: v * (args.near_discount if k in near else 1.0) for k, v in weight.items()}
    dsfx = "d" if args.near_discount < 1.0 else ""

    # 후보(허브 135·정차 12) + 걷기 반영 덮는 OD
    scen = D.read_scenario(next((sw / "exp" / "bundles").iterdir()), fleets[0])
    p = cfg.walk_params(home_gu=info["gu"], multi=args.multi,
                        **({} if args.loop is None else {"loop": None if args.loop == "none" else args.loop}),
                        **(json.loads(args.gen) if args.gen else {}))
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
    gres = G.generate(net, demand, scen, p, reach=reach, jobs=args.gen_jobs)
    # 후보 풀: 순환 방식마다 따로 중복 제거(방식끼리 서로 지우지 않게). 꼬리표 lsfx는 안 이름에 붙는다
    lsfx_of = {"demand": "c", "deadhead": "cd", None: ""}
    if p.loop == "both":
        psets = [(lsfx_of[m], G.pools([c for c in gres.candidates if c.loop == m], p.jaccard)["ours"][0])
                 for m in ("demand", "deadhead")]
    else:
        psets = [(lsfx_of[p.loop], G.pools(gres.candidates, p.jaccard)["ours"][0])]
    pool = [c for _, ps in psets for c in ps]
    by = {c.route_id: c for c in pool}
    ride = None
    if reach is not None:
        from eval_rules import ride_fn
        ride = ride_fn(net.tt)
    spans = {c.route_id: (None if reach is None else reach.spans(c.stops, ride=ride)) for c in pool}
    wkeys = {c.route_id: (WK.walk_cover_keys(list(c.stops), keys, nb) if reach is None else spans[c.route_id][0])
             for c in pool}
    sp_of = {rid: (None if v is None else v[1]) for rid, v in spans.items()}
    # 형평 지표(설계 2.2): 그 구 동네 정류장 수요, 역 far_m 밖 = 지정 정류장 도로 도보 far_m 안에 없는 정류장
    eq_ix = EquityIndex.build({k: demand.od_total(k) for k in keys}, st_stops, home, nb_near, far_m)
    log = {"gu": info["gu"], "radius_m": args.radius, "fleets": fleets, "pool": len(pool),
           "code": code_state(), "command": sys.argv,
           "loop": p.loop, "pools": {(lsfx or "oneway"): len(ps) for lsfx, ps in psets},
           "generate": {"seconds": gres.seconds, "dropped": gres.dropped, "candidates": len(gres.candidates),
                        "loop_reasons": {k: v for k, v in gres.reasons.items() if k.startswith("loop/")}},
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

    def route_info(ids: list[str]) -> list[dict]:
        """순환 노선 기록: 꺾는 정류장 순번·고리 닫기 공차·방향별 편도 시간과 정차 수(설계 6.1b)."""
        out_ = []
        for cid in ids:
            c = by[cid]
            out_.append({"candidate": cid, "kind": c.kind, "origin": c.origin, "hubs": list(c.hubs), "loop": c.loop,
                         "loop_variant": c.loop_variant, "turn": c.turn, "cycle_s": c.cycle_s,
                         "closure_s": c.closure_s, "closure_stops": list(c.return_stops),
                         "out_oneway_s": c.out_oneway_s, "ret_oneway_s": c.ret_oneway_s,
                         "out_n_stops": c.out_n_stops, "ret_n_stops": c.ret_n_stops, "cover": round(c.cover, 1)})
        return out_

    def record(label: str, fleet: int, routes: list[tuple[list[str], int]], extra: dict) -> None:
        if not want(label):
            return
        path = write_plan(out / "plans" / f"{label}-f{fleet}.json", f"plan-{label}-f{fleet}", snap, routes)
        plan_specs.setdefault(label, {})[fleet] = path
        stops = {s for r, _ in routes for s in r}
        eqv = eq_ix.of_plan(reach, [r for r, _ in routes]) if reach is not None else \
            eq_ix.ratios(set().union(*(set(WK.walk_cover_keys(list(r), keys, nb)) for r, _ in routes)))
        log["plans"][f"{label}-f{fleet}"] = {"label": label, "fleet": fleet, "routes": len(routes),
                                             "equity": {"total": round(eqv["total"], 4), "far": round(eqv["far"], 4),
                                                        "rule": "legacy" if reach is None else reach.rule.label()},
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
    # 형평 하한: 같은 규칙에서 잰 지자체안 값(muni) / 고정값 / 끔
    log["equity_base"] = eq_ix.record()
    floor = None
    if floor_cfg not in (None, "none"):
        if floor_cfg == "muni":
            mp = json.loads((next(iter(muni_src.values())) if muni_src else sw / "muni") .joinpath("plan.json")
                            .read_text(encoding="utf-8"))
            mv = (eq_ix.of_plan(reach, [r["stops"] for r in mp["routes"]]) if reach is not None else
                  eq_ix.ratios(set().union(*(set(WK.walk_cover_keys(list(r["stops"]), keys, nb)) for r in mp["routes"]))))
            floor = {"total": mv["total"], "far": mv["far"], "source": "muni"}
            if args.floor_combos == "all" and reach is not None:     # 나머지 조합: 그 조합 규칙으로 잰 지자체안 값
                floor["extra"] = []
                for c in cfg.eval_combos():
                    if c.label() == reach.rule.label():
                        continue
                    rc = Reach(ix, c, nb, keys)
                    mv_c = eq_ix.of_plan(rc, [r["stops"] for r in mp["routes"]])
                    cov_c = {cand.route_id: frozenset(rc.cover_keys(cand.stops)) for cand in pool}
                    floor["extra"].append((c.label(), cov_c, mv_c["total"], mv_c["far"]))   # pool = 모든 방식의 후보
        else:
            ft = floor_cfg if isinstance(floor_cfg, dict) else dict(zip(("total", "far"), map(float, floor_cfg.split(","))))
            floor = {"total": float(ft["total"]), "far": float(ft["far"]), "source": "fixed"}
    log["equity_floor"] = None if floor is None else {
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in floor.items() if k != "extra"},
        **({"extra": {lab: {"total": round(ft, 4), "far": round(ff, 4)} for lab, _, ft, ff in floor["extra"]}}
           if floor.get("extra") else {})}
    log["floor_combos"] = args.floor_combos
    log["alloc"] = {"cap_mode": args.cap_mode, "route_min_per_vehicle": route_min, "service_starts": starts}

    def model(start: str | None, eq_floor=None, rmin: float = 0.0) -> A.Model:
        ap_ = A.AllocParams(cap_mode=args.cap_mode, route_min_per_vehicle=rmin,
                            service_start_s=None if start in (None, "06:00") else _hhmm(start))
        ef = None if eq_floor is None else A.EquityFloor.from_index(eq_ix, eq_floor["total"], eq_floor["far"],
                                                                    eq_floor.get("extra", ()))
        if ef is not None and ef.extra:                         # 다른 조합 하한은 이 풀의 노선만
            ids = {rid for rid, _, _ in cands}
            ef = A.EquityFloor(ef.w_total, ef.w_far, ef.den_total, ef.den_far, ef.floor_total, ef.floor_far,
                               tuple((lab, {r: k for r, k in cov.items() if r in ids}, ft, ff)
                                     for lab, cov, ft, ff in ef.extra))
        return A.Model([(rid, cyc, wkeys[rid], sp_of[rid]) for rid, _, cyc in cands], demand.od, ap_, groups, equity=ef)

    def alloc_record(a: dict) -> dict:
        return {k: a[k] for k in ("value", "violation", "feasible", "equity", "per_vehicle", "route_shortfall",
                                  "cap_mode", "service_start_s", "hour_weights", "warnings", "chosen")}

    # 초기해 보강(3단계 보고 5절 4): 같은 풀에서 먼저 만든 해(같은 대수의 다른 운행 시작 시각, 더 적은 대수 + 탐욕 채움)를
    # 국소 탐색 초기해로 더한다. 순서: 대수 작은 것 → 큰 것, 시작 시각 이른 것 → 늦은 것. 결정론.
    starts = sorted(starts, key=_hhmm)
    solved: dict[tuple, dict[tuple[int, str], dict[str, int]]] = {}

    def extra_starts(m: A.Model, kind: tuple, f: int, st: str) -> dict[str, dict[str, int]]:
        out_ = {}
        for (f2, st2), sol in sorted(solved.get(kind, {}).items()):
            if (f2, st2) == (f, st) or f2 > f:
                continue
            out_[f"prev_f{f2}_{st2.replace(':', '')}"] = sol if f2 == f else A.fill(m, f, A.greedy(m, f, sol))
        return out_

    for (lsfx, ps), f in ((x, f) for x in psets for f in sorted(fleets)):
        cands = [(c.route_id, list(c.stops), c.cycle_s) for c in ps]
        if want("walk" + sfx + lsfx):
            m = model(None)
            a = A.allocate(m, f)
            ids = [k for k, _ in sorted(a["n"].items())]
            record("walk" + sfx + lsfx, f, [(list(by[k].stops), v) for k, v in sorted(a["n"].items())],
                   {"approx": round(a["value"], 1), "alloc": alloc_record(a), "route_ids": ids,
                    "route_info": route_info(ids)})
        for st in starts:
            hh = st.split(":")[0]
            label = ("eqa" if args.floor_combos == "all" else "eq") + sfx + lsfx + hh
            flabel = "fr" + sfx + lsfx + hh
            if not (want(label) or (args.free and want(flabel))) or reach is None:
                continue
            m = model(st, floor, route_min)
            a = A.allocate(m, f, extra_starts(m, (lsfx, "eq"), f, st))
            mf = model(st)
            free = A.allocate(mf, f, extra_starts(mf, (lsfx, "free"), f, st))   # 같은 근사, 하한 없이
            solved.setdefault((lsfx, "eq"), {})[(f, st)] = dict(a["n"])
            solved.setdefault((lsfx, "free"), {})[(f, st)] = dict(free["n"])
            fe = m.equity_of(free["n"]) if floor is not None else None
            ids = [k for k, _ in sorted(a["n"].items())]
            fids = [k for k, _ in sorted(free["n"].items())]
            record(label, f, [(list(by[k].stops), v) for k, v in sorted(a["n"].items())],
                   {"approx": round(a["value"], 1), "service_start": st, "alloc": alloc_record(a),
                    "route_ids": ids, "route_info": route_info(ids),
                    "unconstrained": {"n": free["n"], "approx": round(free["value"], 1),
                                      "per_vehicle": free["per_vehicle"],
                                      "equity": None if fe is None else {"total": round(fe["total"], 4),
                                                                         "far": round(fe["far"], 4)},
                                      "same_as_constrained": free["n"] == a["n"]}})
            if args.free:
                record(flabel, f, [(list(by[k].stops), v) for k, v in sorted(free["n"].items())],
                       {"approx": round(free["value"], 1), "service_start": st, "alloc": alloc_record(free),
                        "route_ids": fids, "route_info": route_info(fids),
                        "equity_floor_check": None if fe is None else {"total": round(fe["total"], 4),
                                                                       "far": round(fe["far"], 4)}})
            print(f"{label}-f{f}: 근사 {a['value']:.1f}, 하한 만족 {a['feasible']}, 형평 {a['equity']}, "
                  f"하한 없이 {free['value']:.1f} {fe}", flush=True)
            for w in a["warnings"]:
                print(f"경고 {label}-f{f}: {w}", flush=True)
        for t in [float(x) for x in args.targets.split(",") if x]:
            for lam in [float(x) for x in args.lambdas.split(",")]:
                label = f"cov{int(round(t * 100))}" + (f"r{lam:g}" if lam else "") + sfx + lsfx + dsfx
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

    if args.no_run:
        return 0
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
