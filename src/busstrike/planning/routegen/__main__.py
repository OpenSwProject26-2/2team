"""후보 생성 + per_gu 배분 → 비교 입력 plan.json.

    python -m busstrike.planning.routegen --root <구 실험 묶음 폴더> --out <저장소 밖 폴더> [--fleets 11,13]
        [--tau 0.2 --radius-m 3000 --c0-min 40 --l-max 20,30 --max-stops 8|none --max-detour 1.6 ...]

맨 앞에서 실험 묶음인지 확인한다(`data.check_root(split="exp")`: 망 ID `<구>-buf2km-0610-v2`, README 실험 주간
10/26~30 문구, 묶음 날짜). 다르면 멈춘다. 테스트 묶음으로는 후보를 만들지 않는다.
`--fleets`·`--home-gu`를 주지 않으면 README의 대수("대수 11·13대")와 구 이름을 쓴다.

출력(`--out`)
- bundle_check.json: 잔여 수요 → 시뮬레이터 정수화 결과가 묶음 demand_bins.csv와 같은지(다르면 중단)
- candidates/<변형>.json: 후보 목록(route_id, kind, stops, C, 덮는 수요, origin …), 허브·군집·소요 시간
- plans/<plan_id>.json: 비교 입력(experiments/plans/schema.md) — 변형 4개 × 차량 조건 × {ours, sp}
- manifest.json: 안별 배분·근사 충족(κ=1)·초기해별 국소 탐색 결과·이동 종류별 시도/수락·파라미터·코드 커밋

배분 순서: 피더안(sp 풀)을 먼저 배분하고, 그 배분을 우리 안(ours 풀) 초기해 하나로 넣는다.
그래서 같은 후보 풀에서 우리 안의 근사 ≥ 피더안의 근사다(아니면 멈춘다).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from busstrike.sim import io as sio

from . import allocate as A
from . import data as D
from . import generate as G

PLAN_LABELS = {"ours": "01", "sp": "02"}   # plan_id에 출처를 드러내지 않는다(schema.md §1)


def _git(*args: str) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=Path(__file__).resolve().parent, capture_output=True, text=True,
                           check=False)
    except OSError:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split(","))


def _opt_int(text: str) -> int | None:
    return None if text.lower() in ("none", "inf", "0") else int(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m busstrike.planning.routegen")
    ap.add_argument("--root", required=True, type=Path, help="구 실험 묶음 폴더(README.md, network/, demand/, bundles/)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--fleets", default=None, type=_ints, help="기본: README의 대수")
    ap.add_argument("--home-gu", default=None, help="cross_gu 표시 기준 구. 기본: README의 구 이름")
    ap.add_argument("--variants", default=",".join(D.VARIANTS))
    d = G.Params()
    ap.add_argument("--tau", type=float, default=d.tau)
    ap.add_argument("--tau-step", type=float, default=d.tau_step)
    ap.add_argument("--radius-m", type=float, default=d.radius_m)
    ap.add_argument("--c0-min", type=int, default=d.c0_s // 60)
    ap.add_argument("--l-max", type=_ints, default=d.l_max_min)
    ap.add_argument("--sectors", type=_ints, default=d.sectors)
    ap.add_argument("--max-stops", type=_opt_int, default=d.max_stops, help="N_max. none = 검사하지 않음")
    ap.add_argument("--max-detour", type=float, default=d.max_detour)
    ap.add_argument("--sp-top", type=int, default=d.sp_top)
    ap.add_argument("--jaccard", type=float, default=d.jaccard)
    ap.add_argument("--revision", type=int, default=1)
    ap.add_argument("--condition", default="base", help="실행 조건 이름(manifest에 적는다)")
    args = ap.parse_args(argv)

    root, out = args.root.resolve(), args.out.resolve()
    root_check = D.check_root(root, "exp")
    if args.fleets is None:
        if not root_check["fleets"]:
            raise SystemExit("README에서 대수를 못 읽었다 — --fleets를 준다")
        args.fleets = tuple(root_check["fleets"])
    home_gu = args.home_gu or root_check["gu"]
    if not home_gu:
        raise SystemExit("README에서 구 이름을 못 읽었다 — --home-gu를 준다")
    (out / "candidates").mkdir(parents=True, exist_ok=True)
    (out / "plans").mkdir(parents=True, exist_ok=True)
    started = time.time()

    net = D.load_network(root)
    rows = D.load_flows(root)
    check = D.check_bundles(root, rows, args.fleets[0])
    (out / "bundle_check.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [c["bundle"] for c in check if not c["match"]]
    if bad:
        print(f"잔여 수요가 묶음 demand_bins.csv와 다르다: {bad}", file=sys.stderr)
        return 1

    params = G.Params(c0_s=args.c0_min * 60, radius_m=args.radius_m, tau=args.tau, tau_step=args.tau_step,
                      sectors=args.sectors, l_max_min=args.l_max, max_stops=args.max_stops,
                      max_detour=args.max_detour, sp_top=args.sp_top, jaccard=args.jaccard, home_gu=home_gu)
    ap_ = A.AllocParams()
    manifest = {"condition": args.condition, "fleets": list(args.fleets), "root_check": root_check, "root": str(root), "readme_title": (root / "README.md").read_text(encoding="utf-8").splitlines()[0],
                "network_snapshot_id": net.tt.network_snapshot_id,
                "commit": _git("rev-parse", "HEAD"),
                "branch": _git("branch", "--show-current"),
                "tracked_changes": bool(_git("status", "--porcelain", "--untracked-files=no")),
                "code_sha256": {p.name: sio.sha256_file(p) for p in sorted(Path(__file__).resolve().parent.glob("*.py"))},
                "python": sys.version.split()[0],
                "params": asdict(params) | {"f_min": params.f_min}, "alloc_params": asdict(ap_),
                "bundle_check": {"bundles": len(check), "all_match": not bad}, "variants": {}, "plans": []}

    for variant in args.variants.split(","):
        t0 = time.time()
        demand = D.build_demand(rows, variant)
        scen = {f: D.read_scenario(root / "bundles" / f"{variant}_{demand.dates[0]}", f) for f in args.fleets}
        scen0 = scen[args.fleets[0]]
        policy = scen0.policy
        if policy.max_wait_s != ap_.wait_s or any(v.capacity != ap_.seat for v in scen0.vehicles):
            raise SystemExit("시나리오 정책(대기 한도·좌석)이 배분 파라미터와 다르다")
        res = G.generate(net, demand, scen0, params)
        pools = G.pools(res.candidates, params.jaccard)
        t_gen = time.time() - t0
        vinfo = {
            "demand_total_5day_avg": round(demand.total(), 2),
            "hubs": {k: {"name": net.stations[k].name, "F": round(v, 2), "catchment": res.catchment_sizes[k]}
                     for k, v in res.hubs.items()},
            "overlaps": res.overlaps, "clusters": res.clusters, "dropped": res.dropped, "reasons": res.reasons,
            "reason_sets": res.reason_sets,
            "seconds": res.seconds | {"generate_total": round(t_gen, 2)},
            "candidates": len(res.candidates),
            "by_origin_kind": {}, "pools": {k: {"kept": len(v[0]), "removed_jaccard": v[1]} for k, v in pools.items()},
        }
        for c in res.candidates:
            key = f"{c.origin}/{c.kind}"
            vinfo["by_origin_kind"][key] = vinfo["by_origin_kind"].get(key, 0) + 1
        cand_doc = {"variant": variant, "params": manifest["params"], **vinfo,
                    "pool_members": {k: [c.route_id for c in v[0]] for k, v in pools.items()},
                    "candidates": [c.record() for c in res.candidates]}
        (out / "candidates" / f"{variant}.json").write_text(json.dumps(cand_doc, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")
        by_id = {c.route_id: c for c in res.candidates}
        vinfo["alloc_seconds"] = {}
        # ours 풀은 sp 풀의 정차열을 모두 가져야 한다(같은 후보 풀 비교). 빠진 정차열은 sp 후보를 그대로 더한다.
        ours_by_stops = {c.stops: c.route_id for c in pools["ours"][0]}
        added = [c for c in pools["sp"][0] if c.stops not in ours_by_stops]
        ours_pool = sorted(pools["ours"][0] + added, key=lambda c: c.route_id)
        sp_to_ours = {c.route_id: ours_by_stops.get(c.stops, c.route_id) for c in pools["sp"][0]}
        vinfo["sp_routes_added_to_ours"] = [c.route_id for c in added]
        models = {lab: A.Model([(c.route_id, c.cycle_s, c.cover_keys) for c in pool], demand.od, ap_)
                  for lab, pool in (("sp", pools["sp"][0]), ("ours", ours_pool))}
        for fleet in args.fleets:
            allocs = {}
            for label in ("sp", "ours"):
                model = models[label]
                starts = None
                if label == "ours":
                    starts = {"sp_plan": {sp_to_ours[r]: k for r, k in allocs["sp"]["n"].items()}}
                t1 = time.time()
                alloc = allocs[label] = A.allocate(model, fleet, starts)
                vinfo["alloc_seconds"][f"{label}_f{fleet}"] = round(time.time() - t1, 2)
                if label == "ours" and alloc["value"] < allocs["sp"]["value"] - 1e-6:
                    raise SystemExit(f"{variant} f{fleet}: 우리 안 근사 {alloc['value']:.3f} < 피더안 {allocs['sp']['value']:.3f}")
                det = model.detail(alloc["n"])
                plan_id = f"plan-{PLAN_LABELS[label]}-{variant}-f{fleet}"
                routes = []
                route_map = {}
                for i, cid in enumerate(sorted(alloc["n"]), start=1):
                    rid = f"r{i:02d}"
                    route_map[rid] = cid
                    routes.append({"route_id": rid, "stops": list(by_id[cid].stops), "vehicles": alloc["n"][cid]})
                plan = {"plan_id": plan_id, "revision": args.revision,
                        "network_snapshot_id": net.tt.network_snapshot_id, "routes": routes}
                path = out / "plans" / f"{plan_id}.json"
                sio.write_bytes(path, sio.json_bytes(plan))
                manifest["plans"].append({
                    "plan_id": plan_id, "label": label, "variant": variant, "fleet": fleet, "file": str(path),
                    "sha256": sio.sha256_file(path),
                    "routes": {rid: {"candidate": cid, "vehicles": alloc["n"][cid], "kind": by_id[cid].kind,
                                     "origin": by_id[cid].origin, "cycle_s": by_id[cid].cycle_s,
                                     "approx_served_by_hour": [round(x, 4) for x in det["by_route"][cid]]}
                               for rid, cid in route_map.items()},
                    "approx_served": round(det["total"], 4), "approx_demand_covered": round(det["demand_covered"], 4),
                    "approx_cap_cut": round(det["cap_cut"], 4),
                    "value_greedy": round(alloc["value_greedy"], 4), "value_filled": round(alloc["value_filled"], 4),
                    "greedy": alloc["greedy"], "local_search": alloc["local_search"],
                    "chosen_start": alloc["chosen"], "move_stats": alloc["move_stats"],
                    "starts": {k: {"initial": round(v["initial"], 4), "final": round(v["final"], 4),
                                   "start": v["start"], "n": v["n"], "moves": v["moves"], "stats": v["stats"]}
                               for k, v in alloc["inits"].items()},
                    "pool_size": len(model.ids),
                    "demand_total_5day_avg": round(demand.total(), 4)})
        manifest["variants"][variant] = vinfo
        print(f"{variant}: 허브 {len(res.hubs)} · 군집 {len(res.clusters)} · 후보 {len(res.candidates)} "
              f"(ours {len(pools['ours'][0])}, sp {len(pools['sp'][0])}) · {time.time() - t0:.1f}s", flush=True)
    manifest["wall_seconds"] = round(time.time() - started, 2)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in manifest["plans"]:
        print(f"  {p['plan_id']}: 노선 {len(p['routes'])} · 대수 "
              f"{'/'.join(str(r['vehicles']) for r in p['routes'].values())} · 근사 충족 {p['approx_served']:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
