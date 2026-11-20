"""모든 구를 순서대로: 생성 설정 실험(hub_sweep) → 지자체안 평가(eval_municipal) → 실험실 레이어(build_lab_data).

    python3 experiments/routegen_lab/run_all_gus.py --work <guro_exp_work> [--parallel 2] [--jobs 3] [--only a,b]

- 다시 실행해도 된다. 끝난 단계는 건너뛴다(hub_sweep.json의 설정이 다 있으면 실험 끝, muni/muni.json이 있으면 평가 끝,
  layers/meta.json이 hub_sweep.json보다 새것이고 지자체안 평가 뒤면 실험실 끝). 실험이 덜 된 구 폴더는 지우고 새로 돈다.
- 구 묶음: `<work>/region_all/ref/<구>_0610_v4`(원자료 기준 묶음 복사본)가 있으면 그것, 없으면 `<work>/region_all/<구>_0610_v4`.
- OSM: `<work>/osm/<구>_roads.json`, 없으면 `<work>/osm/seoul_roads.json`에서 잘라 만든다.
- 지자체안: `<work>/municipal/<구>/plan.json`(있을 때).
- 실험실: `<work>/lab_all/<구>` → `<work>/hub_sweep2/<구>` 링크.
- 진행 기록: `<work>/hub_sweep2/run_all.log`(구·단계별 시작·끝·rc).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIGS = {"base", "hub135", "hub135_s12"}


def log(work: Path, msg: str) -> None:
    line = f"{time.strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(work / "hub_sweep2" / "run_all.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(work: Path, gu: str, step: str, cmd: list[str], logfile: Path) -> int:
    log(work, f"{gu} {step} start")
    with open(logfile, "w", encoding="utf-8") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=HERE.parents[1]).returncode
    log(work, f"{gu} {step} end rc={rc}")
    return rc


def one(work: Path, gu: str, jobs: int) -> None:
    py = sys.executable
    region = work / "region_all" / "ref" if (work / "region_all" / "ref" / f"{gu}_0610_v4").is_dir() else work / "region_all"
    out = work / "hub_sweep2" / gu
    osm = work / "osm" / f"{gu}_roads.json"
    if not osm.exists():
        if sh(work, gu, "slice_osm", [py, str(HERE / "slice_osm.py"), "--src", str(work / "osm" / "seoul_roads.json"),
                                      "--root", str(region / f"{gu}_0610_v4"), "--out", str(osm)], work / "hub_sweep2" / f"{gu}.osm.log"):
            return
    hs = out / "hub_sweep.json"
    done = hs.exists() and CONFIGS <= set(json.loads(hs.read_text(encoding="utf-8"))["configs"])
    if not done:
        if out.exists():
            shutil.rmtree(out)
        if sh(work, gu, "hub_sweep", [py, str(HERE / "hub_sweep.py"), "--region", str(region), "--osm-dir", str(work / "osm"),
                                      "--gu", gu, "--out", str(out), "--jobs", str(jobs)], work / "hub_sweep2" / f"{gu}.log"):
            return
    plan = work / "municipal" / gu / "plan.json"
    if plan.exists() and not (out / "muni" / "muni.json").exists():
        sh(work, gu, "muni", [py, str(HERE / "eval_municipal.py"), "--sweep", str(out), "--plan", str(plan), "--jobs", str(jobs)],
           work / "hub_sweep2" / f"{gu}.muni.log")
    meta = out / "layers" / "meta.json"
    newest = max(p.stat().st_mtime for p in [hs, out / "muni" / "muni.json"] if p.exists())
    if not meta.exists() or meta.stat().st_mtime < newest:
        rc = sh(work, gu, "lab", [py, str(HERE / "build_lab_data.py"), "--region", str(region), "--osm", str(osm), "--gu", gu,
                                  "--sweep", str(out), "--boundaries", str(work / "osm" / "seoul_boundaries.json")],
                work / "hub_sweep2" / f"{gu}.lab.log")
        if rc == 0:
            link = work / "lab_all" / gu
            if link.is_symlink() or link.exists():
                link.unlink() if link.is_symlink() else shutil.rmtree(link)
            link.symlink_to(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    work = args.work.resolve()
    (work / "hub_sweep2").mkdir(parents=True, exist_ok=True)
    gus = sorted({p.name.removesuffix("_0610_v4") for p in (work / "region_all").glob("*_0610_v4")})
    if args.only:
        gus = [g for g in gus if g in args.only.split(",")]
    log(work, f"run_all 시작: {len(gus)}개 구, 동시 {args.parallel}")
    with ThreadPoolExecutor(args.parallel) as ex:
        list(ex.map(lambda g: one(work, g, args.jobs), gus))
    log(work, "run_all 끝")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
