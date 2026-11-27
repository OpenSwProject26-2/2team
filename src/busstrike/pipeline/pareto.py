"""7단계 지표·파레토: pareto.json(docs/design/pipeline.md 5절).

- 같은 plan(sha256)은 한 항목으로 합치고 형평 기준(floors) 목록을 붙인다.
- hull = 지배당하지 않음: 다른 우리 안이 (충족률 ≥, 형평성 ≥, 둘 중 하나는 엄격히 큼)이면 지배당한다.
  지자체안은 hull 계산에 넣지 않는다. 이 경계는 우리 안끼리의 경험적 비지배 경계이지 이론적 파레토 곡선이 아니다.
"""

from __future__ import annotations

PLAN_KEYS = ("id", "base", "algo_name", "line_group", "fill", "floors", "plan_sha256", "served_rate", "equity_far",
             "isolation", "time_ratio_rode", "time_ratio_common", "refused_unserved", "reasons", "routes", "cycle_sum_min",
             "floor_met", "short_far", "hull", "export_path")


def dominates(a: dict, b: dict) -> bool:
    return (a["served_rate"] >= b["served_rate"] and a["equity_far"] >= b["equity_far"]
            and (a["served_rate"] > b["served_rate"] or a["equity_far"] > b["equity_far"]))


def mark_hull(plans: list[dict]) -> list[dict]:
    """plans에 hull(bool)을 채운다(지표가 없는 안은 hull=False). 순서는 바꾸지 않는다."""
    ok = [p for p in plans if p.get("served_rate") is not None and p.get("equity_far") is not None]
    for p in plans:
        p["hull"] = p in ok and not any(dominates(q, p) for q in ok if q is not p)
    return plans


def merge_same_plan(rows: list[dict]) -> list[dict]:
    """(알고리즘, 형평 기준)마다 한 줄인 rows를 plan_sha256로 합친다. 같은 sha의 첫 줄을 남기고 floors를 모은다.
    합친 결과는 (알고리즘 순서, 첫 기준 순서) 그대로, floors는 나온 순서대로."""
    out: list[dict] = []
    by: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["algo"], r["plan_sha256"])
        if key in by:
            by[key]["floors"].append(r["floor"])
            # 같은 plan이면 하한 충족 여부는 기준마다 다르다: 기준별로 남긴다
            by[key]["floor_check"].append({"floor": r["floor"], "floor_met": r["floor_met"], "short_far": r["short_far"]})
            continue
        p = {k: v for k, v in r.items() if k not in ("floor", "algo")}
        p["floors"] = [r["floor"]]
        p["floor_check"] = [{"floor": r["floor"], "floor_met": r["floor_met"], "short_far": r["short_far"]}]
        by[key] = p
        out.append(p)
    return out


def document(gu: str, fleet: int, strike: float, eval_days: list[str], demand_total: float | None,
             plans: list[dict], muni: dict | None) -> dict:
    mark_hull(plans)
    return {"gu": gu, "fleet": fleet, "strike": strike, "eval_days": eval_days, "demand_total": demand_total,
            "plans": plans, "muni": muni}
