"""구로구 지자체안 전사본이 같은 매트릭스(변환기 → M05 → M07)에 그대로 올라가는지 확인한다(고치지 않는다).

    python3 experiments/guro_routegen/check_municipal.py --root <guro_0610 폴더> --out <저장소 밖>/municipal_check.json \
        [--ref origin/docs/15-municipal-plan-transcription] [--path experiments/plans/plan-01/plan.json]

전사본은 git ref에서 `git show`로 읽어 저장소 밖에 둔다(작업 폴더·브랜치를 바꾸지 않는다).
변환기(`busstrike.sim.adapters.comparison_plan`)를 그 그대로 한 번 돌리고, 망에 없는 정류장·구간 수를 센다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
from busstrike.planning.routegen.data import check_root  # noqa: E402


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ref", default="origin/docs/15-municipal-plan-transcription")
    ap.add_argument("--path", default="experiments/plans/plan-01/plan.json")
    ap.add_argument("--bundle", default="affected_20261026")
    ap.add_argument("--fleet", type=int, default=11)
    args = ap.parse_args()
    info = check_root(args.root)

    rec: dict = {"ref": args.ref, "path": args.path, "root_check": info}
    commit = git("rev-parse", args.ref)
    rec["ref_commit"] = commit.stdout.strip() if commit.returncode == 0 else None
    rec["in_main"] = git("merge-base", "--is-ancestor", args.ref, "main").returncode == 0 if rec["ref_commit"] else False
    shown = git("show", f"{args.ref}:{args.path}")
    if shown.returncode != 0:
        rec["found"] = False
        args.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print("전사본 없음")
        return 0
    rec["found"] = True
    plan = json.loads(shown.stdout)
    rec["plan_id"], rec["revision"] = plan.get("plan_id"), plan.get("revision")
    rec["condition"] = plan.get("condition")
    rec["network_snapshot_id"] = plan.get("network_snapshot_id")
    rec["routes"] = len(plan.get("routes", []))
    rec["vehicles"] = sum(int(r.get("vehicles") or 0) for r in plan.get("routes", []))

    b = args.root.resolve() / "bundles" / args.bundle
    tt = json.loads((b / "travel_times.json").read_text(encoding="utf-8"))
    nodes = {x for s in tt["segments"] for x in (s["from_stop_id"], s["to_stop_id"])}
    segs = {(s["from_stop_id"], s["to_stop_id"]) for s in tt["segments"]}
    per = []
    for r in plan.get("routes", []):
        st = r["stops"]
        per.append({"route_id": r["route_id"], "stops": len(st), "vehicles": r.get("vehicles"),
                    "stops_not_in_network": sorted({s for s in st if s not in nodes}),
                    "links": len(st) - 1, "links_not_in_network": sum((x, y) not in segs for x, y in zip(st, st[1:]))})
    rec["per_route"] = per
    rec["stops_not_in_network"] = sum(len(p["stops_not_in_network"]) for p in per)
    rec["links"] = sum(p["links"] for p in per)
    rec["links_not_in_network"] = sum(p["links_not_in_network"] for p in per)

    with tempfile.TemporaryDirectory() as t:
        pth = Path(t) / "plan.json"
        pth.write_text(shown.stdout, encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "busstrike.sim.adapters.comparison_plan", "--plan", str(pth),
                            "--scenario", str(b / f"scenario_f{args.fleet}.json"), "--travel-times",
                            str(b / "travel_times.json"), "--out", str(Path(t) / "m05.json")],
                           cwd=REPO, env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True, text=True)
    lines = [x for x in p.stdout.strip().splitlines() if x.strip()]
    conv = json.loads(lines[-1]) if lines else {}
    rec["conversion"] = {"bundle": args.bundle, "fleet": args.fleet, "rc": p.returncode, "ok": conv.get("ok"),
                         "failures": conv.get("failures"), "checks": conv.get("checks")}
    args.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"전사본 {rec['plan_id']} rev{rec['revision']}: 변환기 rc {p.returncode}, 망에 없는 정류장 "
          f"{rec['stops_not_in_network']}, 망에 없는 구간 {rec['links_not_in_network']}/{rec['links']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
