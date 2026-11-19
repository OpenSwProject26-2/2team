"""모든 구: 옛 망(node)·새 망(edge)을 `uturn_check.py`로 재고, 새 망 파일을 구별로 저장한 뒤 한 표로 모은다. 시뮬레이션은 하지 않는다.

    python3 experiments/routegen_lab/build_nets_all.py --work <guro_exp_work> --out <work>/review1119/stage1b \
        [--parallel 3] [--only guro,mapo] [--summary-only]

- 구 목록·묶음 찾기는 `run_all_gus.py`와 같다: `<work>/region_all/*_0610_v4`의 구, 묶음은
  `<work>/region_all/ref/<구>_0610_v4`가 있으면 그것(구로·강서·동작·강동), 없으면 `<work>/region_all/<구>_0610_v4`.
- OSM: `<work>/osm/<구>_roads.json`(없으면 그 구는 건너뛰고 기록. 이 스크립트는 OSM을 만들지 않는다).
- 옛 망: `uturn_check.py --snap-mode node`, `<work>/hub_sweep2/<구>/net`이 있으면 `--net`으로 주어 재현 여부를 기록한다.
- 새 망: `uturn_check.py --snap-mode edge --compare <옛 망 결과> --write-net <out>/nets/<구>`.
- 결과: `<out>/nets/<구>/`(road.json, turns.json, roadnet_stats.json, snap.json), `<out>/uturn/<구>/{old_node,new_edge}.json`,
  `<out>/logs/<구>.{node,edge}.log`, 요약 `<out>/nets_summary.json`·`<out>/nets_summary.md`.
- 다시 실행하면 결과 json이 있는 단계는 건너뛴다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def region_of(work: Path, gu: str) -> Path:
    ref = work / "region_all" / "ref"
    return ref if (ref / f"{gu}_0610_v4").is_dir() else work / "region_all"


def run(cmd: list[str], logfile: Path) -> int:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(logfile, "w", encoding="utf-8") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=HERE.parents[1]).returncode
    print(f"{time.strftime('%H:%M:%S')} {logfile.name} rc={rc} {time.time() - t0:.0f}s", flush=True)
    return rc


def one(work: Path, out: Path, gu: str) -> dict:
    py = sys.executable
    root = region_of(work, gu) / f"{gu}_0610_v4"
    osm = work / "osm" / f"{gu}_roads.json"
    if not osm.exists():
        return {"gu": gu, "error": f"OSM 없음: {osm}"}
    od = out / "uturn" / gu
    old, new = od / "old_node.json", od / "new_edge.json"
    common = ["--root", str(root), "--osm", str(osm), "--gu", gu]
    if not old.exists():
        cmd = [py, str(HERE / "uturn_check.py"), *common, "--snap-mode", "node", "--out", str(old)]
        if (work / "hub_sweep2" / gu / "net" / "turns.json").exists():
            cmd += ["--net", str(work / "hub_sweep2" / gu / "net")]
        if run(cmd, out / "logs" / f"{gu}.node.log"):
            return {"gu": gu, "error": "node 측정 실패"}
    if not new.exists():
        cmd = [py, str(HERE / "uturn_check.py"), *common, "--snap-mode", "edge", "--compare", str(old),
               "--write-net", str(out / "nets" / gu), "--out", str(new)]
        if run(cmd, out / "logs" / f"{gu}.edge.log"):
            return {"gu": gu, "error": "edge 측정 실패"}
    return {"gu": gu, "root": str(root)}


def pick(r: dict) -> dict:
    """측정 결과 json에서 표에 쓰는 값만."""
    g, sg, sn, w = r["gtfs"], r["segments"], r["snap"], r["walk"]
    ub = g["uturn_by_angle"]
    ur = sg.get("unmatched_by_reason") or {}
    return {"stops": sn["stops"], "unsnapped": sn["unsnapped_n"], "shared_node_stops": sn["shared_node_stops"],
            "unsnapped_home_demand": sn.get("unsnapped_home_demand"), "home_demand_total": sn.get("home_demand_total"),
            "bus_segments": sg["bus_segments"], "bus_matched": sg["bus_matched"],
            "bus_long_match": sg.get("bus_long_match") or 0, "bus_uturn_match": sg.get("bus_uturn_match") or 0,
            "bus_no_path": ur.get("no_path", 0) if sg.get("unmatched_by_reason") is not None else sg["bus_unmatched"],
            "bus_unmatched_other": {k: v for k, v in ur.items() if k != "no_path"},
            "bus_unmatched": sg["bus_unmatched"],
            "net_segments": sg["net_segments"], "added": sg["added"], "added_share": sg["added_share"],
            "triples": g["triples"], "judged": g["judged"], "uturn": g["uturn"], "uturn_rate": g["uturn_rate"],
            "uturn_lt45": ub.get("<45", 0), "uturn_45_135": ub.get("45-135", 0), "uturn_ge135": ub.get(">=135", 0),
            "judged_ge135": g["judged_by_angle"].get(">=135", 0),
            "uturn_reason": g.get("uturn_reason"), "unjudged": g["unjudged"], "unjudged_why": g["unjudged_why"],
            "walk_pairs": w["pairs"], "rebuild_equals_net": r.get("rebuild_equals_net"), "code_commit": r.get("code_commit"),
            "median_bus_speed_kmh": r["roadnet"].get("median_bus_speed_kmh")}


def summarize(out: Path, gus: list[str], errors: dict) -> None:
    rows = {}
    for gu in gus:
        od = out / "uturn" / gu
        if not (od / "new_edge.json").exists():
            continue
        o = json.loads((od / "old_node.json").read_text(encoding="utf-8"))
        n = json.loads((od / "new_edge.json").read_text(encoding="utf-8"))
        rows[gu] = {"old": pick(o), "new": pick(n), "compare_cross": n.get("compare", {}).get("cross"),
                    "net_bytes": sum(p.stat().st_size for p in (out / "nets" / gu).iterdir())}
    (out / "nets_summary.json").write_text(json.dumps({"gus": rows, "errors": errors}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    hdr = ["구", "정류장", "안 붙음 옛→새", "같은 노드 옛→새", "버스 구간", "매칭 옛→새", "긴 경로", "도중 U턴", "끝까지 없음",
           "추가 구간 옛→새(비율)", "3정류장", "판정 가능 옛→새", "U턴 옛→새", "새 U턴 <45/45~135/≥135", "옛 U턴 <45/45~135/≥135",
           "판정 불가 옛→새", "도보 400m 쌍 옛→새"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    tot = {}
    for gu, r in rows.items():
        o, n = r["old"], r["new"]
        for k in ("stops", "bus_segments", "triples", "bus_long_match", "bus_uturn_match", "bus_no_path"):
            tot[k] = tot.get(k, 0) + n[k]
        for side in ("old", "new"):
            for k in ("unsnapped", "shared_node_stops", "bus_matched", "added", "net_segments", "judged", "uturn",
                      "uturn_lt45", "uturn_45_135", "uturn_ge135", "unjudged", "walk_pairs"):
                tot[f"{side}_{k}"] = tot.get(f"{side}_{k}", 0) + r[side][k]
        lines.append("| " + " | ".join(str(x) for x in [
            gu, n["stops"], f"{o['unsnapped']}→{n['unsnapped']}", f"{o['shared_node_stops']}→{n['shared_node_stops']}",
            n["bus_segments"], f"{o['bus_matched']}→{n['bus_matched']}", n["bus_long_match"], n["bus_uturn_match"],
            n["bus_no_path"], f"{o['added']}→{n['added']} ({100 * n['added_share']:.1f}%)", n["triples"],
            f"{o['judged']}→{n['judged']}", f"{o['uturn']}→{n['uturn']} ({100 * n['uturn_rate']:.1f}%)",
            f"{n['uturn_lt45']}/{n['uturn_45_135']}/{n['uturn_ge135']}",
            f"{o['uturn_lt45']}/{o['uturn_45_135']}/{o['uturn_ge135']}",
            f"{o['unjudged']}→{n['unjudged']}", f"{o['walk_pairs']}→{n['walk_pairs']}"]) + " |")
    if rows:
        t = tot
        lines.append("| " + " | ".join(str(x) for x in [
            "**합계**", t["stops"], f"{t['old_unsnapped']}→{t['new_unsnapped']}",
            f"{t['old_shared_node_stops']}→{t['new_shared_node_stops']}", t["bus_segments"],
            f"{t['old_bus_matched']}→{t['new_bus_matched']}", t["bus_long_match"], t["bus_uturn_match"], t["bus_no_path"],
            f"{t['old_added']}→{t['new_added']} ({100 * t['new_added'] / max(1, t['new_net_segments']):.1f}%)", t["triples"],
            f"{t['old_judged']}→{t['new_judged']}",
            f"{t['old_uturn']}→{t['new_uturn']} ({100 * t['new_uturn'] / max(1, t['new_judged']):.1f}%)",
            f"{t['new_uturn_lt45']}/{t['new_uturn_45_135']}/{t['new_uturn_ge135']}",
            f"{t['old_uturn_lt45']}/{t['old_uturn_45_135']}/{t['old_uturn_ge135']}",
            f"{t['old_unjudged']}→{t['new_unjudged']}", f"{t['old_walk_pairs']}→{t['new_walk_pairs']}"]) + " |")
    if errors:
        lines += ["", "실패: " + ", ".join(f"{k}({v})" for k, v in errors.items())]
    (out / "nets_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()
    work, out = args.work.resolve(), args.out.resolve()
    gus = sorted({p.name.removesuffix("_0610_v4") for p in (work / "region_all").glob("*_0610_v4")})
    if args.only:
        gus = [g for g in gus if g in args.only.split(",")]
    errors = {}
    if not args.summary_only:
        with ThreadPoolExecutor(args.parallel) as ex:
            for r in ex.map(lambda g: one(work, out, g), gus):
                if "error" in r:
                    errors[r["gu"]] = r["error"]
    summarize(out, gus, errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
