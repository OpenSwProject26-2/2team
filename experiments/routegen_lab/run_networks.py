"""망(원래·도로)별로 routegen 기본 설정 안을 만들고 실험·테스트 묶음에서 돌린다(실험실 사전 계산).

    python3 experiments/routegen_lab/run_networks.py --region <zip_v4/region> --osm <guro_roads.json> \
        --gu guro --out <저장소 밖>/lab_guro [--jobs 7]

- 원래 망: 묶음의 `network/travel_times.json`, 도로 망: `roadnet.RoadNet`이 만든 망.
- 묶음은 원본을 복사하고 `travel_times.json`만 그 망으로 바꾼 뒤 `run_inputs`를 시뮬레이터 함수로 다시 만든다.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=7)
    args = ap.parse_args()
    root = args.region / f"{args.gu}_0610_v4"
    test_root = args.region / f"{args.gu}_0610_v4_test"
    info = D.check_root(root, "exp")
    D.check_root(test_root, "test")
    fleets = info["fleets"]
    out = args.out.resolve()
    (out / "net").mkdir(parents=True, exist_ok=True)

    rn = RoadNet(args.osm, root)
    stats = rn.build()
    road_path = out / "net" / "road.json"
    sio.write_bytes(road_path, sio.json_bytes(rn.road_doc))
    orig_path = out / "net" / "orig.json"
    shutil.copyfile(root / "network" / "travel_times.json", orig_path)
    (out / "net" / "roadnet_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    base = D.load_network(root)
    rows = D.load_flows(root)
    log = {"gu": info["gu"], "fleets": fleets, "roadnet": stats, "plans": []}
    for net_name, doc_path in (("orig", orig_path), ("road", road_path)):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        net = D.Network(TravelTimeTable.from_dict(doc), base.stops, base.stations)
        for variant in D.VARIANTS:
            demand = D.build_demand(rows, variant)
            vdir = out / net_name / variant
            exp_b = bundles_for(root, vdir / "exp", doc_path, variant, fleets)
            test_b = bundles_for(test_root, vdir / "test", doc_path, variant, fleets)
            scen = D.read_scenario(exp_b / f"{variant}_{demand.dates[0]}", fleets[0])
            p = G.Params(home_gu=info["gu"])
            res = G.generate(net, demand, scen, p)
            pool = G.pools(res.candidates, p.jaccard)["ours"][0]
            by = {c.route_id: c for c in pool}
            m = A.Model([(c.route_id, c.cycle_s, c.cover_keys) for c in pool], demand.od, A.AllocParams())
            keys = set()
            for c in pool:
                keys.update(c.cover_keys)
            plan_args = []
            for f in fleets:
                a = A.allocate(m, f)
                pid = f"plan-{net_name}-{variant}-f{f}"
                doc_plan = {"plan_id": pid, "revision": 1, "network_snapshot_id": net.tt.network_snapshot_id,
                            "routes": [{"route_id": f"r{i:02d}", "stops": list(by[k].stops), "vehicles": v}
                                       for i, (k, v) in enumerate(sorted(a["n"].items()), 1)]}
                path = vdir / "plans" / f"{pid}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                sio.write_bytes(path, sio.json_bytes(doc_plan))
                plan_args += ["--plan", f"ours:{f}={path}"]
                log["plans"].append({"net": net_name, "variant": variant, "fleet": f, "file": str(path),
                                     "approx": round(a["value"], 1), "pool_cover": round(sum(demand.od_total(k) for k in keys), 1),
                                     "demand_total": round(demand.total(), 1),
                                     "routes": {f"r{i:02d}": {"kind": by[k].kind, "origin": by[k].origin, "cycle_s": by[k].cycle_s,
                                                              "hubs": [base.stations[h].name for h in by[k].hubs],
                                                              "approx": round(sum(m.detail(a["n"])["by_route"][k]), 1)}
                                                for i, k in enumerate(sorted(a["n"]), 1)}})
            for split, b in (("exp", exp_b), ("test", test_b)):
                cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(b), "--out", str(vdir / f"runs_{split}"),
                       "--src", str(REPO / "src"), "--jobs", str(args.jobs), *plan_args]
                rc = subprocess.run(cmd, check=False).returncode
                print(f"{net_name} {variant} {split}: rc={rc}", flush=True)
    (out / "run_networks.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
