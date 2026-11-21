"""안마다 평가 규칙 조합(기본 9개)의 형평 지표 "탈 수 있는 수요 비율"(전체, 역 800m 밖)을 낸다(설계 2.2).

    python3 experiments/routegen_lab/equity_eval.py --gu guro --root <region>/<구>_0610_v4 [--test-root <…_test>] \
        --osm <구_roads.json> --plan 이름=<plan.json> [--plan …] --out <저장소 밖>/equity.json \
        [--nb <도보 이웃 400m json>] [--nb-far <도보 이웃 800m json>] [--params gu_params.json]

- 수요: 실험 주간 5일 평균, 정밀 흡수 뒤(배분기와 같음). 분모 = 그 구 동네 정류장 수요(`equity.EquityIndex`).
- 역 800m 밖: 동네 정류장에서 새 망 도로 도보(`walknet`) `equity.far_m`(설정, 기본 800m) 안에 역 지정 정류장이 없는 정류장.
- 분자: 조합 규칙(`reach.Reach`)으로 그 안의 정차열들이 덮는 OD 키(= 평가 재배정으로 탈 수 있는 승객).
- 도보 이웃은 없으면 재서 out 옆에 저장한다(`nb<반경>.json`).
`setup`·`plan_equity`는 walk_eval(배분기 형평 하한)과 eval_rules(결과 기록)가 같이 쓴다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))

from busstrike.planning.routegen import data as D  # noqa: E402
from busstrike.planning.routegen import gu_config  # noqa: E402
from busstrike.planning.routegen.absorb import Absorber, residual_rows  # noqa: E402
from busstrike.planning.routegen.equity import EquityIndex, od_from_start  # noqa: E402
from busstrike.planning.routegen.reach import Reach, StationIndex  # noqa: E402


def load_or_measure_nb(path: Path | None, cache: Path, osm: Path, root: Path, radius: float, stops=None) -> tuple[dict, str]:
    """도보 이웃 json을 읽거나, 없으면 새 망(RoadNet edge 붙이기)으로 재서 cache에 쓴다."""
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8")), str(path)
    if not cache.exists():
        from roadnet import RoadNet
        from walknet import walk_neighbors
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(walk_neighbors(RoadNet(osm, root), radius, stops), separators=(",", ":")),
                         encoding="utf-8")
    return json.loads(cache.read_text(encoding="utf-8")), str(cache)


def setup(root: Path, cfg, osm: Path, cache_dir: Path, test_root: Path | None = None, nb: dict | None = None,
          nb_path: Path | None = None, nb_far_path: Path | None = None, lines: Path | None = None,
          demand=None, rows=None, start_hour: int = 6) -> dict:
    """형평 계산 준비: 수요(정밀 흡수 뒤) · 역 색인 · 도보 이웃(400m, 800m) · EquityIndex."""
    info = D.check_root(root, "exp")
    base = D.load_network(root)
    st_stops = {x for s in base.stations.values() for x in (s.access_stop, s.egress_stop) if x}
    home = {s for s, x in base.stops.items() if x.gu == info["gu"]}
    if rows is None:
        rows = D.load_flows(root)
    if demand is None:
        res, _ = Absorber(root, cfg.absorb_params()).absorb(rows, True, True)
        demand = D.build_demand(residual_rows(rows, res), "affected")
    ix = StationIndex.from_root(root, lines or HERE.parent / "station_lines.csv")
    ix.add_flows(rows)
    if test_root and Path(test_root).exists():
        ix.add_flows(D.load_flows(test_root))
    eqc = cfg.equity
    far_m = float(eqc.get("far_m", 800))
    rmax = max(c.walk_radius_m for c in cfg.eval_combos() + [cfg.eval_rule()])
    nb_src = "given"
    if nb is None:
        nb, nb_src = load_or_measure_nb(nb_path, cache_dir / f"nb{int(rmax)}.json", osm, root, rmax)
    nb_far, far_src = load_or_measure_nb(nb_far_path, cache_dir / f"nb{int(far_m)}_home.json", osm, root, far_m, home)
    od_total = od_from_start(demand.od, start_hour, D.HOURS)   # 운행 시작 전 시간대 수요는 뺀다(07:00 → 06~07시 제외)
    eq = EquityIndex.build(od_total, st_stops, home, nb_far, far_m)
    return {"info": info, "base": base, "st_stops": st_stops, "home": home, "rows": rows, "demand": demand, "ix": ix,
            "nb": nb, "nb_src": nb_src, "nb_far_src": far_src, "od_total": od_total, "eq": eq, "keys": sorted(demand.od)}


def plan_equity(ctx: dict, routes: list[list[str]], rules) -> dict:
    """안 하나 × 규칙들 → {규칙 이름: {total, far, rideable, rideable_far}}."""
    out = {}
    for rule in rules:
        rc = ctx.setdefault("_reach", {}).get(rule.label())
        if rc is None:
            rc = ctx["_reach"][rule.label()] = Reach(ctx["ix"], rule, ctx["nb"], ctx["keys"])
        r = ctx["eq"].of_plan(rc, routes)
        out[rule.label()] = {"total": round(r["total"], 4), "far": round(r["far"], 4), "rideable": r["rideable"],
                             "rideable_far": r["rideable_far"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gu", required=True)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--test-root", type=Path, default=None)
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--plan", action="append", default=[], help="이름=plan.json(여러 번)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--nb", type=Path, default=None)
    ap.add_argument("--nb-far", type=Path, default=None)
    ap.add_argument("--params", type=Path, default=HERE.parent / "gu_params.json")
    args = ap.parse_args()
    cfg = gu_config.load(args.params, args.gu)
    out = args.out.resolve()
    ctx = setup(args.root, cfg, args.osm, out.parent, args.test_root, nb_path=args.nb, nb_far_path=args.nb_far)
    rules = cfg.eval_combos()
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    doc = {"code": {"commit": commit, "tracked_changes": dirty}, "gu": ctx["info"]["gu"], "root": str(args.root),
           "nb": ctx["nb_src"], "nb_far": ctx["nb_far_src"], "equity_base": ctx["eq"].record(),
           "rules": {r.label(): r.record() for r in rules}, "params": cfg.record(), "plans": {}}
    for spec in args.plan:
        name, path = spec.split("=", 1)
        routes = [r["stops"] for r in json.loads(Path(path).read_text(encoding="utf-8"))["routes"]]
        doc["plans"][name] = {"plan": path, "equity": plan_equity(ctx, routes, rules)}
        base = doc["plans"][name]["equity"][cfg.eval_rule().label()]
        print(name, f"기본 조합 {base['total']:.1%} / 역 800m 밖 {base['far']:.1%}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
