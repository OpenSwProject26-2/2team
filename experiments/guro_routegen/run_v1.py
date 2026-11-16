"""routegen v1 실행 범위 전체: 후보 생성·배분(조건별) → 5일 묶음 시뮬레이션.

    python3 experiments/guro_routegen/run_v1.py --root <guro_0610 폴더> --out <저장소 밖>/routegen_v1 \
        [--jobs 6] [--data-label guro_0610.zip] [--skip-sims]

조건
- base: v0 기본값(N_max 8, R 3km, τ 0.2 …) + v1 국소 탐색. 4변형 × 11·13대 × {우리 안, 피더안}
- sens/N{8,12,none}_R{1500,2000,3000}: affected 변형만, 11·13대 × {우리 안, 피더안}. τ 0.2·군집 상한 3은 그대로.
  N8_R3000은 base의 affected와 설정이 같다(같은 plan이 나와야 한다 — 보고서에서 바이트 비교).

맨 앞에서 실험 묶음인지 확인한다(망 ID, README "실험 데이터 10/26~30", 묶음 날짜). 다르면 멈춘다.
이 작업 폴더의 src/와 experiments/guro_exp/run_matrix.py만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
from busstrike.planning.routegen.data import check_root  # noqa: E402

N_MAX = ("8", "12", "none")
RADII = (1500, 2000, 3000)


def conditions() -> list[dict]:
    out = [{"name": "base", "dir": "base", "args": []}]
    for n in N_MAX:
        for r in RADII:
            out.append({"name": f"N{n}_R{r}", "dir": f"sens/N{n}_R{r}", "n_max": n, "radius_m": r,
                        "args": ["--variants", "affected", "--max-stops", n, "--radius-m", str(r)]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--data-label", default=None)
    ap.add_argument("--skip-sims", action="store_true")
    ap.add_argument("--only", default=None, help="쉼표로 고른 조건 이름만")
    args = ap.parse_args()

    info = check_root(args.root)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    conds = conditions()
    if args.only:
        keep = set(args.only.split(","))
        conds = [c for c in conds if c["name"] in keep]
    log = {"root_check": info, "conditions": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for c in conds:
        d = out / c["dir"]
        t0 = time.time()
        cmd = [sys.executable, "-m", "busstrike.planning.routegen", "--root", str(args.root.resolve()),
               "--out", str(d), "--condition", c["name"], *c["args"]]
        gen = subprocess.run(cmd, env=env, cwd=REPO, check=False)
        rec = {**{k: v for k, v in c.items() if k != "args"}, "args": c["args"], "generate_rc": gen.returncode,
               "generate_seconds": round(time.time() - t0, 1)}
        if gen.returncode != 0:
            log["conditions"].append(rec)
            print(f"{c['name']}: 생성 실패 {gen.returncode}", file=sys.stderr)
            break
        if not args.skip_sims:
            t1 = time.time()
            cmd = [sys.executable, str(HERE.parent / "run_sims.py"), "--routegen-out", str(d),
                   "--root", str(args.root.resolve()), "--jobs", str(args.jobs)]
            if args.data_label:
                cmd += ["--data-label", args.data_label]
            sim = subprocess.run(cmd, cwd=REPO, check=False)
            rec |= {"sims_rc": sim.returncode, "sims_seconds": round(time.time() - t1, 1)}
        log["conditions"].append(rec)
        print(f"== {c['name']} 끝 ({time.time() - t0:.0f}s)", flush=True)
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    name = "run_v1.json" if not args.only else f"run_v1_{args.only.replace(',', '_')}.json"
    (out / name).write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = all(r.get("generate_rc") == 0 and r.get("sims_rc", 0) == 0 for r in log["conditions"])
    return 0 if ok and len(log["conditions"]) == len(conds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
