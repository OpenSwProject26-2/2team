"""hub_sweep 결과를 구 × 설정 × 대수 × 주간으로 모은다(M1 served 5일 평균, M07 통과 수).

    python3 experiments/routegen_lab/summarize_sweep.py --dir <저장소 밖>/hub_sweep [--md out.md]

- 설정 muni = 지자체 셔틀안(eval_municipal.py). 셔틀만 = 셔틀 탑승 완료 ÷ 흡수 뒤 셔틀 대상, 전체 = (마을 흡수 + 경기 흡수 + 셔틀 완료) ÷ 파업 노선 승객(affected).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def collect(gdir: Path) -> list[dict]:
    log = json.loads((gdir / "hub_sweep.json").read_text(encoding="utf-8"))
    out = []
    cfgs = list(log["configs"]) + (["muni"] if (gdir / "muni" / "muni.json").exists() else [])
    for cfg in cfgs:
        for split in ("exp", "test"):
            idx = gdir / cfg / f"runs_{split}" / "runs.json"
            if not idx.exists():
                continue
            by = defaultdict(list)
            for r in json.loads(idx.read_text(encoding="utf-8"))["runs"]:
                m = json.loads((Path(r["dir"]) / "run" / "metrics.json").read_text(encoding="utf-8"))["M1"]
                by[r["fleet"]].append((m["served"], m["total"], bool(r.get("verify_pass"))))
            ab = log["absorbed"] if split == "exp" else log["absorbed_test"]
            aff = log["affected_total"] if split == "exp" else log["affected_total_test"]
            for f, xs in sorted(by.items()):
                served = sum(x[0] for x in xs) / len(xs)
                total = sum(x[1] for x in xs) / len(xs)
                absorbed = ab["maeul"] + ab["gg"]
                plan = next((p for p in log["plans"] if p["fleet"] == f and p.get("config") == cfg), {})
                if cfg == "muni":
                    mj = json.loads((gdir / "muni" / "muni.json").read_text(encoding="utf-8"))
                    plan = {"routes": dict.fromkeys(range(mj["routes"]))}
                out.append({"gu": log["gu"], "config": cfg, "split": split, "fleet": f, "days": len(xs),
                            "m07_pass": sum(x[2] for x in xs), "affected": aff, "maeul": ab["maeul"], "gg": ab["gg"],
                            "shuttle_demand": round(total, 1), "served": round(served, 1),
                            "shuttle_rate": served / total if total else None,
                            "overall_rate": (absorbed + served) / aff if aff else None,
                            "pool_cover": plan.get("pool_cover"), "routes": len(plan.get("routes", {}))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--md", type=Path)
    args = ap.parse_args()
    rows = []
    for g in sorted(args.dir.iterdir()):
        if (g / "hub_sweep.json").exists():
            rows += collect(g)
    (args.dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["| 구 | 설정 | 주간 | 대수 | M07 | 셔틀 대상 | 후보 덮음 | 셔틀 완료 | 셔틀만 | 전체 |", "|" + "---|" * 10]
    for r in rows:
        pc = f"{r['pool_cover'] / r['shuttle_demand'] * 100:.1f}%" if r["pool_cover"] and r["split"] == "exp" else ""
        lines.append(f"| {r['gu']} | {r['config']} | {r['split']} | {r['fleet']} | {r['m07_pass']}/{r['days']} | "
                     f"{r['shuttle_demand']:,.0f} | {pc} | {r['served']:,.0f} | {r['shuttle_rate'] * 100:.1f}% | "
                     f"{r['overall_rate'] * 100:.1f}% |")
    text = "\n".join(lines)
    if args.md:
        args.md.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
