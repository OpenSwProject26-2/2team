"""망(원래·도로)별로 routegen 기본 설정 안을 만들고 실험·테스트 묶음에서 돌린다(실험실 사전 계산).

    python3 experiments/routegen_lab/run_networks.py --region <zip_v4/region> --osm <guro_roads.json> \
        --gu guro --out <저장소 밖>/lab_guro [--jobs 7]

- 원래 망: 묶음의 `network/travel_times.json`(구간 거리만 도로 매칭 길이로), 도로 망: `roadnet.RoadNet`이 만든 망.
- 생성: 거리 기준은 모두 망 위 버스 노선 거리(distance_basis="network"), 정류장에서 U턴 금지(도로 조각 기록).
- 배분: 열린 노선은 n0 아래로 줄이지 않는다. 역 보장(cover) 끈 안·켠 안을 모두 만든다.
- 묶음은 원본을 복사하고 `travel_times.json`만 그 망으로 바꾼 뒤 `run_inputs`를 시뮬레이터 함수로 다시 만든다.
- 정밀 흡수 변형 precise_*: 마을은 남는 정원만큼, 경기는 그 정류장·역 정류장 정차 + 남는 정원만큼만 흡수(routegen.absorb,
  기본 가정)한 뒤 남은 수요로 demand_bins를 다시 만든다(시뮬레이터 realize_bins).
- 실행은 `experiments/guro_exp/run_matrix.py`(변환기 → M05 → M07 `--max-recompute 200000`).
"""

from __future__ import annotations

import argparse
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
from busstrike.sim import io as sio  # noqa: E402
from busstrike.sim.arrivals import build_run_inputs  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

from roadnet import RoadNet  # noqa: E402

from busstrike.api.routegen_lab import VARIANT_FLAGS, make_bundle  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, AbsorbParams, residual_rows  # noqa: E402

RUN_MATRIX = REPO / "experiments" / "guro_exp" / "run_matrix.py"


def bundles_for(src_root: Path, dst: Path, doc_path: Path, variant: str, fleets: list[int]) -> Path:
    out = dst / "bundles"
    for b in sorted((src_root / "bundles").glob(f"{variant}_2026*")):
        if b.name.rsplit("_", 1)[0] != variant:
            continue
        t = out / b.name
        t.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b / "demand_bins.csv", t / "demand_bins.csv")
        shutil.copyfile(doc_path, t / "travel_times.json")
        for f in fleets:
            shutil.copyfile(b / f"scenario_f{f}.json", t / f"scenario_f{f}.json")
            ri = json.loads((b / f"run_inputs_f{f}.json").read_text(encoding="utf-8"))
            build_run_inputs(ri["input_bundle_id"] + "-" + doc_path.stem, t / f"scenario_f{f}.json",
                             t / "demand_bins.csv", t / "travel_times.json", ri["seed"], t / f"run_inputs_f{f}.json")
    return out


