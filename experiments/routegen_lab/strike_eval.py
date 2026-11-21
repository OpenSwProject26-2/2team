"""파업 비율 평가(설계 2.4, 통합 3.1): 파업 비율이 바뀌어도 우리 안이 지자체안보다 나은가.

    python3 experiments/routegen_lab/strike_eval.py --out <저장소 밖> --gu guro [--rates 0.1,0.25,0.5,0.75,1.0] [--draws 5]
        [--only p050_d0,...] [--jobs 4] [--groups 4] [--all-combos p100_d0,p050_d0]

시나리오: 파업 비율 × 무작위 뽑기(strike.draw_strike, seed 20261121). 100%는 뽑기와 무관해 한 번.
안
- 우리 안: 시나리오마다 다시 생성(walk_eval, 버스 통행 가능 도로망 stage1c, 순환 demand, 여러 역 경유, 07:00, 형평 하한,
  필수 정류장, 상대 기준 규칙). 대수 = 고정 11(1월 실운행) + 비례 max(1, round(11 × 비율)). 이름 eqmc07.
- 지자체안(1월, 11대, 07:00): review1119/stage2/muni/muni07(plan.json, road_muni.json). 고정(시나리오와 무관).
- 범용 AI안 6개(gpt-6.2-sol, 11대): routegen_v2/strike_curve/ai/aiXX(지자체안과 같은 OSM 이음 규칙, 복귀 이음 보강). 고정.
평가(모든 안 같은 규칙): 실험 주간 10/26~30만(설정 고정 전, 테스트 주간은 열지 않음). 수요 = 그 시나리오의 셔틀 대상
(파업 몫 → 시내버스 흡수 → 정밀 흡수) 실현(realize_bins, 묶음과 같은 seed). **07:00 시작: 06~07시 수요(bin_start < 25,200)는
모든 안에서 분모에서 뺀다**, 차량 가용 시작 25,200초. 평가 규칙 = eval_rules.py(기본 조합 same_line-w400, --all-combos로
고른 시나리오는 9개 조합). 판정(설계 2.2): 형평 두 값이 지자체안 이상이고 탑승(도보 반영)이 더 많으면 우리 안 승.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))

from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen import strike as K  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, residual_rows  # noqa: E402
from busstrike.sim import io as sio  # noqa: E402
from busstrike.sim.arrivals import build_run_inputs, realize_bins, write_demand_bins  # noqa: E402
from busstrike.sim.models import DemandFlow  # noqa: E402

# 저장소 밖 작업 폴더(데이터 위치, docs/design/routegen-data.md). 환경 변수 ROUTEGEN_WORK로 바꾼다
W = Path(os.environ.get("ROUTEGEN_WORK", "/mnt/c/Users/ddonggojin/orca/projects/guro_exp_work"))
START_S = 7 * 3600
EXP_DATES = ("20261026", "20261027", "20261028", "20261029", "20261030")
FIXED = 11
AIS = [f"ai{i:02d}" for i in range(5, 11)]


def drop_before(bins, start_s: int = START_S) -> list:
    """운행 시작 전 시간대(bin_start < start_s) 수요를 뺀다. 모든 안에 같게 적용한다(분모에서 제외)."""
    return [b for b in bins if b.bin_start_s >= start_s]


def fleet_scenario(base: dict, fleet: int, start_s: int = START_S, tag: str = "") -> dict:
    """원래 시나리오의 차량 한 대를 본떠 `fleet`대로 만들고 가용 시작을 start_s 이후로 둔다(시뮬레이터 입력만 바꿈)."""
    s2 = json.loads(json.dumps(base))
    tmpl = s2["fleet"]["vehicles"][0]
    s2["fleet"]["vehicles"] = [dict(tmpl, vehicle_id=f"V{i:03d}",
                                    available_start_s=max(int(tmpl["available_start_s"]), start_s))
                               for i in range(1, fleet + 1)]
    s2["fleet"]["authorized_count"] = fleet
    if tag:
        s2["scenario_id"] = f"{base['scenario_id']}-{tag}-f{fleet}"
    return s2


def sid_of(rate: float, draw: int) -> str:
    return f"p{round(rate * 100):03d}_d{draw}"


def scenario_bundles(root: Path, cfg, strike, nets: dict[str, Path], fleets: list[int], out: Path,
                     dates=EXP_DATES) -> dict:
    """시나리오 수요 묶음(망별). 수요 = 파업 몫 → 시내버스 흡수 → 정밀 흡수 뒤 남은 셔틀 대상(생성기와 같음),
    실현 seed는 원래 묶음 것. 06~07시 수요는 뺀다(07:00 시작, 모든 안 같게). 차량은 원래 묶음 차량 한 대를 본떠
    대수만큼 만들고 가용 시작을 07:00으로. 돌려주는 값: 날짜별 승객 수(07~10시)."""
    rows, kst = K.strike_rows(root, strike, "strike")
    res, _ = Absorber(root, cfg.absorb_params()).absorb(rows, True, True)
    left = residual_rows(rows, res)
    pax = {}
    for d in dates:
        src = root / "bundles" / f"affected_{d}"
        tmpl_path = sorted(src.glob("scenario_f*.json"))[0]
        base = json.loads(tmpl_path.read_text(encoding="utf-8"))
        seed = json.loads((src / tmpl_path.name.replace("scenario_", "run_inputs_")).read_text(encoding="utf-8"))["seed"]
        sc = sio.scenario_from_dict(base)
        flows = [DemandFlow(r.kind, r.stop_id, r.station_stop_id, r.hour * 3600, f"{float(r.flow):.6f}")
                 for r in left if r.date == d]
        flows = [f for f in flows if float(f.flow) > 0]
        bins = drop_before(realize_bins(flows, sc.policy, sc.window, seed, 3600))
        pax[d] = sum(b.passenger_count for b in bins)
        for net, tt in nets.items():
            t = out / net / f"strike_{d}"
            t.mkdir(parents=True, exist_ok=True)
            write_demand_bins(bins, t / "demand_bins.csv")
            shutil.copyfile(tt, t / "travel_times.json")
            for f in fleets:
                s2 = fleet_scenario(base, f, START_S, out.name)
                sio.write_bytes(t / f"scenario_f{f}.json", sio.json_bytes(s2))
                build_run_inputs(f"{s2['scenario_id']}-{net}", t / f"scenario_f{f}.json", t / "demand_bins.csv",
                                 t / "travel_times.json", seed, t / f"run_inputs_f{f}.json")
    return {"passengers_07_10": pax, "strike_stats": kst}


def run_scenario(a, rate: float, draw: int) -> dict:
    sid = sid_of(rate, draw)
    o = a.out / sid
    done = o / "summary.json"
    if done.exists():
        return json.loads(done.read_text(encoding="utf-8"))
    o.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    root = W / "region_all" / "ref" / f"{a.gu}_0610_v4"
    cfg = gu_config.load(REPO / "experiments" / "routegen_lab" / "gu_params.json", a.gu)
    strike = K.draw_strike(root, rate, draw)
    prop = max(1, round(FIXED * rate))
    fleets = sorted({FIXED, prop})
    sweep = W / "review1119" / "stage1c" / "sweep" / a.gu
    muni = W / "review1119" / "stage2" / "muni" / "muni07"
    # 1. 우리 안(실험 주간으로 생성)
    gen = o / "gen"
    if not (gen / "walk_eval.json").exists():
        cmd = [sys.executable, str(REPO / "experiments" / "routegen_lab" / "walk_eval.py"), "--sweep", str(sweep),
               "--region", str(W / "region_all" / "ref"), "--osm", str(W / "osm" / f"{a.gu}_roads.json"), "--gu", a.gu,
               "--multi", "--muni", f"muni07={muni}", "--loop", "demand", "--service-start", "07:00",
               "--fleets", ",".join(map(str, fleets)), "--facilities", str(W / "routegen_v2" / "facilities" /
                                                                            "facilities_seoul.csv"),
               "--strike-rate", str(rate), "--strike-draw", str(draw), "--no-run", "--gen-jobs", str(a.gen_jobs),
               "--only", "muni07,eqmc07", "--out", str(gen)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        (o / "gen.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        if r.returncode != 0:
            return {"sid": sid, "error": "generation failed"}
    we = json.loads((gen / "walk_eval.json").read_text(encoding="utf-8"))
    # 2. 시나리오 묶음(실험 주간, 07:00, 06~07시 수요 제외)
    nets = {"road": sweep / "net" / "road.json", "muni07": muni / "road_muni.json"}
    nets.update({x: a.ai_root / x / "road_muni.json" for x in AIS})
    bundles = o / "bundles"
    binfo = scenario_bundles(root, cfg, strike, nets, fleets, bundles)
    # 3. 평가(eval_rules)
    plans = []
    for f in fleets:
        pp = gen / "plans" / f"eqmc07-f{f}.json"
        if pp.exists():
            plans.append({"tag": f"eqmc07-f{f}", "label": "eqmc07", "fleet": f, "plan": str(pp),
                          "bundles": {"exp": str(bundles / "road")}})
    plans.append({"tag": "muni07-f11", "label": "muni07", "fleet": FIXED, "plan": str(muni / "plan.json"),
                  "bundles": {"exp": str(bundles / "muni07")}})
    for x in AIS:
        plans.append({"tag": f"{x}-f11", "label": x, "fleet": FIXED, "plan": str(a.ai_root / x / "plan.json"),
                      "bundles": {"exp": str(bundles / x)}})
    combos = None if sid in a.all_combos else [{"station_mode": "same_line", "walk_radius_m": 400}]
    spec = {"gu": a.gu, "root": str(root), "test_root": str(W / "region_all" / "ref" / f"{a.gu}_0610_v4_test"),
            "nb": str(W / "review1119" / "stage2" / "eval" / "nb400.json"),
            "nb_far": str(W / "review1119" / "stage3" / "equity" / "nb800_home.json"),
            "osm": str(W / "osm" / f"{a.gu}_roads.json"), "combos": combos, "renumber": False, "equity": True,
            "keep_events": "", "plans": plans,
            "strike": {"rate": rate, "draw": draw, "seed": strike.seed}, "equity_start": "07:00",
            "_설명": f"파업 비율 평가 {sid}: 실험 주간, 07:00, 06~07시 수요 제외(모든 안), 우리 안만 시나리오마다 다시 생성"}
    (o / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "experiments" / "routegen_lab" / "eval_rules.py"), "--spec",
                        str(o / "spec.json"), "--out", str(o / "eval"), "--groups", str(a.groups), "--jobs", str(a.jobs)],
                       capture_output=True, text=True)
    (o / "eval.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    shutil.rmtree(bundles, ignore_errors=True)
    for big in (o / "eval" / "runs").rglob("passenger_outcomes.csv"):        # 지표를 뽑은 뒤(results.json) 지운다
        big.unlink()
    for big in (o / "eval" / "runs").rglob("events.jsonl"):
        big.unlink()
    res = json.loads((o / "eval" / "results.json").read_text(encoding="utf-8"))["runs"] \
        if (o / "eval" / "results.json").exists() else []
    plan_rec = we["plans"]
    summ = {"sid": sid, "rate": rate, "draw": draw, "strike": strike.record(), "fleets": fleets,
            "bundles": binfo, "rules": we.get("rules"), "must": we.get("must"),
            "ours_alloc": {k: {kk: v.get(kk) for kk in ("approx", "alloc", "must_effect")}
                           for k, v in plan_rec.items() if k.startswith("eqmc07")},
            "results": res, "eval_rc": r.returncode, "seconds": round(time.time() - t0, 1)}
    done.write_text(json.dumps(summ, ensure_ascii=False), encoding="utf-8")
    return summ


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gu", default="guro")
    ap.add_argument("--rates", default="0.1,0.25,0.5,0.75,1.0")
    ap.add_argument("--draws", type=int, default=5)
    ap.add_argument("--only", default="")
    ap.add_argument("--all-combos", default="p100_d0,p050_d0", help="9개 조합으로 평가할 시나리오(나머지는 기본 조합)")
    ap.add_argument("--ai-root", type=Path, default=W / "routegen_v2" / "strike_curve" / "ai")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--groups", type=int, default=4)
    ap.add_argument("--gen-jobs", type=int, default=2)
    a = ap.parse_args()
    a.out = a.out.resolve()
    a.all_combos = set(filter(None, a.all_combos.split(",")))
    rates = [float(x) for x in a.rates.split(",")]
    jobs = [(r, d) for d in range(a.draws) for r in rates if not (r >= 1.0 and d > 0)]
    only = set(filter(None, a.only.split(",")))
    for r, d in jobs:
        if only and sid_of(r, d) not in only:
            continue
        s = run_scenario(a, r, d)
        print(sid_of(r, d), s.get("error", "ok"), "실행", len(s.get("results", [])), s.get("seconds"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
