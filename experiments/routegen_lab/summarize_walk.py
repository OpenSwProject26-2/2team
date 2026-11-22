"""walk_eval 결과 요약: 안 × 대수 × 시뮬레이터 가정(nowalk·walk) × 주간.

    python3 experiments/routegen_lab/summarize_walk.py --dir <walk_eval/<구>> [--md out.md]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LABEL = {"muni": "지자체안", "s12": "우리 · 허브135·정차12(기존)", "walk": "우리 · 걷기 반영 배분",
         "cov60": "우리 · 형평 먼저 60%", "cov70": "우리 · 형평 먼저 70%", "cov80": "우리 · 형평 먼저 80%",
         **{f"cov{t}r{l}": f"우리 · 형평 먼저 {t}% + 탈 수요 ×{l}" for t in (60, 70, 80) for l in (1, 3)},
         "walkm": "우리 · 걷기 반영 배분 + 여러 역 경유",
         **{f"cov{t}r3m": f"우리 · 형평 먼저 {t}% + 탈 수요 ×3 + 여러 역 경유" for t in (60, 70)},
         **{f"cov{t}r3md": f"우리 · 형평 먼저 {t}% + 탈 수요 ×3 + 여러 역 경유 + 역 근처 할인" for t in (60, 70, 80)}}


def runs(idx: Path, label: str, fleet: int):
    if not idx.exists():
        return None
    xs = []
    for r in json.loads(idx.read_text(encoding="utf-8"))["runs"]:
        if r["label"] == label and r["fleet"] == fleet:
            m = json.loads((Path(r["dir"]) / "run" / "metrics.json").read_text(encoding="utf-8"))["M1"]
            xs.append((m["served"], m["total"], r.get("verify_pass") is True))
    if not xs:
        return None
    return {"served": sum(x[0] for x in xs) / len(xs), "total": sum(x[1] for x in xs) / len(xs),
            "pass": sum(x[2] for x in xs), "days": len(xs)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--md", type=Path)
    args = ap.parse_args()
    d = args.dir
    log = json.loads((d / "walk_eval.json").read_text(encoding="utf-8"))
    rows = []
    for key, p in log["plans"].items():
        lab, f = p["label"], p["fleet"]
        for split in ("exp", "test"):
            ab = log["absorbed"] if split == "exp" else log["absorbed_test"]
            A = log["affected_total"] if split == "exp" else log["affected_total_test"]
            for mode in ("nowalk", "walk"):
                idx = d / f"runs_{mode}" / f"{lab}-f{f}" / split / "runs.json"
                if mode == "nowalk" and not idx.exists():
                    idx = d / "runs_nowalk" / split / "runs.json"
                r = runs(idx, lab, f)
                if r is None:
                    continue
                ws = log["walk_stats"].get(f"{lab}-f{f}-{split}", {}) if mode == "walk" else {}
                rows.append({"plan": lab, "fleet": f, "split": split, "mode": mode, **r,
                             "rate": r["served"] / r["total"], "overall": (ab["maeul"] + ab["gg"] + r["served"]) / A,
                             "coverage": p["coverage"], "routes": p["routes"], "stops": p["stops"],
                             "walked": ws.get("walked"), "walk_m": ws.get("walk_m_avg")})
    (d / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    out = ["| 안 | 대수 | 걸어서 400m 안 수요 | 셔틀 태움 · 걷기 없음 | 셔틀 태움 · 걷기 400m | 셔틀만 · 걷기 400m | 전체 · 걷기 없음 | 전체 · 걷기 400m | M07 |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    order = list(LABEL)
    for f in sorted({r["fleet"] for r in rows}):
        for lab in order:
            a = next((r for r in rows if r["plan"] == lab and r["fleet"] == f and r["split"] == "exp" and r["mode"] == "nowalk"), None)
            b = next((r for r in rows if r["plan"] == lab and r["fleet"] == f and r["split"] == "exp" and r["mode"] == "walk"), None)
            if not a or not b:
                continue
            ok = sum(r["pass"] for r in rows if r["plan"] == lab and r["fleet"] == f)
            n = sum(r["days"] for r in rows if r["plan"] == lab and r["fleet"] == f)
            out.append(f"| {LABEL[lab]} | {f} | {a['coverage'] * 100:.0f}% | {a['served']:,.0f}명 | {b['served']:,.0f}명 | "
                       f"{b['rate'] * 100:.1f}% | {a['overall'] * 100:.1f}% | {b['overall'] * 100:.1f}% | {ok}/{n} |")
    text = "\n".join(out)
    if args.md:
        args.md.write_text(text + "\n", encoding="utf-8")
    print(text)
    for r in rows:
        if r["split"] == "test" and r["mode"] == "walk":
            print("test", r["plan"], r["fleet"], round(r["served"]), f"{r['overall'] * 100:.1f}%", "걸어간 승객", r["walked"], "평균", r["walk_m"], "m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
