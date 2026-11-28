"""충족률·형평성 곡선(파이프라인 pareto.json)과 안별 지도용 운행안(export/<plan id>.json)을 구별로 읽는다.

<pareto_root>/<구>/pareto.json, <pareto_root>/<구>/export/*.json (factorial_1126 같은 파이프라인 출력).
- 계산하지 않는다. pareto.json을 그대로 주고, 있으면 두 가지만 덧붙인다:
  - plans[].rep: demo/representative.json의 대표안 역할(탑승 우선·균형·형평 우선), plan_sha256으로 맞춤
  - muni.export_path: export/muni.json이 있으면(지자체안 점을 눌러 노선을 볼 수 있게)
- export는 pareto.json에 있는 안(과 지자체안)만 준다.
- run(실행 버튼 작업 ID)을 주면 <pareto_root>/<구>/runs/<run_id>/job.json의 out_dir에서 읽는다(d_runs). 주지 않으면 예전 그대로
  <pareto_root>/<구>/pareto.json.
- 안마다 refused_rate(만차 거절률 = refused_unserved ÷ demand_total, 둘 다 하루 평균 명)를 덧붙인다. 확정 묶음
  confirm.json metrics와 같은 정의(metrics_of).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REGION = re.compile(r"^[a-z_]+$")
METRIC_KEYS = ("served_rate", "equity_far", "isolation", "refused_rate", "time_ratio_rode")


def refused_rate(plan: dict | None, demand_total) -> float | None:
    """만차 거절률 = 만차로 끝내 못 탄 승객(하루 평균) ÷ 셔틀 대상 수요(하루 평균). 둘 중 하나라도 없으면 None."""
    if not plan or plan.get("refused_unserved") is None or not demand_total:
        return None
    return round(float(plan["refused_unserved"]) / float(demand_total), 4)


def metrics_of(plan: dict, demand_total) -> dict:
    """확정 묶음·지표 띠가 같이 쓰는 지표 다섯 개."""
    return {"served_rate": plan.get("served_rate"), "equity_far": plan.get("equity_far"),
            "isolation": plan.get("isolation"), "refused_rate": refused_rate(plan, demand_total),
            "time_ratio_rode": plan.get("time_ratio_rode")}


GATE_ID = "oneway_loop_lg0_fill0__floor_muni"   # 요인 실험 v*: 관문 편도 후 순회 (0,0), 하한 = 지자체안 값 점의 형평성


def representative_roles(plans: list[dict]) -> dict[str, str]:
    """경계(충족률·형평성 둘 다 지배되지 않는 점) 위 대표안: 탑승 우선 = 충족률 최대, 형평 우선 = 형평성 v* 이상 중
    충족률 최대(v* 점이 없는 빠른 설정은 형평성 최대), 균형 = 두 끝(탑승 우선·형평성 최대)을 잇는 선에서 가장 먼 무릎점.
    요인 실험 scripts/analyze.py reps()와 같은 규칙·같은 동점 처리."""
    import math
    pts = {}
    for p in plans:
        if p.get("served_rate") is not None and p.get("equity_far") is not None and p.get("id"):
            pts.setdefault(p.get("plan_sha256") or p["id"], p)
    U = list(pts.values())
    dom = lambda p, q: (q["served_rate"] >= p["served_rate"] - 1e-12 and q["equity_far"] >= p["equity_far"] - 1e-12  # noqa: E731
                        and (q["served_rate"] > p["served_rate"] + 1e-12 or q["equity_far"] > p["equity_far"] + 1e-12))
    H = [p for p in U if not any(dom(p, q) for q in U if q is not p)]
    if not H:
        return {}
    key = lambda p: (-p["served_rate"], -p["equity_far"], p["id"])  # noqa: E731
    A = sorted(H, key=key)[0]
    B = sorted(H, key=lambda p: (-p["equity_far"], -p["served_rate"], p["id"]))[0]
    gate = next((p for p in plans if p.get("id") == GATE_ID and p.get("equity_far") is not None), None)
    vstar = gate["equity_far"] if gate else B["equity_far"]
    eqp = [p for p in H if p["equity_far"] >= vstar - 1e-12]
    E = sorted(eqp, key=key)[0] if eqp else None
    K = None
    if len(H) > 2 and B["equity_far"] > A["equity_far"]:
        def dist(p):
            u = (p["equity_far"] - A["equity_far"]) / (B["equity_far"] - A["equity_far"])
            w = (p["served_rate"] - B["served_rate"]) / (A["served_rate"] - B["served_rate"]) if A["served_rate"] != B["served_rate"] else 0
            return (u + w - 1) / math.sqrt(2)
        cands = sorted([p for p in H if p is not A and p is not B], key=lambda p: (-dist(p), -p["equity_far"], p["id"]))
        cands = [p for p in cands if p is not E]
        K = cands[0] if cands else None
    out: dict[str, list[str]] = {}
    for role, p in (("탑승 우선", A), ("형평 우선", E), ("균형", K)):
        if p is not None:
            out.setdefault(p["id"], []).append(role)
    return {k: "·".join(v) for k, v in out.items()}


class ParetoStore:
    def __init__(self, root: str | Path | None, runs=None):
        self.root = Path(root).resolve() if root else None
        self.runs = runs                      # d_runs.RunManager(있으면 run 인자를 받는다)
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def _dir(self, region: str, run: str | None = None) -> Path | None:
        if not self.root or not REGION.match(region or ""):
            return None
        if run:
            hit = self.runs.find(run) if self.runs is not None else None
            if not hit or hit[1].get("gu") != region:
                return None
            d = Path(hit[1].get("out_dir") or hit[0])
            return d if (d / "pareto.json").is_file() else None
        d = self.root / region
        return d if (d / "pareto.json").is_file() else None

    def regions(self) -> list[str]:
        if not self.root or not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if REGION.match(p.name) and (p / "pareto.json").is_file())

    def pareto(self, region: str, run: str | None = None) -> dict:
        d = self._dir(region, run)
        if d is None:
            return {"status": "unavailable", "region": region, "run": run}
        mtime = (d / "pareto.json").stat().st_mtime
        hit = self._cache.get((region, run))
        if hit and hit[0] == mtime:
            return hit[1]
        doc = json.loads((d / "pareto.json").read_text(encoding="utf-8"))
        rep_file = d / "demo" / "representative.json"
        if rep_file.is_file():
            roles: dict[str, list[str]] = {}
            for x in json.loads(rep_file.read_text(encoding="utf-8")).get("representative", []):
                if x.get("plan_sha256") and x.get("role"):
                    roles.setdefault(x["plan_sha256"], []).append(x["role"])
            for p in doc.get("plans", []):
                if p.get("plan_sha256") in roles:
                    p["rep"] = "·".join(roles[p["plan_sha256"]])
        elif doc.get("plans"):
            # 새 실행(작업 실행기)에는 대표안 파일이 없다 → 요인 실험 analyze.py reps와 같은 규칙으로 계산
            roles = representative_roles(doc["plans"])
            for p in doc["plans"]:
                if p.get("id") in roles:
                    p["rep"] = roles[p["id"]]
            doc["rep_source"] = "계산(요인 실험 analyze.py reps 규칙, 경계 위 점)"
        if doc.get("muni") and not doc["muni"].get("export_path") and (d / "export" / "muni.json").is_file():
            doc["muni"]["export_path"] = "export/muni.json"
        for p in doc.get("plans", []):
            p["refused_rate"] = refused_rate(p, doc.get("demand_total"))
        if doc.get("muni"):
            doc["muni"]["refused_rate"] = refused_rate(doc["muni"], doc.get("demand_total"))
        n_excl = len(doc.get("excluded") or [])
        out = {"status": "available", "region": region, "source": str(d), "run": run, "excluded_count": n_excl, **doc}
        self._cache[(region, run)] = (mtime, out)
        return out

    def plan_dir(self, region: str, run: str | None = None) -> Path | None:
        """확정이 실행 폴더(eval/, gen/, bundles/)를 찾을 때 쓴다."""
        return self._dir(region, run)

    def export(self, region: str, plan_id: str, run: str | None = None) -> dict:
        doc = self.pareto(region, run)
        if doc.get("status") != "available":
            return {"status": "unavailable", "region": region}
        paths = {p["id"]: p.get("export_path") for p in doc.get("plans", [])}
        if doc.get("muni"):
            paths[doc["muni"].get("id", "muni")] = doc["muni"].get("export_path")
        rel = paths.get(plan_id)
        if not rel:
            return {"status": "unavailable", "region": region, "plan_id": plan_id}
        base = self._dir(region, run).resolve()
        f = (base / rel).resolve()
        if base not in f.parents or not f.is_file():
            return {"status": "unavailable", "region": region, "plan_id": plan_id}
        return {"status": "available", **json.loads(f.read_text(encoding="utf-8"))}