def plan_and_run(args, log, base, info, net_name, net, variant, demand, exp_b, test_b, vdir, extra=None) -> None:
    """한 (망, 변형): 후보 생성 → 역 보장 끔/켬 × 대수 배분 → 실험·테스트 묶음 실행."""
    fleets = info["fleets"]
    scen = D.read_scenario(next(exp_b.iterdir()), fleets[0])
    p = G.Params(home_gu=info["gu"], distance_basis="network")
    res = G.generate(net, demand, scen, p)
    pool = G.pools(res.candidates, p.jaccard)["ours"][0]
    by = {c.route_id: c for c in pool}
    groups = {c.route_id: "·".join(c.hubs) for c in pool}
    weight = {"·".join(c.hubs): sum(res.hubs.get(h, 0.0) for h in c.hubs) for c in pool}
    keys = set()
    for c in pool:
        keys.update(c.cover_keys)
    plan_args = []
    for cover, f in [(cv, f) for cv in (False, True) for f in fleets]:
        m = A.Model([(c.route_id, c.cycle_s, c.cover_keys) for c in pool], demand.od,
                    A.AllocParams(cover_groups=cover), groups, weight)
        a = A.allocate(m, f)
        tag = "cover" if cover else "ours"
        pid = f"plan-{net_name}-{variant}-f{f}-{tag}"
        doc_plan = {"plan_id": pid, "revision": 1, "network_snapshot_id": net.tt.network_snapshot_id,
                    "routes": [{"route_id": f"r{i:02d}", "stops": list(by[k].stops), "vehicles": v}
                               for i, (k, v) in enumerate(sorted(a["n"].items()), 1)]}
        path = vdir / "plans" / f"{pid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        sio.write_bytes(path, sio.json_bytes(doc_plan))
        plan_args += ["--plan", f"{tag}:{f}={path}"]
        det = m.detail(a["n"])
        log["plans"].append({"net": net_name, "variant": variant, "fleet": f, "cover": cover, "label": tag,
                             "file": str(path), "covered_groups": a["covered_groups"],
                             "approx": round(a["value"], 1), "pool_cover": round(sum(demand.od_total(k) for k in keys), 1),
                             "demand_total": round(demand.total(), 1), **(extra or {}),
                             "routes": {f"r{i:02d}": {"kind": by[k].kind, "origin": by[k].origin, "cycle_s": by[k].cycle_s,
                                                      "hubs": [base.stations[h].name for h in by[k].hubs],
                                                      "approx": round(sum(det["by_route"][k]), 1)}
                                        for i, k in enumerate(sorted(a["n"]), 1)}})
    for split, b in (("exp", exp_b), ("test", test_b)):
        cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(b), "--out", str(vdir / f"runs_{split}"),
               "--src", str(REPO / "src"), "--jobs", str(args.jobs), *plan_args]
        rc = subprocess.run(cmd, check=False).returncode
        print(f"{net_name} {variant} {split}: rc={rc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--precise-only", action="store_true",
                    help="이미 만든 망(--out/net)으로 정밀 흡수 변형(precise_*)만 돌려 run_networks.json에 더한다")
    args = ap.parse_args()
    root = args.region / f"{args.gu}_0610_v4"
    test_root = args.region / f"{args.gu}_0610_v4_test"
    info = D.check_root(root, "exp")
    D.check_root(test_root, "test")
    fleets = info["fleets"]
    out = args.out.resolve()
    (out / "net").mkdir(parents=True, exist_ok=True)
    road_path, orig_path = out / "net" / "road.json", out / "net" / "orig.json"
    base = D.load_network(root)
    if args.precise_only:
        log = json.loads((out / "run_networks.json").read_text(encoding="utf-8"))
        log["plans"] = [p for p in log["plans"] if not p["variant"].startswith("precise_")]
        turns = json.loads((out / "net" / "turns.json").read_text(encoding="utf-8"))
    else:
        rn = RoadNet(args.osm, root)
        stats = rn.build()
        sio.write_bytes(road_path, sio.json_bytes(rn.road_doc))
        sio.write_bytes(orig_path, sio.json_bytes(rn.orig_doc))
        turns = rn.turn_edges()
        (out / "net" / "turns.json").write_text(json.dumps(turns, separators=(",", ":")), encoding="utf-8")
        (out / "net" / "roadnet_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
        log = {"gu": info["gu"], "fleets": fleets, "roadnet": stats, "plans": []}
    rows = D.load_flows(root)
    for net_name, doc_path in (("orig", orig_path), ("road", road_path)):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        net = D.Network(TravelTimeTable.from_dict(doc), base.stops, base.stations, turns)
        if not args.precise_only:
            for variant in D.VARIANTS:
                demand = D.build_demand(rows, variant)
                vdir = out / net_name / variant
                exp_b = bundles_for(root, vdir / "exp", doc_path, variant, fleets)
                test_b = bundles_for(test_root, vdir / "test", doc_path, variant, fleets)
                plan_and_run(args, log, base, info, net_name, net, variant, demand, exp_b, test_b, vdir)
        # 정밀 흡수(마을: 정원, 경기: 정차·역·정원, 기본 가정) — 실험·테스트 각자의 수요·승차 자료로 계산
        ap0 = AbsorbParams()
        for variant in ("affected_maeul", "affected_gg", "affected_maeul_gg"):
            m, g = VARIANT_FLAGS[variant]
            vname = "precise_" + variant
            vdir = out / net_name / vname
            parts = {}
            for split, sroot in (("exp", root), ("test", test_root)):
                srows = D.load_flows(sroot)
                resid, summ = Absorber(sroot, ap0).absorb(srows, m, g)
                rrows = residual_rows(srows, resid)
                days = len({r.date for r in srows})
                for date in sorted({r.date for r in srows}):
                    make_bundle(sroot / "bundles" / f"affected_{date}", vdir / split / "bundles" / f"{vname}_{date}",
                                [r for r in rrows if r.date == date], doc_path, fleets, f"precise-{net_name}")
                parts[split] = (rrows, {k: round(v / days, 1) for k, v in summ.items()})
            demand = D.build_demand(parts["exp"][0], "affected")
            plan_and_run(args, log, base, info, net_name, net, vname, demand, vdir / "exp" / "bundles",
                         vdir / "test" / "bundles", vdir,
                         {"absorb": "precise", "absorb_params": ap0.__dict__, "absorbed": parts["exp"][1],
                          "absorbed_test": parts["test"][1]})
    (out / "run_networks.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
