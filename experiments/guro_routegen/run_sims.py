"""routegen이 만든 비교 입력 plan.json을 해당 변형·차량 조건의 5일 묶음으로 돌린다.

    python3 experiments/guro_routegen/run_sims.py --routegen-out <routegen --out 폴더> \
        --bundles <guro_0610>/bundles --run-matrix <feat/guro-exp-1026-30의 experiments/guro_exp/run_matrix.py> \
        [--src <이 작업 폴더>/src] [--jobs 6] [--data-label guro_0610_2.zip]

실행은 `run_matrix.py`(변환기 S1–S6 → M05 → M07 `--max-recompute 200000`)에 맡긴다. 그 스크립트는
`--bundles` 아래 묶음 전부에 모든 안을 돌리므로, 변형마다 그 변형 묶음 5개만 든 폴더(심볼릭 링크)를 만들어 넘긴다.
결과: `<routegen-out>/runs/<변형>/runs.json`과 실행별 폴더, `<routegen-out>/runs/run_sims.json`(실행 기록).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--routegen-out", required=True, type=Path)
    ap.add_argument("--bundles", required=True, type=Path)
    ap.add_argument("--run-matrix", required=True, type=Path)
    ap.add_argument("--src", type=Path, default=HERE.parents[2] / "src")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--data-label", default=None)
    args = ap.parse_args()

    rg = args.routegen_out.resolve()
    manifest = json.loads((rg / "manifest.json").read_text(encoding="utf-8"))
    bundles = args.bundles.resolve()
    readme = bundles.parent / "README.md"
    by_variant: dict[str, list[dict]] = {}
    for p in manifest["plans"]:
        by_variant.setdefault(p["variant"], []).append(p)

    log = {"run_matrix": str(args.run_matrix.resolve()), "run_matrix_sha256": sha256(args.run_matrix),
           "run_matrix_repo_commit": git(args.run_matrix.resolve().parent, "rev-parse", "HEAD"),
           "run_matrix_repo_branch": git(args.run_matrix.resolve().parent, "branch", "--show-current"),
           "src": str(args.src.resolve()), "src_commit": git(args.src.resolve(), "rev-parse", "HEAD"),
           "src_tracked_changes": bool(git(args.src.resolve(), "status", "--porcelain", "--untracked-files=no",
                                           "--", "src/busstrike/sim", "src/busstrike/evaluation")),
           "variants": {}}
    for variant in sorted(by_variant):
        view = rg / "views" / variant
        (view / "bundles").mkdir(parents=True, exist_ok=True)
        if readme.exists() and not (view / "README.md").exists():
            (view / "README.md").symlink_to(readme)
        dates = []
        for b in sorted(bundles.iterdir()):
            if b.is_dir() and b.name.rsplit("_", 1)[0] == variant:
                link = view / "bundles" / b.name
                if not link.exists():
                    link.symlink_to(b, target_is_directory=True)
                dates.append(b.name)
        if len(dates) != 5:
            sys.exit(f"{variant}: 묶음이 5개가 아니다: {dates}")
        cmd = [sys.executable, str(args.run_matrix), "--bundles", str(view / "bundles"),
               "--out", str(rg / "runs" / variant), "--src", str(args.src.resolve()), "--jobs", str(args.jobs)]
        if args.data_label:
            cmd += ["--data-label", args.data_label]
        for p in sorted(by_variant[variant], key=lambda x: (x["label"], x["fleet"])):
            cmd += ["--plan", f"{p['label']}:{p['fleet']}={p['file']}"]
        t0 = time.time()
        done = subprocess.run(cmd, check=False)
        log["variants"][variant] = {"bundles": dates, "returncode": done.returncode,
                                    "seconds": round(time.time() - t0, 1)}
        if done.returncode != 0:
            print(f"{variant}: run_matrix 종료 코드 {done.returncode}", file=sys.stderr)
    (rg / "runs" / "run_sims.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(v["returncode"] == 0 for v in log["variants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
