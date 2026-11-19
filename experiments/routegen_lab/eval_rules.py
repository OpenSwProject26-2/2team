"""평가 규칙 조합(목적 역 가정 × 걷기 반경)마다 안을 시뮬레이션한다(설계 7절, 2.3의 9개 조합).

    python3 experiments/routegen_lab/eval_rules.py --spec <spec.json> --out <저장소 밖 결과 폴더> [--groups 6] [--jobs 1]

조합마다 안의 수요 묶음(demand_bins)을 `reach.Reach.reassign`으로 재배정하고(행을 합치지 않고 aggregate_id를 그대로 둔다 →
승객 도착 시각이 모든 조합에서 같다), run_inputs를 시뮬레이터 함수로 다시 만들어 run_matrix(변환기 → M05 → M07)로 돌린다.
시뮬레이터·검증기는 그대로다. 도보 시간은 M07 결과에서 따로 센다(`reach.served_with_walk`, 파생 지표 served_walk).

spec.json
{
  "gu": "guro", "params": "<gu_params.json>",           # eval 절: 기본 규칙과 조합(station_modes × walk_radii_m)
  "root": "<region>/<구>_0610_v4", "test_root": "<region>/<구>_0610_v4_test",
  "lines": "<station_lines.csv>",                       # 비우면 이 폴더의 station_lines.csv
  "nb": "<도보 이웃 json>" | null, "osm": "<구_roads.json>",   # nb가 없으면 osm으로 새 망 도보 이웃(최대 반경)을 잰다
  "combos": [{"station_mode": ..., "walk_radius_m": ...}, ...] | null,   # 비우면 설정 파일 eval.combos
  "plans": [{"tag": "cov70r3md-f11", "label": "cov70r3md", "fleet": 11, "plan": "<plan.json>",
             "bundles": {"exp": "<묶음 폴더>", "test": "<묶음 폴더>"}, "start": "07:00" | null,
             "dates": ["20261028"] | null, "combos": ["same_line-w400"] | null}],
  "renumber": false,                                    # true면 참고 구현처럼 aggregate_id를 a0000001…로 다시 매김
  "keep_events": "cov70r3md-f11/same_line-w400/exp/20261028"   # events.jsonl·realization.jsonl을 남길 실행 하나
}
결과: `<out>/runs/<tag>/<조합>/<split>/`(run_matrix 결과 + group.json), `<out>/results.json`(실행마다 한 줄),
`<out>/meta.json`(코드 커밋·규칙·망·설정). 이미 끝난 묶음(group.json이 있고 모두 M07 통과)은 건너뛴다.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen.reach import EvalRule, Reach, StationIndex, served_with_walk  # noqa: E402
from busstrike.sim import io as sio  # noqa: E402
from busstrike.sim.arrivals import build_run_inputs  # noqa: E402
from busstrike.sim.traveltime import TravelTimeTable  # noqa: E402

RUN_MATRIX = REPO / "experiments" / "guro_exp" / "run_matrix.py"
HEAD = ["aggregate_id", "origin", "destination", "bin_start_s", "bin_width_s", "passenger_count"]
_lock = threading.Lock()


def git_state() -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "tracked_changes": bool(git("status", "--porcelain", "--untracked-files=no"))}


def hhmm_s(x: str) -> int:
    h, m = x.split(":")
    return int(h) * 3600 + int(m) * 60


def ride_fn(tt: TravelTimeTable, dwell_s: int = 10, t: int = 8 * 3600):
    """차내 시간(8시 구간 시간 + 정차 10초 × 정류장 수). 재배정 순서 ⑤에만 쓴다."""
    cache: dict = {}

    def ride(seq, i, j):
        k = (tuple(seq), i, j)
        if k not in cache:
            cache[k] = sum(tt.lookup(a, b, t) for a, b in zip(seq[i:j], seq[i + 1:j + 1])) + dwell_s * (j - i)
        return cache[k]
    return ride


def read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def make_bundle(src: Path, dst: Path, routes: list[list[str]], reach: Reach, fleet: int, ride=None,
                renumber: bool = False, start_s: int | None = None) -> dict:
    """src 묶음 하나를 재배정해 dst에 쓴다. 돌려주는 값: 재배정 요약 + 설계 규칙 대조 + 행별 도보 m."""
    rows = read_rows(src / "demand_bins.csv")
    new, per, stats = reach.reassign(rows, routes, ride)
    cover: set = set()
    for seq in routes:
        cover |= set(reach.cover_keys(seq))
    design = sum(int(r["passenger_count"]) for r in rows if (r["origin"], r["destination"]) in cover)
    if renumber:
        order = sorted(range(len(new)), key=lambda i: new[i]["aggregate_id"])
        ren = {}
        for n, i in enumerate(order, 1):
            ren[new[i]["aggregate_id"]] = f"a{n:07d}"
        new = [{**new[i], "aggregate_id": ren[new[i]["aggregate_id"]]} for i in order]
        per = {ren[k]: v for k, v in per.items()}
    dst.mkdir(parents=True, exist_ok=True)
    with open(dst / "demand_bins.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEAD)
        for r in sorted(new, key=lambda r: r["aggregate_id"]):
            w.writerow([r[k] for k in HEAD])
    shutil.copyfile(src / "travel_times.json", dst / "travel_times.json")
    sc = json.loads((src / f"scenario_f{fleet}.json").read_text(encoding="utf-8"))
    if start_s is not None:
        for v in sc["fleet"]["vehicles"]:
            v["available_start_s"] = max(int(v["available_start_s"]), start_s)
    sio.write_bytes(dst / f"scenario_f{fleet}.json", sio.json_bytes(sc))
    ri = json.loads((src / f"run_inputs_f{fleet}.json").read_text(encoding="utf-8"))
    build_run_inputs(ri["input_bundle_id"] + "-" + reach.rule.label() + (f"-s{start_s}" if start_s else ""),
                     dst / f"scenario_f{fleet}.json", dst / "demand_bins.csv", dst / "travel_times.json",
                     ri["seed"], dst / f"run_inputs_f{fleet}.json")
    return {**stats, "design_reachable": design, "design_minus_sim": design - stats["reachable"],
            "total": sum(int(r["passenger_count"]) for r in rows), "walk_m": {k: v[0] for k, v in per.items() if v[0]}}


def outcome_metrics(run_dir: Path, walk_m: dict, rule: EvalRule) -> dict:
    m = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    with open(run_dir / "passenger_outcomes.csv", encoding="utf-8", newline="") as f:
        sw = served_with_walk(csv.DictReader(f), walk_m, rule)
    ref = m["M2"].get("passengers_refused_by_outcome", {})
    return {"served": m["M1"]["served"], "total_sim": m["M1"]["total"], "unserved_by_reason": m["M2"]["unserved_by_reason"],
            "served_walk": sw["served_walk"], "dropped_by_walk_time": sw["dropped_by_walk_time"],
            "refused_unserved": ref.get("unserved", 0),
            # 파생 지표의 치우침 상한: 도보 시간으로 못 탄 승객이 차지한 자리 때문에 정원으로 못 탄 승객 수 이하
            "walk_bias_bound": min(sw["dropped_by_walk_time"], ref.get("unserved", 0))}


def run_group(g: dict, out: Path, reach: Reach, ride_of, jobs: int, renumber: bool, keep: str) -> list[dict]:
    p, rule, split = g["plan"], reach.rule, g["split"]
    gdir = out / "runs" / p["tag"] / rule.label() / split
    gj = gdir / "group.json"
    if gj.exists():
        old = json.loads(gj.read_text(encoding="utf-8"))
        if old and all(r.get("verify_pass") is True for r in old) and len(old) == len(g["bundles"]):
            return old
    routes = [r["stops"] for r in json.loads(Path(p["plan"]).read_text(encoding="utf-8"))["routes"]]
    bdir = out / "_bundles" / p["tag"] / rule.label() / split
    if bdir.exists():
        shutil.rmtree(bdir)
    start_s = hhmm_s(p["start"]) if p.get("start") else None
    info = {}
    for b in g["bundles"]:
        ride = ride_of(b / "travel_times.json")
        info[b.name] = make_bundle(b, bdir / b.name, routes, reach, p["fleet"], ride, renumber, start_s)
    if gdir.exists():
        shutil.rmtree(gdir)
    cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(bdir), "--out", str(gdir), "--src", str(REPO / "src"),
           "--jobs", str(jobs), "--plan", f"{p['label']}:{p['fleet']}={Path(p['plan']).resolve()}"]
    subprocess.run(cmd, check=False, capture_output=True, text=True)
    runs = json.loads((gdir / "runs.json").read_text(encoding="utf-8"))["runs"]
    recs = []
    for r in runs:
        st = info[r["bundle"]]
        rec = {"tag": p["tag"], "label": p["label"], "fleet": p["fleet"], "start": p.get("start"), "rule": rule.label(),
               "station_mode": rule.station_mode, "walk_radius_m": rule.walk_radius_m, "split": split, "date": r["date"],
               "verify_pass": r.get("verify_pass"), "stage_failed": r.get("stage_failed"),
               "commit": (r.get("engine_env") or {}).get("commit"),
               "reassign": {k: v for k, v in st.items() if k != "walk_m"}}
        run_dir = Path(r["dir"]) / "run"
        if (run_dir / "metrics.json").exists():
            rec.update(outcome_metrics(run_dir, st["walk_m"], rule))
        (gdir / f"walk_m_{r['date']}.json").write_text(json.dumps(st["walk_m"], separators=(",", ":")), encoding="utf-8")
        here = f"{p['tag']}/{rule.label()}/{split}/{r['date']}"
        if here != keep:
            for name in ("events.jsonl", "realization.jsonl"):
                (run_dir / name).unlink(missing_ok=True)
        recs.append(rec)
    gj.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(bdir, ignore_errors=True)
    return recs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--groups", type=int, default=6, help="동시에 돌릴 (안, 조합, 주간) 묶음 수")
    ap.add_argument("--jobs", type=int, default=1, help="묶음 하나 안에서 run_matrix --jobs")
    args = ap.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg = gu_config.load(spec.get("params") or HERE.parent / "gu_params.json", spec["gu"])
    base_rule = cfg.eval_rule()
    combos = [cfg.eval_rule(**c) for c in spec["combos"]] if spec.get("combos") else cfg.eval_combos()
    lines = spec.get("lines") or str(HERE.parent / "station_lines.csv")
    root, test_root = Path(spec["root"]), Path(spec["test_root"])
    ix = StationIndex.from_root(root, lines)
    ix.add_flows(D.load_flows(root))
    ix.add_flows(D.load_flows(test_root))
    if spec.get("nb"):
        nb = json.loads(Path(spec["nb"]).read_text(encoding="utf-8"))
        nb_src = spec["nb"]
    else:
        nbp = out / f"nb{int(max(c.walk_radius_m for c in combos))}.json"
        if not nbp.exists():
            from roadnet import RoadNet
            from walknet import walk_neighbors
            nbp.write_text(json.dumps(walk_neighbors(RoadNet(Path(spec["osm"]), root),
                                                     max(c.walk_radius_m for c in combos)), separators=(",", ":")),
                           encoding="utf-8")
        nb = json.loads(nbp.read_text(encoding="utf-8"))
        nb_src = str(nbp)
    # 모든 묶음의 OD 키(덮는 키 색인을 한 번만 만든다)
    groups = []
    keys: set = set()
    for p in spec["plans"]:
        for split, bd in p["bundles"].items():
            bs = [b for b in sorted(Path(bd).iterdir()) if b.is_dir()
                  and (not p.get("dates") or b.name.rsplit("_", 1)[1] in p["dates"])]
            for b in bs:
                keys |= {(r["origin"], r["destination"]) for r in read_rows(b / "demand_bins.csv")}
            for c in combos:
                if p.get("combos") and c.label() not in p["combos"]:
                    continue
                groups.append({"plan": p, "split": split, "bundles": bs, "rule": c})
    reaches = {c.label(): Reach(ix, c, nb, sorted(keys)) for c in combos}
    tts: dict = {}

    def ride_of(path: Path):
        k = path.read_bytes()
        h = hash(k)
        with _lock:
            if h not in tts:
                tts[h] = ride_fn(TravelTimeTable.from_dict(json.loads(k)))
        return tts[h]

    meta = {"code": git_state(), "spec": spec, "spec_path": str(args.spec), "nb": nb_src, "lines": lines,
            "base_rule": base_rule.record(), "rules": {c.label(): c.record() for c in combos},
            "params": cfg.record(), "missing_line_stations": [[k, ix.names.get(k, "")] for k in ix.missing_lines],
            "reassign_order": ["원래 (o, d) 그대로", "원래 역 먼저(대체 역보다)", "도보 거리(candidates: 동네 쪽)",
                               "차내 시간(8시 구간 + 정차 10초)", "정류장 ID"]}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    results: list[dict] = []
    keep = spec.get("keep_events", "")

    def one(g):
        recs = run_group(g, out, reaches[g["rule"].label()], ride_of, args.jobs, bool(spec.get("renumber")), keep)
        with _lock:
            results.extend(recs)
            bad = [r for r in recs if r.get("verify_pass") is not True]
            print(f"[{len(results)}] {g['plan']['tag']} {g['rule'].label()} {g['split']}: "
                  f"{[r.get('served') for r in recs]} walk {[r.get('served_walk') for r in recs]}"
                  f"{' FAIL ' + str(len(bad)) if bad else ''}", flush=True)
            (out / "results.json").write_text(json.dumps({"meta": {"code": meta["code"]}, "runs": results},
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=max(1, args.groups)) as ex:
        list(ex.map(one, groups))
    shutil.rmtree(out / "_bundles", ignore_errors=True)
    bad = [r for r in results if r.get("verify_pass") is not True]
    print(f"완료 {len(results)}건, M07 미통과 {len(bad)}", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
