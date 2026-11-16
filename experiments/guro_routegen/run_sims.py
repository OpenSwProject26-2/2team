"""routegen이 만든 비교 입력 plan.json을 해당 변형·차량 조건의 5일 묶음으로 돌린다.

    python3 experiments/guro_routegen/run_sims.py --routegen-out <routegen --out 폴더> \
        --root <guro_0610 폴더> [--jobs 6] [--data-label guro_0610.zip]

맨 앞에서 실험 묶음인지 확인한다(`routegen.data.check_root`: 망 ID guro-buf2km-0610-v2, README
"실험 데이터 10/26~30", 묶음 날짜). 다르면 멈춘다.

실행은 **이 작업 폴더의** `experiments/guro_exp/run_matrix.py`(변환기 S1–S6 → M05 → M07 `--max-recompute 200000`)에
맡기고, 패키지도 이 작업 폴더의 `src/`만 쓴다(다른 체크아웃의 파일을 부르지 않는다). 그 스크립트는
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
REPO = HERE.parents[2]
RUN_MATRIX = REPO / "experiments" / "guro_exp" / "run_matrix.py"
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
from busstrike.planning.routegen.data import check_root  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--routegen-out", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path, help="실험 묶음 폴더(README.md, network/, bundles/)")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--data-label", default=None)
    args = ap.parse_args()

    root_check = check_root(args.root)
    rg = args.routegen_out.resolve()
    manifest = json.loads((rg / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest["root"]).resolve() != args.root.resolve():
        sys.exit(f"manifest의 묶음 {manifest['root']} != --root {args.root}")
    bundles = args.root.resolve() / "bundles"
    readme = args.root.resolve() / "README.md"
    by_variant: dict[str, list[dict]] = {}
    for p in manifest["plans"]:
        by_variant.setdefault(p["variant"], []).append(p)

    log = {"root_check": root_check, "run_matrix": str(RUN_MATRIX), "run_matrix_sha256": sha256(RUN_MATRIX),
           "run_matrix_tracked": git(REPO, "ls-files", "--error-unmatch", str(RUN_MATRIX)) != "",
           "run_matrix_changed": bool(git(REPO, "status", "--porcelain", "--", str(RUN_MATRIX))),
           "repo_commit": git(REPO, "rev-parse", "HEAD"), "repo_branch": git(REPO, "branch", "--show-current"),
           "src": str(SRC), "src_tracked_changes": bool(git(REPO, "status", "--porcelain", "--untracked-files=no",
                                                             "--", "src/busstrike/sim", "src/busstrike/evaluation")),
           "variants": {}}
    if log["src_tracked_changes"] or log["run_matrix_changed"] or not log["run_matrix_tracked"]:
        sys.exit("시뮬레이터·검증기 또는 run_matrix.py가 커밋과 다르다 — 멈춘다")
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
        cmd = [sys.executable, str(RUN_MATRIX), "--bundles", str(view / "bundles"),
               "--out", str(rg / "runs" / variant), "--src", str(SRC), "--jobs", str(args.jobs)]
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
