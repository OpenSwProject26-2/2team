"""비교 운행안 × 실행 묶음 × 차량 조건을 변환기(S1–S6) → M05 → M07 검증까지 돌린다.

    python3 experiments/guro_exp/run_matrix.py --bundles <묶음 루트>/bundles \
        --plan smoke:11=<plan_f11.json> --plan smoke:13=<plan_f13.json> \
        --out <저장소 밖 결과 폴더> [--data-label guro_0610_2.zip] [--jobs 4]

- `--bundles`: `<변형>_<YYYYMMDD>/` 폴더들이 든 곳. 폴더마다 scenario_f{F}.json,
  run_inputs_f{F}.json, travel_times.json이 있어야 한다.
- `--plan LABEL:FLEET=PATH`: 비교 입력 plan.json(experiments/plans/schema.md). 차량 조건마다 따로 준다.
- `--out`: 실행 원본(events.jsonl 등)이 커서 저장소 밖이나 .gitignore된 곳만 받는다.

실행 1건은 `<out>/<LABEL>/<묶음>/f<FLEET>/`에 conversion_report.json, plan_m05.json,
scenario/, run/, verifier_report.json을 남긴다. 전체 색인은 `<out>/runs.json`이다.
정책·변형·망은 묶음에 든 그대로 쓰고 이 스크립트는 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUNDLE_RE = re.compile(r"^(?P<variant>.+)_(?P<date>\d{8})$")


def parse_plan(text: str) -> tuple[str, int, Path]:
    try:
        key, path = text.split("=", 1)
        label, fleet = key.rsplit(":", 1)
        return label, int(fleet), Path(path).resolve()
    except ValueError:
        raise argparse.ArgumentTypeError(f"--plan은 LABEL:FLEET=PATH 형식이다: {text!r}")


def check_out_dir(out: Path) -> None:
    """저장소 안이면 .gitignore된 곳만 허용한다(실행 원본을 커밋하지 않기 위해)."""
    try:
        out.relative_to(REPO)
    except ValueError:
        return
    probe = out / "events.jsonl"
    ignored = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(probe)], check=False).returncode == 0
    if not ignored:
        sys.exit(f"--out {out} 은 저장소 안의 추적 가능한 위치다. 저장소 밖이나 .gitignore된 폴더를 쓴다.")


def git_state() -> dict:
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "tracked_changes": bool(git("status", "--porcelain", "--untracked-files=no"))}


def last_json(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def run_one(job: dict, env: dict) -> dict:
    d = Path(job["dir"])
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    b = Path(job["bundle_dir"])
    f = job["fleet"]
    scen, rin, tt = b / f"scenario_f{f}.json", b / f"run_inputs_f{f}.json", b / "travel_times.json"
    rec = {k: job[k] for k in ("label", "fleet", "bundle", "variant", "date")}
    rec["dir"] = str(d)
    t0 = time.time()

    def step(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", *args], cwd=REPO, env=env, capture_output=True, text=True,
                              check=False)

    p = step(["busstrike.sim.adapters.comparison_plan", "--plan", job["plan"], "--scenario", str(scen),
              "--travel-times", str(tt), "--out", str(d / "plan_m05.json")])
    (d / "conversion_report.json").write_text(p.stdout, encoding="utf-8")
    conv = last_json(p.stdout) or {}
    rec["conversion_rc"] = p.returncode
    if p.returncode != 0:
        rec["stage_failed"] = "conversion"
        failed = sorted(k for k, v in conv.get("checks", {}).items() if v != "pass")
        rec["reason"] = (f"변환기 검사 실패 {','.join(failed)}: {json.dumps(conv.get('failures'), ensure_ascii=False)[:300]}"
                         if failed else f"변환기 입력 오류: {(conv.get('message') or p.stderr)[-300:]}")
        rec["seconds"] = round(time.time() - t0, 1)
        return rec

    p = step(["busstrike.sim", "run", "--scenario", str(scen), "--run-inputs", str(rin),
              "--plan", str(d / "plan_m05.json"), "--out", str(d)])
    sim = last_json(p.stdout) or {}
    rec["sim_rc"] = p.returncode
    if p.returncode != 0 or sim.get("status") != "ok":
        rec["stage_failed"] = "simulation"
        rec["reason"] = f"M05 실패: {(sim.get('message') or p.stderr or p.stdout)[-300:]}"
        rec["seconds"] = round(time.time() - t0, 1)
        return rec
    rec["input_bundle_id"] = sim.get("input_bundle_id")
    rec["engine_env"] = sim.get("env")
    rec["policy"] = sim.get("policy")

    p = step(["busstrike.evaluation.verify", str(d / "run"), "--max-recompute", "200000",
              "--out", str(d / "verifier_report.json"), "--quiet"])
    rec["verify_rc"] = p.returncode
    rep_path = d / "verifier_report.json"
    if not rep_path.exists():
        rec["stage_failed"] = "verification"
        rec["reason"] = f"검증기 오류: {p.stderr[-300:]}"
    else:
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        rec["verify_pass"] = rep.get("pass")
        rec["failed_checks"] = rep.get("failed_checks")
        rec["not_run_checks"] = rep.get("not_run_checks")
        rec["not_verified_checks"] = rep.get("not_verified_checks")
        if rep.get("pass") is not True:
            rec["stage_failed"] = "verification"
            rec["reason"] = f"M07 pass=false: 실패 {rep.get('failed_checks')} · 미실행 {rep.get('not_run_checks')}"
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, type=Path)
    ap.add_argument("--plan", required=True, action="append", type=parse_plan)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--data-label", default=None, help="보고서에 적을 입력 묶음 이름(예: zip 파일명)")
    ap.add_argument("--src", type=Path, default=REPO / "src", help="busstrike 패키지가 있는 src 폴더")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    bundles_root = args.bundles.resolve()
    out = args.out.resolve()
    check_out_dir(out)
    out.mkdir(parents=True, exist_ok=True)

    bundles = []
    for b in sorted(p for p in bundles_root.iterdir() if p.is_dir()):
        m = BUNDLE_RE.match(b.name)
        if not m:
            sys.exit(f"묶음 폴더 이름이 <변형>_<YYYYMMDD>가 아니다: {b.name}")
        bundles.append((b, m["variant"], m["date"]))
    if not bundles:
        sys.exit(f"묶음이 없다: {bundles_root}")

    jobs = []
    for label, fleet, plan in args.plan:
        if not plan.is_file():
            sys.exit(f"운행안 파일이 없다: {plan}")
        for b, variant, date in bundles:
            jobs.append({"label": label, "fleet": fleet, "plan": str(plan), "bundle": b.name,
                         "bundle_dir": str(b), "variant": variant, "date": date,
                         "dir": str(out / label / b.name / f"f{fleet}")})

    env = {**os.environ, "PYTHONPATH": str(args.src.resolve())}
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = []
        for rec in pool.map(lambda j: run_one(j, env), jobs):
            results.append(rec)
            status = "ok" if "stage_failed" not in rec else f"FAIL({rec['stage_failed']})"
            print(f"[{len(results):3d}/{len(jobs)}] {rec['label']} f{rec['fleet']} {rec['bundle']}: {status} "
                  f"{rec['seconds']}s", flush=True)

    readme = bundles_root.parent / "README.md"
    meta = {
        "data_label": args.data_label,
        "bundles_readme_title": readme.read_text(encoding="utf-8").splitlines()[0] if readme.exists() else None,
        "plans": [{"label": l, "fleet": f, "file": p.name,
                   "content": json.loads(p.read_text(encoding="utf-8"))} for l, f, p in args.plan],
        "repo": git_state(),
        "python": sys.version.split()[0],
        "wall_seconds": round(time.time() - started, 1),
    }
    (out / "runs.json").write_text(json.dumps({"meta": meta, "runs": results}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    bad = [r for r in results if "stage_failed" in r]
    print(f"완료 {len(results) - len(bad)}/{len(results)} 성공, 실패 {len(bad)}. 색인: {out / 'runs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
