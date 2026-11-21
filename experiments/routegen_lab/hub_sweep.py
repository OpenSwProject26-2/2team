"""생성 설정(허브 기준·정차 수 상한)을 바꿔 가며 구별로 후보 생성 → 배분 → 실험·테스트 묶음 실행(도로 망, 정밀 흡수).

    python3 experiments/routegen_lab/hub_sweep.py --region <zip_v4/region> --osm-dir <저장소 밖>/osm \
        --gu guro --out <저장소 밖>/hub_sweep/guro [--jobs 7]

- 망: `roadnet.RoadNet`(OSM 차도 위 버스 구간, 정류장은 선분 투영 + 진행 방향별 정차 노드), 거리 기준은 망 위 버스 노선 거리,
  정류장 U턴 금지. `<out>/net/`에 road.json·turns.json(turns-v2)·roadnet_stats.json·snap.json(정류장 배치)을 쓴다.
- 수요: 정밀 흡수(마을 정원, 경기 정차·역·정원, 기본 가정) 뒤 남은 셔틀 수요(`precise_affected_maeul_gg`).
- 생성 설정·흡수 가정은 구별 설정 파일(`--params`, 기본 gu_params.json)에서 읽는다(구마다 덮어쓸 수 있음).
  실제로 쓴 값은 hub_sweep.json의 params에 남긴다. 역 보장(cover)은 끈 안만.
- 실행은 `run_networks.plan_and_run`(변환기 → M05 → M07 `--max-recompute 200000`). 결과 요약은 `hub_sweep.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.api.routegen_lab import make_bundle  # noqa: E402
from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, residual_rows  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

from roadnet import RoadNet, write_net  # noqa: E402
from run_networks import plan_and_run  # noqa: E402

PARAMS = HERE.parent / "gu_params.json"
VNAME = "precise_affected_maeul_gg"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--osm-dir", required=True, type=Path)
    ap.add_argument("--gu", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--params", type=Path, default=PARAMS, help="구별 설정 파일")
    ap.add_argument("--configs", default="", help="돌릴 생성 설정 이름(쉼표). 비우면 설정 파일의 전부")
    args = ap.parse_args()
    root, test_root = args.region / f"{args.gu}_0610_v4", args.region / f"{args.gu}_0610_v4_test"
    info = D.check_root(root, "exp")
    cfg = gu_config.load(args.params, args.gu)
    D.check_root(test_root, "test")
    fleets = info["fleets"]
    out = args.out.resolve()
    (out / "net").mkdir(parents=True, exist_ok=True)
    road_path = out / "net" / "road.json"
    rn = RoadNet(args.osm_dir / f"{args.gu}_roads.json", root)
    stats = rn.build()
    write_net(rn, out / "net", stats)     # road.json·turns.json·roadnet_stats.json·snap.json(edge 망: 정류장 배치)
    turns = rn.turn_edges()
    base = D.load_network(root)
    net = D.Network(TravelTimeTable.from_dict(rn.road_doc), base.stops, base.stations, turns)
    ap0 = cfg.absorb_params()
    parts = {}
    for split, sroot in (("exp", root), ("test", test_root)):
        srows = D.load_flows(sroot)
        resid, summ = Absorber(sroot, ap0).absorb(srows, True, True)
        rrows = residual_rows(srows, resid)
        dates = sorted({r.date for r in srows})
        for date in dates:
            make_bundle(sroot / "bundles" / f"affected_{date}", out / split / "bundles" / f"{VNAME}_{date}",
                        [r for r in rrows if r.date == date], road_path, fleets, "precise-road")
        parts[split] = (rrows, {k: round(v / len(dates), 1) for k, v in summ.items()},
                        round(sum(float(r.flow) for r in srows) / len(dates), 1))
    demand = D.build_demand(parts["exp"][0], "affected")
    log = {"gu": info["gu"], "fleets": fleets, "roadnet": stats, "absorbed": parts["exp"][1],
           "affected_total": parts["exp"][2], "absorbed_test": parts["test"][1], "affected_total_test": parts["test"][2],
           "configs": {}, "plans": [], "params": cfg.record()}
    for name in (args.configs.split(",") if args.configs else list(cfg.configs)):
        p = cfg.params(name, home_gu=info["gu"])
        log["configs"][name] = {k: v for k, v in cfg.configs[name].items()}
        plan_and_run(args, log, base, info, "road", net, f"{VNAME}-{name}", demand, out / "exp" / "bundles",
                     out / "test" / "bundles", out / name, {"config": name}, params=p, covers=(False,))
        (out / "hub_sweep.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
