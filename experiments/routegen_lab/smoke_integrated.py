"""통합 생성기 스모크(통합 작업 4절): 구로·강서·강동, 10/28 하루, 11대, 07:00 시작, 전면 파업.

    python3 experiments/routegen_lab/smoke_integrated.py --out <저장소 밖> [--gus guro,gangseo,gangdong]

성능 주장 없음(하루, 실험 주간). 구마다
1. 입력 폴더(walk_eval --sweep 형식): 버스 통행 가능 도로망 review1119/stage1c/nets/<구>(road.json·turns.json),
   시나리오 정책·운행 창은 원래 묶음 것. 대수 11은 입력(1월 구로 실운행)이라 모든 구 같게.
2. 생성·배분: walk_eval(순환 demand, 여러 역 경유, 07:00, 필수 시설 CSV, 상대 기준 규칙, 설정값 정책). 형평 하한은
   지자체안이 있는 구(구로 muni07)만 지자체안 값, 없는 구는 끔(기록).
3. 평가: strike_eval.scenario_bundles(10/28, 정밀 흡수 뒤, 06~07시 제외, 차량 07:00) → eval_rules(same_line-w400).
결과: <out>/<구>/..., <out>/smoke_table.json, 표를 출력한다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen import strike as K  # noqa: E402
from busstrike.sim import io as sio  # noqa: E402
from strike_eval import START_S, W, fleet_scenario, scenario_bundles  # noqa: E402

DATE = "20261028"
FLEET = 11
MUNI = {"guro": W / "review1119" / "stage2" / "muni" / "muni07"}


def sweep_dir(gu: str, root: Path, out: Path) -> Path:
    sw = out / "sweep"
    net = W / "review1119" / "stage1c" / "nets" / gu
    (sw / "net").mkdir(parents=True, exist_ok=True)
    for n in ("road.json", "turns.json"):
        t = sw / "net" / n
        if not t.exists():
            t.write_bytes((net / n).read_bytes())
    b = sw / "exp" / "bundles" / f"affected_{DATE}"
    b.mkdir(parents=True, exist_ok=True)
    src = root / "bundles" / f"affected_{DATE}"
    base = json.loads(sorted(src.glob("scenario_f*.json"))[0].read_text(encoding="utf-8"))
    sio.write_bytes(b / f"scenario_f{FLEET}.json", sio.json_bytes(fleet_scenario(base, FLEET, START_S)))
    (sw / "hub_sweep.json").write_text(json.dumps({"gu": gu, "fleets": [FLEET], "absorbed": None, "affected_total": None,
                                                   "absorbed_test": None, "affected_total_test": None,
                                                   "plans": [], "_설명": "smoke_integrated 입력 폴더"},
                                                  ensure_ascii=False), encoding="utf-8")
    return sw


def one(gu: str, out: Path, gen_jobs: int) -> dict:
    o = out / gu
    root = W / "region_all" / "ref" / f"{gu}_0610_v4"
    sw = sweep_dir(gu, root, o)
    gen = o / "gen"
    muni = MUNI.get(gu)
    if not (gen / "walk_eval.json").exists():
        cmd = [sys.executable, str(HERE.parent / "walk_eval.py"), "--sweep", str(sw), "--region", str(W / "region_all" / "ref"),
               "--osm", str(W / "osm" / f"{gu}_roads.json"), "--gu", gu, "--multi", "--loop", "demand",
               "--service-start", "07:00", "--fleets", str(FLEET),
               "--facilities", str(W / "routegen_v2" / "facilities" / "facilities_seoul.csv"),
               "--no-run", "--gen-jobs", str(gen_jobs), "--out", str(gen)]
        cmd += (["--muni", f"muni07={muni}", "--only", "muni07,eqmc07"] if muni else
                ["--equity-floor", "none", "--only", "eqmc07"])
        r = subprocess.run(cmd, capture_output=True, text=True)
        (o / "gen.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        if r.returncode != 0:
            return {"gu": gu, "error": "generation failed", "log": str(o / "gen.log")}
    cfg = gu_config.load(HERE.parent / "gu_params.json", gu)
    nets = {"road": sw / "net" / "road.json", **({"muni07": muni / "road_muni.json"} if muni else {})}
    bundles = o / "bundles"
    binfo = scenario_bundles(root, cfg, K.full_strike(root), nets, [FLEET], bundles, dates=(DATE,))
    plans = [{"tag": f"eqmc07-f{FLEET}", "label": "eqmc07", "fleet": FLEET, "plan": str(gen / "plans" / f"eqmc07-f{FLEET}.json"),
              "bundles": {"exp": str(bundles / "road")}}]
    if muni:
        plans.append({"tag": f"muni07-f{FLEET}", "label": "muni07", "fleet": FLEET, "plan": str(muni / "plan.json"),
                      "bundles": {"exp": str(bundles / "muni07")}})
    spec = {"gu": gu, "root": str(root), "test_root": str(W / "region_all" / "ref" / f"{gu}_0610_v4_test"),
            "nb": None, "nb_far": None, "osm": str(W / "osm" / f"{gu}_roads.json"),
            "combos": [{"station_mode": "same_line", "walk_radius_m": 400}], "renumber": False, "equity": True,
            "keep_events": f"eqmc07-f{FLEET}/same_line-w400/exp/{DATE}", "plans": plans, "equity_start": "07:00",
            "_설명": "통합 스모크: 10/28 하루, 11대, 07:00, 06~07시 수요 제외, 전면 파업(정밀 흡수 뒤)"}
    if gu == "guro":
        spec["nb"] = str(W / "review1119" / "stage2" / "eval" / "nb400.json")
        spec["nb_far"] = str(W / "review1119" / "stage3" / "equity" / "nb800_home.json")
    (o / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE.parent / "eval_rules.py"), "--spec", str(o / "spec.json"), "--out",
                        str(o / "eval"), "--groups", "2", "--jobs", "1"], capture_output=True, text=True)
    (o / "eval.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    res = json.loads((o / "eval" / "results.json").read_text(encoding="utf-8"))["runs"] \
        if (o / "eval" / "results.json").exists() else []
    we = json.loads((gen / "walk_eval.json").read_text(encoding="utf-8"))
    rows = []
    for p in plans:
        rep = json.loads((gen / "plans" / f"{p['tag']}.plan_report.json").read_text(encoding="utf-8")) \
            if p["label"] == "eqmc07" else None
        run = next((x for x in res if x["tag"] == p["tag"]), {})
        must = (rep or {}).get("must") or {}
        lc = (rep or {}).get("loop_check") or []
        rows.append({
            "gu": gu, "plan": p["label"], "fleet": FLEET, "routes": len(json.loads(Path(p["plan"]).read_text(
                encoding="utf-8"))["routes"]),
            "loops_ok": (sum(1 for x in lc if x["returns_with_stops"] and x["closure_single_leg"] and x["closure_ok"]),
                         len(lc)) if rep else None,
            "must_required": len(must.get("stops", {})), "must_covered": sum(1 for v in must.get("stops", {}).values()
                                                                             if v.get("covered")),
            "must_uncovered": must.get("must_stop_uncovered", []),
            "served": run.get("served"), "served_walk": run.get("served_walk"), "total": run.get("total_sim"),
            "equity": run.get("equity"), "m07": run.get("verify_pass"),
            "passengers_07_10": binfo["passengers_07_10"].get(DATE), "rules": (we.get("rules") or {}).get("params"),
            "equity_floor": we.get("equity_floor"), "must_stops": (we.get("must") or {}).get("must_stops")})
    import shutil
    shutil.rmtree(bundles, ignore_errors=True)
    return {"gu": gu, "rows": rows, "eval_rc": r.returncode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gus", default="guro,gangseo,gangdong")
    ap.add_argument("--gen-jobs", type=int, default=2)
    a = ap.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    table_p = out / "smoke_table.json"
    table = json.loads(table_p.read_text(encoding="utf-8")) if table_p.exists() else {}
    for gu in a.gus.split(","):
        table[gu] = one(gu, out, a.gen_jobs)
        table_p.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        print(gu, json.dumps(table[gu], ensure_ascii=False)[:600], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
