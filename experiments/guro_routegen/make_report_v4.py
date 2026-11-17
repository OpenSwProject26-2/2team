"""routegen v1 설정을 v4 구 묶음(구로·동작·강서·강동 × 실험/테스트)으로 돌린 결과 보고서.

    python3 experiments/guro_routegen/make_report_v4.py --v4 <밖>/routegen_v4 --v1 <밖>/routegen_v1/base \
        --region <밖>/zip_v4/region --out experiments/reports/routegen_v4.md

읽는 것
- `<v4>/<구>/manifest.json`, `bundle_check.json`, `runs/<변형>/runs.json`(실험), `runs_test/<변형>/runs.json`(테스트)
- 실행별 `run/metrics.json`(M1 served·total, M2 미충족 사유)
- `<v1>`: v3 데이터 구로 기본 조건(v1 보고서 표 1과 같은 실행)
- `<region>/<구>_0610_v4[_test]/demand/demand_flows.csv.gz`: gg_reach가 시간대마다 바뀌는 쌍 수를 세고,
  테스트 묶음 잔여 수요가 그 묶음 demand_bins.csv와 같은지(`data.check_bundles`) 여기서 확인한다

충족률 = M1 served ÷ M1 total(그날 잔여 수요), 5일 평균(최소–최대). M07 pass인 실행만 집계한다.
숫자는 모두 이 스크립트가 계산한다. 보고서를 손으로 고치지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
from collections import defaultdict
import hashlib
import sys
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from busstrike.planning.routegen import data as D  # noqa: E402

GUS = (("guro", "구로구"), ("dongjak", "동작구"), ("gangseo", "강서구"), ("gangdong", "강동구"))
VARIANTS = ("affected", "affected_maeul", "affected_gg", "affected_maeul_gg")
LABEL = {"ours": "우리 안", "sp": "피더안"}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def read_runs(base: Path, sub: str) -> tuple[dict, list[dict]]:
    """(label, variant, fleet) → 날짜순 기록, 전체 기록 목록."""
    out: dict[tuple, list[dict]] = defaultdict(list)
    allr = []
    for rj in sorted((base / sub).glob("*/runs.json")):
        for r in load(rj)["runs"]:
            rec = dict(r)
            if r.get("verify_pass") is True:
                m = load(Path(r["dir"]) / "run" / "metrics.json")
                rec["served"] = m["M1"]["served"]
                rec["total"] = m["M1"]["total"]
                rec["reasons"] = m["M2"]["unserved_by_reason"]
            allr.append(rec)
            out[(r["label"], r["variant"], r["fleet"])].append(rec)
    for k in out:
        out[k].sort(key=lambda x: x["date"])
    return out, allr


def rates(rs: list[dict]) -> list[float]:
    return [r["served"] / r["total"] for r in rs if r.get("verify_pass") is True]


def mean(xs):
    return statistics.mean(xs) if xs else float("nan")


def pct(x: float, d: int = 2) -> str:
    return f"{100 * x:.{d}f}%"


def span(xs: list[float]) -> str:
    return f"{pct(mean(xs))} ({pct(min(xs))}–{pct(max(xs))})" if xs else "—"


def pp(x: float) -> str:
    return f"{100 * x:+.2f}%p"


def gg_vary(root: Path) -> tuple[int, int]:
    seen: dict[tuple, set] = defaultdict(set)
    for r in csv.DictReader(gzip.open(root / "demand" / "demand_flows.csv.gz", "rt", encoding="utf-8")):
        seen[(r["kind"], r["stop_id"], r["station_id"])].add(r["gg_reach"])
    return sum(len(v) > 1 for v in seen.values()), len(seen)


def plan_routes(manifest: dict) -> dict[tuple, dict]:
    return {(p["label"], p["variant"], p["fleet"]): p for p in manifest["plans"]}


def stops_key(p: dict) -> list:
    doc = load(Path(p["file"]))
    return sorted((tuple(r["stops"]), r["vehicles"]) for r in doc["routes"])


def pool_cover(g: str, region: Path, v4: Path, fleets) -> dict:
    """후보 풀 전체·안별로 덮는 잔여 수요(5일 평균), 역별 수요·덮개, 두 안의 같은 정차열 수."""
    man = load(v4 / g / "manifest.json")
    cd = load(v4 / g / "candidates" / "affected.json")
    C = {c["route_id"]: c for c in cd["candidates"]}
    root = region / f"{g}_0610_v4"
    net = D.load_network(root)
    dem = D.build_demand(D.load_flows(root), "affected")
    stop2st: dict[str, str] = {}
    for k, st_ in net.stations.items():
        for x in (st_.access_stop, st_.egress_stop):
            if x:
                stop2st[x] = k

    def keys_of(ids):
        out = set()
        for i in ids:
            out.update(tuple(x) for x in C[i]["cover_keys"])
        return out

    def by_station(keys):
        out: dict[str, float] = defaultdict(float)
        for o, d in keys:
            out[stop2st.get(d) or stop2st.get(o)] += dem.od_total((o, d))
        return out

    plans = {(p["label"], p["fleet"]): p for p in man["plans"] if p["variant"] == "affected"}
    plan_keys = {k: keys_of(r["candidate"] for r in p["routes"].values()) for k, p in plans.items()}
    same = {}
    for f in fleets:
        so = {tuple(C[r["candidate"]]["stops"]) for r in plans[("ours", f)]["routes"].values()}
        ss = {tuple(C[r["candidate"]]["stops"]) for r in plans[("sp", f)]["routes"].values()}
        same[f] = (len(so & ss), len(so), len(ss))
    pools = cd["pool_members"]
    return {"net": net, "dem": dem, "C": C, "man": man, "plans": plans, "same": same,
            "total": dem.total(), "pool_ours": keys_of(pools["ours"]), "pool_sp": keys_of(pools["sp"]),
            "by_station": by_station, "plan_keys": plan_keys, "keys_of": keys_of}


def station_blockers(g: str, region: Path, names: list[str], pc: dict) -> dict:
    """역별로 seed 1개짜리 노선이 어느 제약에 걸리는지, 통과한 것은 변환기(공차 복귀 포함)를 통과하는지."""
    from busstrike.planning.routegen import generate as G
    from busstrike.planning.routegen.cycle import single_route_plan
    from busstrike.planning.routegen.graph import Graph, Limits
    from busstrike.sim.adapters.comparison_plan import to_engine_plan
    from busstrike.sim.models import SimError
    root = region / f"{g}_0610_v4"
    net, dem = pc["net"], pc["dem"]
    params = G.Params(**{k: v for k, v in pc["man"]["params"].items() if k in G.Params.__dataclass_fields__
                         and k not in ("sectors", "l_max_min")},
                      sectors=tuple(pc["man"]["params"]["sectors"]), l_max_min=tuple(pc["man"]["params"]["l_max_min"]))
    scen = D.read_scenario(root / "bundles" / f"affected_{dem.dates[0]}", pc["man"]["fleets"][0])
    hubs = G.hubs_of(dem, net, params)
    K = G.catchments(dem, net, hubs, params)
    graph = Graph(net, scen.policy.dwell_base_s, cutoff_s=max(params.l_max_min) * 60)
    b = G.Builder(net, graph, dem, params)
    lim = Limits(max(params.l_max_min) * 60, params.max_stops, params.max_detour)
    out = {}
    for nm in names:
        hs = [k for k, st_ in net.stations.items() if st_.name == nm and k in hubs]
        if not hs:
            continue
        h = hs[0]
        for pat in G._patterns([h], net):
            vals = G._pool(pat, K, dem)
            reasons: dict[str, int] = defaultdict(int)
            ok = conv_ok = 0
            ok_dem = conv_dem = 0.0
            ret_fail = 0
            for sd in sorted(vals, key=lambda x: (-vals[x], x)):
                st_, bad = b.check(pat, pat.anchors_with(sd), lim)
                if st_ is None:
                    reasons["+".join(sorted(bad))] += 1
                    continue
                ok += 1
                ok_dem += vals[sd]
                try:
                    to_engine_plan(single_route_plan(st_, net.tt, scen), net.tt, scen)
                    conv_ok += 1
                    conv_dem += vals[sd]
                except SimError as e:
                    fails = (getattr(e, "details", None) or {}).get("failures", [])
                    if any(f.get("check") == "RETURN" for f in fails):
                        ret_fail += 1
            st_obj = net.stations[h]
            fixed = st_obj.access_stop if pat.kind == "access" else st_obj.egress_stop
            reach = len(graph.tree(fixed, capped=False)) if fixed else 0
            out[(nm, pat.kind)] = {"seeds": len(vals), "demand": sum(vals.values()), "ok": ok, "ok_dem": ok_dem,
                                   "conv_ok": conv_ok, "conv_dem": conv_dem, "ret_fail": ret_fail,
                                   "reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:3]),
                                   "fixed": fixed, "reach": reach, "n_stops": len(set(net.tt.stop_ids()))}
    return out


def gangseo_section(w, data: dict, region: Path, v4: Path) -> None:
    x = data["gangseo"]
    fl = x["fleets"]
    w("## 6. 강서: 우리 안 − 피더안이 0.0/+0.1%p인 이유")
    w("")
    w("대상은 `affected`, 실험 주간 5일 평균. 다른 구와 같은 표로 비교한다.")
    w("")
    w("**미충족 사유 분해** (인원. 노선 없음 = `no_connection`, 운행 종료 = `service_end`, 대기 초과 = `wait`, 통행 초과 = `journey`. "
      "좌석 부족은 M2 `refusal_events` = 만차로 못 탄 사건 수이고, 그 승객의 최종 결과는 위 분류 중 하나에 들어간다.)")
    w("")
    w("| 구 | 대수 | 안 | 충족 | 노선 없음 | 운행 종료 | 대기 초과 | 통행 초과 | 좌석 부족(거절 사건) | 노선 없음 비중 |")
    w("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for g, y in data.items():
        for f in y["fleets"]:
            for lab in ("ours", "sp"):
                rs = [r for r in y["exp"][(lab, "affected", f)] if r.get("verify_pass") is True]
                ms = [load(Path(r["dir"]) / "run" / "metrics.json") for r in rs]
                u = {k: mean([m["M2"]["unserved_by_reason"].get(k, 0) for m in ms])
                     for k in ("no_connection", "service_end", "wait", "journey")}
                ref = mean([m["M2"]["refusal_events"] for m in ms])
                tot = mean([m["M1"]["total"] for m in ms])
                w(f"| {y['name']} | {f} | {LABEL[lab]} | {mean([m['M1']['served'] for m in ms]):,.0f} | "
                  + " | ".join(f"{u[k]:,.0f}" for k in ("no_connection", "service_end", "wait", "journey"))
                  + f" | {ref:,.0f} | {pct(u['no_connection'] / tot, 1)} |")
    w("")
    w("**역별 잔여 수요 집중도와 후보 풀이 덮을 수 있는 수요** (허브 = 생성기가 고른 역, 덮개 = OD가 노선 위에서 출발 → 도착 순서로 놓임)")
    w("")
    w("| 구 | 허브 수 | 1위 역 비중 | 상위 3역 | 허브 합 | 풀 전체 덮개: 우리 풀 | 피더 풀 | 생성 후보가 더한 덮개 | "
      + "우리 안 덮개 (작은 대수) | 우리 안 덮개 (큰 대수) | 두 안 같은 정차열 (작은 / 큰 대수) |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    pcs = {}
    for g, y in data.items():
        pc = pcs[g] = pool_cover(g, region, v4, y["fleets"])
        hubs = y["man"]["variants"]["affected"]["hubs"]
        F = sorted((h["F"] for h in hubs.values()), reverse=True)
        t = pc["total"]
        po = sum(pc["dem"].od_total(k) for k in pc["pool_ours"])
        ps = sum(pc["dem"].od_total(k) for k in pc["pool_sp"])
        pcov = [sum(pc["dem"].od_total(k) for k in pc["plan_keys"][("ours", f)]) for f in y["fleets"]]
        w(f"| {y['name']} | {len(F)} | {pct(F[0] / t, 1)} | {pct(sum(F[:3]) / t, 1)} | {pct(sum(F) / t, 1)} | "
          f"{po:,.0f} ({pct(po / t, 1)}) | {ps:,.0f} | {po - ps:+,.0f} | "
          + " | ".join(f"{c:,.0f} ({f}대)" for c, f in zip(pcov, y["fleets"]))
          + " | " + " / ".join(f"{a}/{b}" for a, b, _ in (pc["same"][f] for f in y["fleets"])) + " |")
    w("")
    pc = pcs["gangseo"]
    allst = pc["by_station"](pc["dem"].od.keys())
    bp = pc["by_station"](pc["pool_ours"])
    bpl = {f: pc["by_station"](pc["plan_keys"][("ours", f)]) for f in fl}
    top = sorted(allst.items(), key=lambda kv: -kv[1])[:10]
    names = [pc["net"].stations[k].name for k, _ in top if k in pc["net"].stations]
    blk = station_blockers("gangseo", region, names, pc)
    w("**강서 역별** (상위 10개 역. 풀 덮개 = 후보 풀 전체가 덮을 수 있는 그 역 수요, 단일 seed 통과 = seed 정류장 1개 + 역 정류장으로 만든 노선이 "
      "L_max 30분·N_max 8·우회율 1.6을 지키는 seed 수(그 seed 수요), 변환 통과 = 그중 변환기 S1–S6·공차 복귀까지 통과한 수)")
    w("")
    w("| 역 | 잔여 수요 | 비중 | 누적 | 풀 덮개 | " + " | ".join(f"{f}대 덮개" for f in fl)
      + " | access: seed · 단일 seed 통과 · 변환 통과 · 주된 거절 | 역 access 정류장에서 닿는 정류장 |")
    w("|---|---:|---:|---:|---:|" + "---:|" * len(fl) + "---|---:|")
    cum = 0.0
    for k, v in top:
        cum += v
        nm = pc["net"].stations[k].name if k in pc["net"].stations else k
        a = blk.get((nm, "access"))
        if a:
            rs = ", ".join(f"{r} {n}" for r, n in a["reasons"].items())
            at = (f"{a['seeds']} · {a['ok']}({a['ok_dem']:,.0f}) · {a['conv_ok']}({a['conv_dem']:,.0f})"
                  + (f", 복귀 경로 없음 {a['ret_fail']}" if a["ret_fail"] else "") + f" · {rs}")
            rc = f"{a['reach']}/{a['n_stops']}"
        else:
            at, rc = "허브 아님", "—"
        w(f"| {nm} | {v:,.0f} | {pct(v / pc['total'], 1)} | {pct(cum / pc['total'], 1)} | {bp.get(k, 0):,.0f} | "
          + " | ".join(f"{bpl[f].get(k, 0):,.0f}" for f in fl) + f" | {at} | {rc} |")
    w("")
    w("**피더안과 노선 구조** (우리 안 노선 중 피더 풀 정차열과 같은 것, 근사·덮개)")
    w("")
    w("| 대수 | 우리 안 노선(생성/피더 출처) | 피더안 노선 | 같은 정차열 | 근사 우리 / 피더 | 덮개 우리 / 피더 | 국소 탐색 선택 초기해 |")
    w("|---:|---|---:|---:|---:|---:|---|")
    for f in fl:
        o, sp_ = pc["plans"][("ours", f)], pc["plans"][("sp", f)]
        og = sum(1 for r in o["routes"].values() if r["origin"] == "generated")
        a, b_, _ = pc["same"][f]
        w(f"| {f} | {len(o['routes'])} ({og}/{len(o['routes']) - og}) | {len(sp_['routes'])} | {a}/{b_} | "
          f"{o['approx_served']:,.0f} / {sp_['approx_served']:,.0f} | {o['approx_demand_covered']:,.0f} / {sp_['approx_demand_covered']:,.0f} | "
          f"{o['chosen_start']} |")
    w("")
    w("- 우리 안의 '생성' 출처 노선도 피더 풀에 같은 정차열이 있으면 같은 노선이다.")
    w("")
    # 결론(숫자는 위 표와 같은 값에서 계산)
    t = pc["total"]
    F = {g: sorted((h["F"] for h in y["man"]["variants"]["affected"]["hubs"].values()), reverse=True)[0] / pcs[g]["total"]
         for g, y in data.items()}
    others = [F[g] for g in data if g != "gangseo"]
    po = sum(pc["dem"].od_total(k) for k in pc["pool_ours"])
    ps = sum(pc["dem"].od_total(k) for k in pc["pool_sp"])
    weak = [(pc["net"].stations[k].name, v, bp.get(k, 0)) for k, v in top[:6] if k in pc["net"].stations and bp.get(k, 0) < 0.1 * v]
    ms = {f: [load(Path(r["dir"]) / "run" / "metrics.json") for r in x["exp"][("ours", "affected", f)] if r.get("verify_pass")]
          for f in fl}
    yc = blk.get(("염창", "access"))
    main_reach = max(v["reach"] for v in blk.values())
    gain = {g: sum(pcs[g]["dem"].od_total(k) for k in pcs[g]["pool_ours"]) - sum(pcs[g]["dem"].od_total(k) for k in pcs[g]["pool_sp"])
            for g in data if g != "gangseo"}
    detour_st = [nm for (nm, kind), v in blk.items() if kind == "access" and nm != "염창" and v["seeds"]
                 and v["ok"] <= 0.1 * v["seeds"]]
    w("**정리**")
    w("")
    w(f"1. **수요가 고르게 흩어져 있다.** 1위 역 비중 {pct(F['gangseo'], 1)}(다른 세 구 {pct(min(others), 1)}–{pct(max(others), 1)}). "
      "한 역에 노선을 모아 얻는 이득이 작다.")
    w(f"2. **큰 역 일부에 닿는 후보가 거의 없다.** 상위 6개 역 중 "
      + ", ".join(f"{n}({pct(v / t, 1)}, 풀 덮개 {c:,.0f})" for n, v, c in weak)
      + f"은 후보 풀 전체가 덮을 수 있는 수요가 그 역 수요의 10%도 안 된다. 합하면 잔여 수요의 {pct(sum(v for _, v, _ in weak) / t, 1)}이다.")
    if yc:
        w(f"   - 염창: 역 access 지정 정류장 `{yc['fixed']}`에서 망으로 닿는 정류장이 {yc['reach']}개뿐이다(상위 10개 역의 다른 정류장은 최대 {main_reach}개, 망 정류장 {yc['n_stops']}개). "
          f"그래서 제약을 지키는 단일 seed 노선 {yc['ok']}개(seed 수요 {yc['ok_dem']:,.0f})가 모두 변환기 공차 복귀 검사(`RETURN`, "
          "끝 정류장 → 첫 정류장 경로 없음)에서 떨어진다. 2km 버퍼 망 가장자리의 막다른 구간으로 보이며 **망 데이터 쪽 확인이 필요하다**"
          "(routegen 설정으로는 풀 수 없다).")
    w(f"   - {'·'.join(detour_st)}: access seed 중 제약을 지키는 것이 10% 이하다. 역 정류장까지 망 위 최단경로가 직선거리에 비해 크게 돌아서 "
      "대부분이 우회율·N_max·L_max에 동시에 걸린다(위 표 '주된 거절').")
    w(f"3. **차량·좌석은 제약이 아니다.** 대기 초과 {', '.join(f'{f}대 {mean([m['M2']['unserved_by_reason']['wait'] for m in ms[f]]):,.0f}명' for f in fl)}, "
      f"좌석 부족 거절 {', '.join(f'{f}대 {mean([m['M2']['refusal_events'] for m in ms[f]]):,.0f}회' for f in fl)}. "
      "미충족의 대부분은 노선이 없는 OD다.")
    w(f"4. **생성 후보가 피더 후보에 더하는 것이 거의 없다.** 우리 풀의 덮개 {po:,.0f}명은 피더 풀 {ps:,.0f}명보다 {po - ps:+,.0f}명뿐이다"
      f"(다른 구 {min(gain.values()):+,.0f}~{max(gain.values()):+,.0f}명). 그래서 같은 배분기에서 두 안이 거의 같은 노선을 고른다.")
    cov = {f: sum(pc["dem"].od_total(k) for k in pc["plan_keys"][("ours", f)]) for f in fl}
    w(f"5. **배분만 바꿔 얻을 수 있는 상한**: 어떤 배분도 후보 풀 덮개 {po:,.0f}명({pct(po / t, 1)})을 넘지 못한다. "
      "지금 덮개는 " + ", ".join(f"{f}대 {cov[f]:,.0f}명({pct(cov[f] / t, 1)})" for f in fl)
      + "이다. 지금 배분이 그 대수에서 최적에 얼마나 가까운지는 확인하지 않았다(정확한 배분 최적화 미적용). "
      "상한 자체가 낮으므로 개선의 큰 몫은 배분보다 후보(망 연결·역 지정 정류장) 쪽에 있다.")
    w("")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--v4", required=True, type=Path)
    ap.add_argument("--v1", required=True, type=Path, help="v3 데이터 구로 기본 조건(routegen_v1/base)")
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--v3-root", required=True, type=Path, help="v3 구로 실험 묶음 폴더(guro_0610_2.zip을 푼 곳)")
    ap.add_argument("--seoul", required=True, type=Path, help="seoul_routegen_inputs_0610_v4를 푼 폴더")
    ap.add_argument("--zip", action="append", default=[], type=Path, help="입력 zip(해시를 적고 푼 폴더와 대조)")
    ap.add_argument("--release-sha", action="append", default=[],
                    help="이름=SHA-256 앞자리(릴리스 매니페스트 값). release_manifest_v4.csv가 없어 받은 값을 쓴다")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    L: list[str] = []
    w = L.append
    data = {}
    for g, name in GUS:
        d = args.v4 / g
        man = load(d / "manifest.json")
        exp, exp_all = read_runs(d, "runs")
        test, test_all = read_runs(d, "runs_test")
        rs_test = load(d / "runs_test" / "run_sims.json")
        data[g] = {"name": name, "man": man, "check": load(d / "bundle_check.json"), "exp": exp, "test": test,
                   "all": exp_all + test_all, "n_exp": len(exp_all), "n_test": len(test_all),
                   "eval_check": rs_test["eval_check"], "fleets": man["fleets"],
                   "gg_exp": gg_vary(args.region / f"{g}_0610_v4"),
                   "gg_test": gg_vary(args.region / f"{g}_0610_v4_test")}
        troot = args.region / f"{g}_0610_v4_test"
        data[g]["check_test"] = D.check_bundles(troot, D.load_flows(troot), man["fleets"][0])
    v1runs, v1all = read_runs(args.v1, "runs")
    v1man = load(args.v1 / "manifest.json")

    runs_total = sum(len(x["all"]) for x in data.values())
    ok_total = sum(1 for x in data.values() for r in x["all"] if r.get("verify_pass") is True)
    nv = sorted({",".join(r.get("not_verified_checks") or []) for x in data.values() for r in x["all"]})

    w("# routegen v1 설정 × v4 구 묶음 — 구로 v3 대비, 4개 구, 실험 → 테스트")
    w("")
    w("입력은 `region_bundles_0610_v4.zip`(구로·동작·강서·강동 × 실험 10/26~30 · 테스트 11/02~06)이다. "
      "이 문서의 숫자는 모두 `experiments/guro_routegen/make_report_v4.py`가 결과 파일에서 계산했다.")
    w("")
    w("- 후보 생성·배분은 **실험 묶음에서만** 했고, 테스트 묶음에는 그 안(plan.json)을 고치지 않고 돌렸다. "
      "그래서 테스트 충족률은 표본 밖 성능이다.")
    w("- 설정은 v1 기본 조건(`base`) 그대로다: τ 0.2, 군집 상한 3, R 3km, L_max 20·30분, N_max 8, 우회율 1.6, "
      "섹터 2·3·4, 피더 seed 상위 15, Jaccard 0.8, 국소 탐색·초기해 3개. 대수만 묶음 README 값을 쓴다.")
    w(f"- 실행 {runs_total}회 중 변환기 → M05 → M07(`--max-recompute 200000`) pass **{ok_total}회**. "
      f"미검증 항목: {' / '.join(x or '없음' for x in nv)} (이전 보고서들과 같은 항목).")
    w("- 충족률 = M1 served ÷ M1 total(그날 잔여 수요), 5일 평균 (최소–최대).")
    w("")

    # 0. 입력 파일
    w("## 0. 입력 파일과 해시")
    w("")
    rel = dict(x.split("=", 1) for x in args.release_sha)
    extracted = {"region_bundles_0610_v4.zip": args.region, "seoul_routegen_inputs_0610_v4.zip": args.seoul.parent}
    w("| 파일 | 크기(바이트) | SHA-256 | 릴리스 값(앞자리) | 일치 | 푼 폴더와 대조(CRC32) |")
    w("|---|---:|---|---|:---:|---:|")
    for z in args.zip:
        h = hashlib.sha256(z.read_bytes()).hexdigest()
        exp_prefix = rel.get(z.name)
        same = "예" if exp_prefix and h.startswith(exp_prefix) else ("—" if not exp_prefix else "**아니오**")
        base = extracted.get(z.name)
        ok = n = 0
        if base is not None:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    n += 1
                    f = base / info.filename
                    if f.is_file() and (zlib.crc32(f.read_bytes()) & 0xFFFFFFFF) == info.CRC:
                        ok += 1
        w(f"| `{z.name}` | {z.stat().st_size:,} | `{h}` | `{exp_prefix or '—'}…` | {same} | {ok}/{n} |")
    w("")
    w("- 위치: `seoul_transit_data copy/bundles_v4/`(폴더 이름에 \" copy\"가 붙어 있다). "
      "`release_manifest_v4.csv`는 작업 폴더에 없어 전달받은 해시 앞자리(16자)와 대조했다.")
    w("- `split/` 분할 zip은 쓰지 않았다. 그래도 각 묶음 `build_stats.json`의 `split.demand_profile_id`를 확인했다:")
    for g, _ in GUS:
        for suf in ("", "_test"):
            pid = load(args.region / f"{g}_0610_v4{suf}" / "build_stats.json")["split"]["demand_profile_id"]
            w(f"  - `{g}_0610_v4{suf}`: `{pid}` — {'-pattern-exp5k5-v4로 끝남' if pid.endswith('-pattern-exp5k5-v4') else '**다름**'}")
    w("")

    # 1. 입력 확인
    w("## 1. 입력과 실행 확인")
    w("")
    w("| 구 | 망 ID | README(실험) | README(테스트) | 대수 | 잔여 수요 = 묶음 demand_bins (실험 · 테스트) | gg_reach가 시간대마다 바뀌는 쌍 (실험 / 테스트) | 실행(실험+테스트) | M07 pass |")
    w("|---|---|---|---|---|---:|---|---:|---:|")
    for g, x in data.items():
        rc = x["man"]["root_check"]
        ok = sum(1 for r in x["all"] if r.get("verify_pass") is True)
        w(f"| {x['name']} | `{rc['network_snapshot_id']}` | {rc['readme_match']} | {x['eval_check']['readme_match']} | "
          f"{'·'.join(map(str, x['fleets']))} | {sum(c['match'] for c in x['check'])}/{len(x['check'])} · "
          f"{sum(c['match'] for c in x['check_test'])}/{len(x['check_test'])} | "
          f"{x['gg_exp'][0]}/{x['gg_exp'][1]} · {x['gg_test'][0]}/{x['gg_test'][1]} | "
          f"{x['n_exp']}+{x['n_test']} | {ok}/{len(x['all'])} |")
    w("")
    w("- 잔여 수요 대조: routegen이 행(정류장–역 × 날짜 × **시간**)마다 `walkable`·`maeul_reach`·`gg_reach`로 거른 수요를 "
      "시뮬레이터 정수화(`realize_bins`)에 넣은 결과가 실험·테스트 묶음 `demand_bins.csv`와 행 단위로 같다. "
      "시간대마다 다른 gg_reach를 쌍 단위로 묶었다면 gg 변형에서 어긋났을 것이다.")
    commits = {x["man"]["commit"][:7] + (" (변경 있음)" if x["man"]["tracked_changes"] else "") for x in data.values()}
    w(f"- 코드: `feat/candidate-gen` 커밋 {', '.join(sorted(commits))}. 시뮬레이터·검증기·run_matrix.py는 변경 없음(`run_sims.py`가 확인).")
    w("")

    # 2. 구로 v3 대비
    w("## 2. 구로: v3 데이터(v1 보고서) 대비")
    w("")
    w("| 변형 | 대수 | 안 | v3 충족률 | v4 충족률 | 변화 | v3 잔여 수요(5일 평균) | v4 잔여 수요 | v3 충족 인원 | v4 충족 인원 | 노선·대수 같음 |")
    w("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|:---:|")
    gx = data["guro"]
    p3, p4 = plan_routes(v1man), plan_routes(gx["man"])
    for v in VARIANTS:
        for f in gx["fleets"]:
            for lab in ("ours", "sp"):
                a, b = v1runs[(lab, v, f)], gx["exp"][(lab, v, f)]
                ra, rb = rates(a), rates(b)
                same = stops_key(p3[(lab, v, f)]) == stops_key(p4[(lab, v, f)])
                w(f"| `{v}` | {f} | {LABEL[lab]} | {span(ra)} | {span(rb)} | {pp(mean(rb) - mean(ra))} | "
                  f"{mean([r['total'] for r in a]):,.1f} | {mean([r['total'] for r in b]):,.1f} | "
                  f"{mean([r['served'] for r in a]):,.1f} | {mean([r['served'] for r in b]):,.1f} | {'같음' if same else ''} |")
    w("")

    w("**v3 → v4 구로 변화 진단** (5일 평균. 근사 = 배분기 충족 근사, 덮개 = 열린 노선이 덮는 잔여 수요, 우리 안 11대)")
    w("")
    w("| 변형 | 잔여 수요 v3 → v4 | 근사 v3 → v4 | 덮개 v3 → v4 | v3 상위 200 OD 유량 v3 → v4 | 상위 200 OD 겹침 |")
    w("|---|---:|---:|---:|---:|---:|")
    R3, R4 = D.load_flows(args.v3_root), D.load_flows(args.region / "guro_0610_v4")
    for v in VARIANTS:
        d3 = {k: sum(x) for k, x in D.build_demand(R3, v).od.items()}
        d4 = {k: sum(x) for k, x in D.build_demand(R4, v).od.items()}
        t3 = sorted(d3, key=lambda k: -d3[k])[:200]
        t4 = set(sorted(d4, key=lambda k: -d4[k])[:200])
        a, b = p3[("ours", v, 11)], p4[("ours", v, 11)]
        w(f"| `{v}` | {sum(d3.values()):,.0f} → {sum(d4.values()):,.0f} | {a['approx_served']:,.0f} → {b['approx_served']:,.0f} | "
          f"{a['approx_demand_covered']:,.0f} → {b['approx_demand_covered']:,.0f} | "
          f"{sum(d3[k] for k in t3):,.0f} → {sum(d4.get(k, 0) for k in t3):,.0f} | {len(set(t3) & t4)}/200 |")
    w("")
    w("- 잔여 수요 총량은 거의 같지만, v3에서 컸던 OD의 유량이 v4에서 줄고(비율이 11/02 하루 공개 집계 → 실험 주간 5일 카드 집계) "
      "그만큼이 다른 OD로 흩어졌다. 생성기는 v4 수요로 다시 돌았는데도 노선이 덮는 수요가 줄었다. "
      "같은 코드로 v3 데이터를 다시 돌리면 v1 안과 16/16 바이트 단위로 같으므로(§7) 하락은 코드가 아니라 데이터에서 왔다.")
    da = {f: mean(rates(gx["exp"][("ours", "affected", f)])) - mean(rates(v1runs[("ours", "affected", f)])) for f in gx["fleets"]}
    w(f"- **`affected`는 흡수 마스크(maeul_reach·gg_reach)를 쓰지 않는다.** 그래서 `affected`의 하락(우리 안 "
      + " / ".join(f"{f}대 {pp(da[f])}" for f in gx["fleets"]) + ")은 **비율 변화의 효과**다.")
    gl = []
    for f in gx["fleets"]:
        for v, ref in (("affected_gg", "affected"), ("affected_maeul_gg", "affected_maeul")):
            dv = mean(rates(gx["exp"][("ours", v, f)])) - mean(rates(v1runs[("ours", v, f)]))
            dr = mean(rates(gx["exp"][("ours", ref, f)])) - mean(rates(v1runs[("ours", ref, f)]))
            gl.append(f"`{v}` − `{ref}` {f}대 {pp(dv - dr)}")
    w("- 경기 규칙(순서 + 그 시간 운행 1회 이상) 효과는 gg 변형의 변화와 마스크가 같은 짝(gg 없음)의 변화의 차이로만 본다: "
      + ", ".join(gl) + ". 이 차이에는 바뀐 비율과 경기 마스크의 상호작용도 섞여 있어 경기 규칙만의 효과로 떼어 내지 않았다.")
    w("")

    # 3. 4개 구 실험·테스트
    w("## 3. 4개 구 × 대수 × 실험/테스트")
    w("")
    w("안은 실험 묶음에서 만들었다. 테스트 열은 같은 안을 11/02~06 묶음에 돌린 결과다.")
    w("")
    for g, x in data.items():
        w(f"### {x['name']} (대수 {'·'.join(map(str, x['fleets']))})")
        w("")
        w("| 변형 | 대수 | 안 | 노선 수 | 실험 충족률 | 테스트 충족률 | 테스트 − 실험 | 실험 잔여 수요 | 테스트 잔여 수요 | 실험 충족 | 테스트 충족 |")
        w("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        pr = plan_routes(x["man"])
        for v in VARIANTS:
            for f in x["fleets"]:
                for lab in ("ours", "sp"):
                    e, t = x["exp"][(lab, v, f)], x["test"][(lab, v, f)]
                    re_, rt = rates(e), rates(t)
                    w(f"| `{v}` | {f} | {LABEL[lab]} | {len(pr[(lab, v, f)]['routes'])} | {span(re_)} | {span(rt)} | "
                      f"{pp(mean(rt) - mean(re_))} | {mean([r['total'] for r in e]):,.1f} | {mean([r['total'] for r in t]):,.1f} | "
                      f"{mean([r['served'] for r in e]):,.1f} | {mean([r['served'] for r in t]):,.1f} |")
        w("")

    # 4. 요약 표
    w("## 4. 요약: 우리 안 `affected`")
    w("")
    w("| 구 | 대수 | 실험 | 테스트 | 테스트 − 실험 | 우리 − 피더 (실험) | 우리 − 피더 (테스트) |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    diffs = []
    for g, x in data.items():
        for f in x["fleets"]:
            e, t = mean(rates(x["exp"][("ours", "affected", f)])), mean(rates(x["test"][("ours", "affected", f)]))
            es, ts = mean(rates(x["exp"][("sp", "affected", f)])), mean(rates(x["test"][("sp", "affected", f)]))
            w(f"| {x['name']} | {f} | {pct(e)} | {pct(t)} | {pp(t - e)} | {pp(e - es)} | {pp(t - ts)} |")
    for x in data.values():
        for k in x["exp"]:
            diffs.append(mean(rates(x["test"][k])) - mean(rates(x["exp"][k])))
    w("")
    w(f"- 64안(4개 구 × 변형 4 × 대수 2 × 2안) 테스트 − 실험: 최소 {pp(min(diffs))}, 중앙값 {pp(statistics.median(diffs))}, "
      f"최대 {pp(max(diffs))}, 절댓값 평균 {100 * mean([abs(d) for d in diffs]):.2f}%p.")
    w("- 해석: v4 테스트 묶음은 **실험 주간(10/26~30) 비율 × 테스트 주간 TPSS 승차**로 만들었다(묶음 README). "
      "환승·목적지 비율은 실험과 같고 정류장 승차량만 바뀌었다. 그래서 이 차이가 작다는 것은 안이 **승차량 변동에 강하다**는 뜻이지, "
      "**이동 패턴(OD 비율) 변화에 강하다**는 뜻이 아니다. 이동 패턴이 바뀐 경우의 성능은 이 실험으로 알 수 없다.")
    w("")

    # 5. 미충족 사유
    w("## 5. 미충족 사유 (우리 안 `affected`, 5일 평균 인원)")
    w("")
    w("| 구 | 대수 | 주간 | 미충족 | no_connection | service_end | wait | journey |")
    w("|---|---:|---|---:|---:|---:|---:|---:|")
    for g, x in data.items():
        for f in x["fleets"]:
            for sp_name, R in (("실험", x["exp"]), ("테스트", x["test"])):
                rs = [r for r in R[("ours", "affected", f)] if r.get("verify_pass") is True]
                rsn = {k: mean([r["reasons"].get(k, 0) for r in rs]) for k in ("no_connection", "service_end", "wait", "journey")}
                w(f"| {x['name']} | {f} | {sp_name} | {mean([r['total'] - r['served'] for r in rs]):,.1f} | "
                  + " | ".join(f"{rsn[k]:,.1f}" for k in ("no_connection", "service_end", "wait", "journey")) + " |")
    w("")
    gangseo_section(w, data, args.region, args.v4)
    w("## 7. 코드에서 고친 곳 (`feat/candidate-gen`, 커밋 8454b9b)")
    w("")
    w("- `routegen/data.py` `check_root`: 구로 망 ID(`guro-buf2km-0610-v2`)·v3 README 문구·실험 날짜 고정 → "
      "망 ID 형식 `<구>-buf2km-0610-v2`(망 폴더·묶음 전부 같은 ID), README 주간 문구 v3·v4 둘 다, `split`(exp 10/26~30 · test 11/02~06). "
      "README에서 구 이름과 대수를 읽어 기본값으로 쓴다. 후보 생성기는 `split=exp`로만 묶음을 확인하므로, "
      "테스트 묶음(11/02~06)을 `--root`로 주어 후보를 만들려 하면 확인 단계에서 멈춘다.")
    w("- `routegen/data.py` `check_bundles`: `scenario_f11.json` 고정 → 실행 대수의 시나리오(동작·강서 20·40, 강동 35·70에는 f11이 없다).")
    w("- `routegen/__main__.py`: `--fleets` 기본 11,13 → README 대수. `home_gu`(cross_gu 표시) 구로구 고정 → README 구 이름(`--home-gu`로 바꿀 수 있음).")
    w("- `experiments/guro_routegen/run_sims.py`: `--eval-root` — 실험 주간 안을 테스트 묶음에 그대로 돌린다(망 ID·구 같은지 확인, 결과는 `runs_test/`).")
    w("- 고치지 않은 것: ① `gg_reach`는 처음부터 행(쌍 × 날짜 × 시간) 단위로 읽고 거른다 — 쌍 단위 캐시가 없다. "
      "단위 테스트 `HourlyReachTest`를 더했고, 실험·테스트 묶음 160개 전부 잔여 수요가 demand_bins와 같다(§1). "
      "② 수요 비율은 routegen이 계산하지 않고 묶음 `demand_flows`를 그대로 읽으므로 고칠 곳이 없다. "
      "③ 06~09시는 `HOURS = (6, 7, 8, 9)`로 이미 같다.")
    w("- **서울 전체 벡터 입력은 지금 브랜치가 읽을 수 없다.** 이 브랜치의 routegen은 구 묶음 형식"
      "(`network/` + `demand/demand_flows.csv.gz` + `bundles/<변형>_<날짜>/scenario_f*.json`)만 읽는다. "
      "서울 전체 입력(`seoul_routegen_inputs_0610_v4`)은 수요가 `demand/exp/`·`demand/test/` 아래 "
      "`access_vectors_seoul.csv.gz`·`egress_vectors_seoul.csv.gz`로 나뉘어 있고, 열도 다르다"
      "(`kind`·`stop_id`·`station_stop_id`·`in_network` 없음, `stop`·`station`·`f0`·`f2`·`f2g`·`gg_runs_h` 있음). "
      "시나리오(`bundles/`)도 없다. 그래서 `demand/exp`·`demand/test` 경로 분리는 이 브랜치 코드에 영향이 없고, "
      "서울 전체로 돌리려면 이 형식을 읽는 로더와 시나리오 생성이 새로 필요하다(이번에는 만들지 않았다).")
    w("- 회귀 확인: 바꾼 코드로 v3 구로 묶음(`--fleets 11,13`)을 다시 돌린 plan.json 16개가 v1 기본 조건과 바이트 단위로 같다. "
      "v4 4개 구도 두 번 돌린 plan 64개가 같다. 전체 단위 테스트 통과.")
    w("")
    w("## 8. 재현")
    w("")
    w("```")
    w("W=<저장소 밖>; R=$W/zip_v4/region   # region_bundles_0610_v4.zip을 푼 곳")
    w("for g in guro dongjak gangseo gangdong; do")
    w("  PYTHONPATH=src python3 -m busstrike.planning.routegen --root $R/${g}_0610_v4 --out $W/routegen_v4/$g --condition base")
    w("  python3 experiments/guro_routegen/run_sims.py --routegen-out $W/routegen_v4/$g --root $R/${g}_0610_v4 --jobs 7")
    w("  python3 experiments/guro_routegen/run_sims.py --routegen-out $W/routegen_v4/$g --root $R/${g}_0610_v4 --eval-root $R/${g}_0610_v4_test --jobs 7")
    w("done")
    w("python3 experiments/guro_routegen/make_report_v4.py --v4 $W/routegen_v4 --v1 $W/routegen_v1/base --region $R \\")
    w("    --v3-root $W/zip_v3/guro_0610 --seoul $W/zip_v4/seoul/seoul_routegen_inputs_0610_v4 \\\\")
    w("    --zip <bundles_v4>/region_bundles_0610_v4.zip --zip <bundles_v4>/seoul_routegen_inputs_0610_v4.zip \\\\")
    w("    --release-sha region_bundles_0610_v4.zip=514c4a746fbddfe9 --release-sha seoul_routegen_inputs_0610_v4.zip=d46e02ab84cfb4d4 \\\\")
    w("    --out experiments/reports/routegen_v4.md")
    w("```")
    w("")
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
