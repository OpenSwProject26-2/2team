"""파업 비율 평가 요약(설계 2.4): strike_eval.py 결과 → 시나리오별 판정, 비율별 승/전체, 진 시나리오와 이유.

    python3 experiments/routegen_lab/strike_summary.py --dir <strike_eval 결과 폴더>

판정(설계 2.2, 같은 대수 11대끼리): 형평 두 값(전체·역 800m 밖)이 지자체안 이상이고 탑승(도보 반영, 5일 평균)이 더 많으면 승.
9개 조합 시나리오(2.3)는 조합마다 판정해 우세(9/9)·조건부(일부)·열세(기본 조합에서 짐)로 쓴다. 비례 대수 안은 대수가 달라
판정에 쓰지 않고 참고로 싣는다. AI안 6개는 우리 안(11대)과 같은 규칙으로 견준다.
결과: <dir>/summary.json, <dir>/SUMMARY.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

BASE = "same_line-w400"
MUNI = "muni07-f11"
OURS = "eqmc07-f11"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def per_plan(runs: list[dict]) -> dict:
    """(tag, rule) → 5일 평균 탑승·도보 반영 탑승·대상, 형평, M07 통과 수."""
    g = defaultdict(list)
    for r in runs:
        g[(r["tag"], r["rule"])].append(r)
    out = {}
    for k, rs in g.items():
        eq = rs[0].get("equity") or {}
        out[k] = {"served": mean([r.get("served") for r in rs]), "served_walk": mean([r.get("served_walk") for r in rs]),
                  "total": mean([r.get("total_sim") for r in rs]), "days": len(rs),
                  "m07_pass": sum(1 for r in rs if r.get("verify_pass") is True),
                  "equity_total": eq.get("total"), "equity_far": eq.get("far")}
    return out


def judge(a: dict, b: dict) -> tuple[bool, list[str]]:
    """a(우리)가 b(비교 안)보다 낫나(2.2). 돌려주는 값: (승, 진 이유)."""
    why = []
    if a["equity_total"] is None or b["equity_total"] is None:
        return False, ["형평 값 없음"]
    if a["equity_total"] + 1e-9 < b["equity_total"]:
        why.append(f"형평 전체 {a['equity_total']:.3f} < {b['equity_total']:.3f}")
    if a["equity_far"] + 1e-9 < b["equity_far"]:
        why.append(f"형평 역 800m 밖 {a['equity_far']:.3f} < {b['equity_far']:.3f}")
    if not (a["served_walk"] or 0) > (b["served_walk"] or 0):
        why.append(f"탑승 {a['served_walk']:.1f} ≤ {b['served_walk']:.1f}")
    if a["m07_pass"] < a["days"] or b["m07_pass"] < b["days"]:
        why.append("M07 미통과 실행 있음")
    return not why, why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    a = ap.parse_args()
    scen = []
    for sj in sorted(a.dir.glob("p*_d*/summary.json")):
        s = json.loads(sj.read_text(encoding="utf-8"))
        if s.get("error"):
            scen.append({"sid": s["sid"], "error": s["error"]})
            continue
        runs = s["results"]["runs"] if isinstance(s["results"], dict) else s["results"]
        pp = per_plan(runs)
        rules = sorted({r for _, r in pp})
        rec = {"sid": s["sid"], "rate": s["rate"], "draw": s["draw"], "strike_routes": len(s["strike"]["routes"]),
               "eligible": s["strike"]["eligible"], "fleets": s["fleets"], "rules": s.get("rules", {}).get("params"),
               "must_stops": (s.get("must") or {}).get("must_stops"), "passengers_07_10": s["bundles"]["passengers_07_10"],
               "plans": {f"{t}|{r}": v for (t, r), v in sorted(pp.items())}, "judge": {}, "vs_ai": {}}
        for k, v in s.get("ours_alloc", {}).items():
            al = v.get("alloc") or {}
            m = al.get("must") or {}
            rec.setdefault("ours", {})[k] = {"approx": v.get("approx"), "feasible": al.get("feasible"),
                                             "warnings": al.get("warnings"),
                                             "must_required": len(m.get("stops", {})),
                                             "must_covered": sum(1 for x in m.get("stops", {}).values() if x.get("covered")),
                                             "must_uncovered": m.get("must_stop_uncovered", [])}
        for r in rules:
            if (OURS, r) in pp and (MUNI, r) in pp:
                ok, why = judge(pp[(OURS, r)], pp[(MUNI, r)])
                rec["judge"][r] = {"win": ok, "why": why}
        for (t, r), v in pp.items():
            if r == BASE and t.startswith("ai") and (OURS, BASE) in pp:
                ok, why = judge(pp[(OURS, BASE)], v)
                rec["vs_ai"][t] = {"win": ok, "why": why}
        if len(rec["judge"]) > 1:
            wins = sum(1 for x in rec["judge"].values() if x["win"])
            base_win = rec["judge"].get(BASE, {}).get("win")
            rec["grade"] = "우세" if wins == len(rec["judge"]) else ("열세" if not base_win else "조건부 우세")
            rec["grade_detail"] = f"{wins}/{len(rec['judge'])} 조합"
        scen.append(rec)
    by_rate = defaultdict(lambda: {"n": 0, "win": 0, "losses": []})
    for s in scen:
        if "error" in s:
            continue
        j = s["judge"].get(BASE)
        if j is None:
            continue
        b = by_rate[s["rate"]]
        b["n"] += 1
        b["win"] += int(j["win"])
        if not j["win"]:
            b["losses"].append({"sid": s["sid"], "why": j["why"]})
    out = {"scenarios": scen, "by_rate": {str(k): v for k, v in sorted(by_rate.items())},
           "total": {"n": sum(v["n"] for v in by_rate.values()), "win": sum(v["win"] for v in by_rate.values())}}
    (a.dir / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# 파업 비율 평가 요약 (구로, 실험 주간 10/26~30, 07:00 시작, 기본 조합 same_line-w400)", "",
         "판정(설계 2.2, 11대끼리): 형평 두 값 ≥ 지자체안 + 탑승(도보 반영, 5일 평균) 더 많음.", "",
         f"**전체: {out['total']['win']}/{out['total']['n']} 시나리오 승**", "",
         "| 파업 비율 | 승/전체 | 진 시나리오(이유) |", "|---|---|---|"]
    for k, v in sorted(by_rate.items()):
        L.append(f"| {round(k * 100)}% | {v['win']}/{v['n']} | " +
                 ("; ".join(f"{x['sid']}: {', '.join(x['why'])}" for x in v["losses"]) or "—") + " |")
    L += ["", "| 시나리오 | 파업 노선 | 대상(07~10시, 5일 평균) | 우리 11대 탑승 | 지자체 11대 | 우리 비례 대수 | 형평 우리(전체/800m 밖) | 형평 지자체 | 필수 덮음 | 판정 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in scen:
        if "error" in s:
            L.append(f"| {s['sid']} | 오류: {s['error']} |||||||||")
            continue
        P = s["plans"]
        o, m = P.get(f"{OURS}|{BASE}"), P.get(f"{MUNI}|{BASE}")
        prop = [f for f in s["fleets"] if f != 11]
        pv = P.get(f"eqmc07-f{prop[0]}|{BASE}") if prop else None
        pax = mean(list(s["passengers_07_10"].values()))
        ours = (s.get("ours") or {}).get(OURS) or {}
        mc = f"{ours.get('must_covered', 0)}/{ours.get('must_required', 0)}"
        j = s["judge"].get(BASE, {})
        L.append(f"| {s['sid']} | {s['strike_routes']}/{s['eligible']} | {pax:.0f} | "
                 f"{o['served_walk']:.1f} | {m['served_walk']:.1f} | "
                 + (f"{prop[0]}대 {pv['served_walk']:.1f}" if pv else "—") + " | "
                 f"{o['equity_total']:.3f}/{o['equity_far']:.3f} | {m['equity_total']:.3f}/{m['equity_far']:.3f} | {mc} | "
                 + ("승" if j.get("win") else "패: " + ", ".join(j.get("why", []))) +
                 (f" · 9개 조합 {s['grade']}({s['grade_detail']})" if s.get("grade") else "") + " |")
    L += ["", "AI안(11대, 기본 조합) 대비 우리 안 승: " + ", ".join(
        f"{s['sid']} {sum(1 for x in s['vs_ai'].values() if x['win'])}/{len(s['vs_ai'])}" for s in scen if s.get("vs_ai"))]
    (a.dir / "SUMMARY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
