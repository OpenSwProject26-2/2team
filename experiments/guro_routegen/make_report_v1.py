"""routegen v1 결과(기본 조건 + 민감도) → 보고서 markdown. 숫자는 모두 결과 파일에서 읽어 센다.

    python3 experiments/guro_routegen/make_report_v1.py --v1 <routegen_v1> --v0 <routegen_v0/default> \
        --root <guro_0610 폴더> --data-label guro_0610_2.zip --out experiments/reports/guro_routegen_v1.md

읽는 것
- <v1>/run_v1.json, <v1>/{base,sens/*}/{manifest.json, candidates/affected*.json, runs/run_sims.json, runs/<변형>/runs.json}
- 실행별 run/{metrics.json, timetable.json}
- <v1>/municipal_check.json(check_municipal.py), <v1>/repeat_base/plans(결정론 재실행, 있으면)
- <v0>/{manifest.json, runs/<변형>/runs.json}(v0 대비 변화)
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

VARIANTS = ["affected", "affected_maeul", "affected_gg", "affected_maeul_gg"]
FLEETS = [11, 13]
LABEL = {"ours": "우리 안", "sp": "피더안"}
REASONS = ["no_connection", "service_end", "wait", "journey"]
CONSTRAINTS = ["unreachable", "repeat", "l_max", "max_stops", "detour"]
C_NAME = {"unreachable": "망에서 못 닿음", "repeat": "정류장 겹침(S2)", "l_max": "편도 L_max",
          "max_stops": "정차 수 N_max", "detour": "우회율 1.6", "no_path_or_repeat": "경로 없음·S2(v0 묶음)"}
MOVE_NAME = {"shift1": "1대 옮기기", "shift2": "2대 옮기기", "shift3": "3대 옮기기", "swap": "노선 교체",
             "close_reopt": "닫고 재배분"}
START_NAME = {"greedy": "탐욕", "top_demand": "수요 상위", "sp_plan": "피더안 배분"}
HORIZON = 14400
SEAT = 45


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def n(x: float, d: int = 1) -> str:
    if d == 0:
        return f"{int(round(x)):,}"
    return f"{x:,.{d}f}"


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}%"


def span_pct(xs: list[float]) -> str:
    avg = sum(xs) / len(xs)
    return f"{pct(avg)} ({pct(min(xs))}–{pct(max(xs))})"


def pp(x: float) -> str:
    return f"{100 * x:+.2f}%p"


def nmax_label(v) -> str:
    return "제한 없음" if v is None else str(v)


# ---------------------------------------------------------------- 결과 읽기

def read_runs(cond_dir: Path) -> dict[tuple, list[dict]]:
    """(label, variant, fleet) → 날짜순 실행 기록(metrics·timetable 요약 포함)."""
    out: dict[tuple, list[dict]] = defaultdict(list)
    for rj in sorted((cond_dir / "runs").glob("*/runs.json")):
        R = load(rj)
        for r in R["runs"]:
            rec = dict(r)
            d = Path(r["dir"])
            if r.get("verify_pass") is True:
                m = load(d / "run" / "metrics.json")
                tt = load(d / "run" / "timetable.json")
                rec["served"] = m["M1"]["served"]
                rec["total"] = m["M1"]["total"]
                rec["censored"] = m["M1"]["censored"]
                rec["reasons"] = m["M2"]["unserved_by_reason"]
                rec["period"] = {x["route_id"]: x["period_s"] for x in tt["routes"]}
            out[(r["label"], r["variant"], r["fleet"])].append(rec)
    for k in out:
        out[k].sort(key=lambda x: x["date"])
    return out


def rates(rs: list[dict]) -> list[float]:
    return [r["served"] / r["total"] for r in rs]


def theo_max(plan: dict) -> float:
    return sum(r["vehicles"] * SEAT * HORIZON / r["cycle_s"] for r in plan["routes"].values())


def cand_stats(cdoc: dict) -> dict:
    cands = cdoc["candidates"]
    sp_seq = {tuple(x["stops"]) for x in cands if x["origin"] == "shortest_path_feeder"}
    gen = [x for x in cands if x["origin"] == "generated"]
    R = cdoc["reasons"]
    ok = sum(v for k, v in R.items() if k.startswith("insertion") and k.endswith("/ok"))
    rej = sum(v for k, v in R.items() if k.startswith("insertion") and k.endswith("/rejected"))
    return {"n": len(cands), "gen": len(gen), "gen_same": sum(tuple(x["stops"]) in sp_seq for x in gen),
            "ins_ok": ok, "ins_n": ok + rej, "mean_c": statistics.mean(x["cycle_s"] for x in cands),
            "pool_ours": len(cdoc["pool_members"]["ours"]), "pool_sp": len(cdoc["pool_members"]["sp"])}


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def reason_table(R: dict, RS: dict, stage_prefix: str, kinds: tuple[str, ...]) -> dict:
    tries = ok = 0
    per = Counter()
    sole = Counter()
    combos = Counter()
    for k, v in R.items():
        st, kind, r = k.split("/")
        if not st.startswith(stage_prefix) or kind not in kinds:
            continue
        if r == "ok":
            ok += v
            tries += v
        elif r == "rejected":
            tries += v
        else:
            per[r] += v
    for k, v in RS.items():
        st, kind, combo = k.split("/")
        if not st.startswith(stage_prefix) or kind not in kinds:
            continue
        combos[combo] += v
        if "+" not in combo:
            sole[combo] += v
    return {"tries": tries, "ok": ok, "per": per, "sole": sole, "combos": combos}


# ---------------------------------------------------------------- 본문

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True, type=Path)
    ap.add_argument("--v0", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--data-label", default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    v1 = args.v1.resolve()
    runlog = load(v1 / "run_v1.json")
    conds = runlog["conditions"]
    base_dir = v1 / "base"
    man = {c["name"]: load(v1 / c["dir"] / "manifest.json") for c in conds}
    cand = {(c["name"], v): load(v1 / c["dir"] / "candidates" / f"{v}.json")
            for c in conds for v in man[c["name"]]["variants"]}
    runs = {c["name"]: read_runs(v1 / c["dir"]) for c in conds}
    sims = {c["name"]: load(v1 / c["dir"] / "runs" / "run_sims.json") for c in conds}
    plans = {c["name"]: {(p["label"], p["variant"], p["fleet"]): p for p in man[c["name"]]["plans"]} for c in conds}
    sens = [c for c in conds if c["name"] != "base"]
    v0man = load(args.v0 / "manifest.json")
    v0plans = {(p["label"], p["variant"], p["fleet"]): p for p in v0man["plans"]}
    v0runs = read_runs(args.v0)
    muni = load(v1 / "municipal_check.json") if (v1 / "municipal_check.json").exists() else None

    all_runs = [r for cr in runs.values() for rs in cr.values() for r in rs]
    ok_runs = [r for r in all_runs if r.get("verify_pass") is True]
    envs = Counter(json.dumps(r.get("engine_env"), sort_keys=True, ensure_ascii=False) for r in all_runs)
    not_verified = Counter(tuple(r.get("not_verified_checks") or []) for r in all_runs)
    c_eq = c_n = 0
    for cname, cr in runs.items():
        for key, rs in cr.items():
            p = plans[cname][key]
            for r in rs:
                if "period" not in r:
                    continue
                for rid, rr in p["routes"].items():
                    c_n += 1
                    c_eq += int(r["period"].get(rid) == rr["cycle_s"])
    ours_ge_sp = [(cname, v, f, plans[cname][("ours", v, f)]["approx_served"], plans[cname][("sp", v, f)]["approx_served"])
                  for cname in plans for (lab, v, f) in plans[cname] if lab == "ours"]
    ge_bad = [x for x in ours_ge_sp if x[3] < x[4] - 1e-6]

    def avg_served(cname, key):
        rs = runs[cname][key]
        return statistics.mean(r["served"] for r in rs)

    L: list[str] = []
    w = L.append

    # ------------------------------------------------------------ 요약
    w("# 구로구 후보 노선 생성 + 구 단독 배분 v1 — 국소 탐색 수정·N_max·R 민감도")
    w("")
    w("실험 데이터(10/26~30) 5일 묶음 결과다. 이 문서의 숫자는 모두 `experiments/guro_routegen/make_report_v1.py`가 결과 파일"
      "(`manifest.json`, `candidates/*.json`, `runs.json`, 실행별 `metrics.json`·`timetable.json`)에서 계산했다. "
      "보고서를 손으로 고치지 않고 스크립트를 고친다.")
    w("")
    w("## 요약")
    w("")
    w(f"- 실행 {len(all_runs)}회(조건 {len(conds)}개 · 안 {sum(len(p) for p in plans.values())}개 × 5일) 중 "
      f"변환기·M05·M07 pass **{len(ok_runs)}회**. 후보 주기 C = 시뮬레이터 `period_s` {c_eq}/{c_n}건.")
    # 기본 조건 요약
    b_ours = {(v, f): statistics.mean(rates(runs["base"][("ours", v, f)])) for v in VARIANTS for f in FLEETS}
    b_sp = {(v, f): statistics.mean(rates(runs["base"][("sp", v, f)])) for v in VARIANTS for f in FLEETS}
    v0_ours = {(v, f): statistics.mean(rates(v0runs[("ours", v, f)])) for v in VARIANTS for f in FLEETS}
    better = [(v, f) for v in VARIANTS for f in FLEETS if b_ours[(v, f)] > b_sp[(v, f)] + 1e-12]
    same_plan = [(v, f) for v in VARIANTS for f in FLEETS
                 if _same_routes(plans["base"][("ours", v, f)], plans["base"][("sp", v, f)])]
    w(f"- **국소 탐색 수정(1-1)**: 노선 교체·2~3대 옮기기·초기해 3개(탐욕·수요 상위·피더안 배분)를 넣었다. "
      f"우리 안 근사 ≥ 같은 설정 피더안 근사: {len(ours_ge_sp) - len(ge_bad)}/{len(ours_ge_sp)}안. "
      f"기본 조건에서 시뮬레이터 충족률이 피더안보다 높은 조건 {len(better)}/8, 두 안이 같은 조건 {len(same_plan)}/8.")
    gains = [b_ours[k] - v0_ours[k] for k in b_ours]
    w(f"- 기본 조건 우리 안 충족률 v0 대비 변화: {min(gains) * 100:+.2f}%p ~ {max(gains) * 100:+.2f}%p "
      f"(8조건 평균 {statistics.mean(gains) * 100:+.2f}%p).")
    summary_at = len(L)

    # ------------------------------------------------------------ 1 입력
    w("")
    w("## 1. 입력과 실행 확인")
    w("")
    rc = runlog["root_check"]
    w(f"- 실험 묶음 확인(`routegen.data.check_root`, 생성·시뮬레이션·지자체안 확인 스크립트 맨 앞에서 실행): 망 ID "
      f"`{rc['network_snapshot_id']}`(망 폴더와 묶음 {rc['bundles']}개 전부), README 문구 \"{rc['readme_match']}\", "
      f"묶음 날짜 {rc['dates'][0]}~{rc['dates'][-1]} × 변형 {len(rc['variants'])}개. 하나라도 다르면 멈춘다"
      "(망 v1 자료로 돌리면 멈추는 것을 확인했다 — 단위 테스트 `CheckRootTest`).")
    w(f"- 입력 zip: `{args.data_label or '?'}`에서 푼 폴더. 망 ID가 `guro-buf2km-0610-v2`인 파일이고, 사용자가 이름을 "
      "`guro_0610.zip`으로 정리하기로 했다(정리 전 실행이라 실행 기록의 data_label은 이전 이름이다).")
    w("- 테스트 묶음(`guro_0610_test`)은 열지 않았다. v0 결과(`routegen_v0/`)는 그대로 두고 v1은 `routegen_v1/`에 저장했다.")
    s0 = sims["base"]
    w(f"- 코드: 브랜치 `{s0['repo_branch']}` 커밋 `{s0['repo_commit'][:7]}` + 커밋 전 v1 변경. 시뮬레이터·검증기"
      f"(`src/busstrike/sim`, `src/busstrike/evaluation`) 추적 파일 변경 "
      f"{'없음' if not any(s['src_tracked_changes'] for s in sims.values()) else '있음'}.")
    w(f"- 실행기: 이 작업 폴더의 `experiments/guro_exp/run_matrix.py`(커밋됨, sha256 `{s0['run_matrix_sha256'][:16]}…`, "
      f"조건 {len(sims)}개 실행 모두 같은 해시: {len({s['run_matrix_sha256'] for s in sims.values()}) == 1}). "
      "다른 체크아웃의 파일은 부르지 않았다.")
    w(f"- 엔진 기록(실행별 `engine_env`): " + "; ".join(f"{n_}회 `{e}`" for e, n_ in envs.items()) + ".")
    w("  - `dirty: true`는 저장소 전체 기준이다. 실행 때 커밋 전이던 v1 변경(`src/busstrike/planning/routegen/`, "
      "`experiments/guro_routegen/`, 테스트) 때문이고, 시뮬레이터·검증기 추적 파일 변경은 조건 "
      f"{sum(not s_['src_tracked_changes'] for s_ in sims.values())}/{len(sims)}개 실행 모두 없음으로 기록됐다(`run_sims.json`).")
    code_now = {f.name: _sha(f) for f in sorted((Path(__file__).resolve().parents[2] / "src" / "busstrike" / "planning"
                                                / "routegen").glob("*.py"))}
    code_eq = [c["name"] for c in conds if man[c["name"]]["code_sha256"] == code_now]
    w(f"  - 실행 때 routegen 코드(`manifest.json`의 `code_sha256`)와 지금 작업 폴더 파일의 sha256이 같은 조건: "
      f"{len(code_eq)}/{len(conds)}개.")
    w(f"- M07 미검증 항목: " + "; ".join(f"{','.join(k) or '없음'} {n_}회" for k, n_ in not_verified.items())
      + " (이전 보고서들과 같은 항목).")
    w("- 운영 종료 규칙: 지금 규칙(main 324ae0c) 그대로. `fix/service-end-rule`은 섞지 않았다. service_end가 커도 그대로 적는다.")
    rep = v1 / "repeat_base" / "plans"
    if rep.exists():
        same = sum((rep / Path(p["file"]).name).read_bytes() == Path(p["file"]).read_bytes()
                   for p in man["base"]["plans"])
        w(f"- 결정론: 기본 조건 생성·배분을 한 번 더 돌린 plan.json {same}/{len(man['base']['plans'])}개가 바이트 단위로 같다.")
    if "N8_R3000" in plans:
        same = sum(Path(plans["N8_R3000"][k]["file"]).read_bytes() == Path(plans["base"][k]["file"]).read_bytes()
                   for k in plans["N8_R3000"])
        w(f"- 민감도 조건 N_max 8·R 3km는 기본 조건과 설정이 같다: affected plan {same}/{len(plans['N8_R3000'])}개가 바이트 단위로 같다.")
    w("")

    # ------------------------------------------------------------ 2 변경
    w("## 2. v0 → v1에서 바뀐 것")
    w("")
    w("**알고리즘(기본 조건에도 적용)**")
    w("")
    w("- 국소 탐색: 매 회차 `1대·2대·3대 옮기기`(주는 노선은 1대 이상 남김)와 `노선 교체`(열린 노선 하나를 닫고 안 열린 후보를 "
      "n0 = ⌈C/1200⌉대로 연다. 모자라면 다른 노선에서 1대씩 — 뺄 때 근사 손실이 가장 작은 노선부터, 1대는 남김 — 가져오고 남으면 "
      "이득 큰 노선에 1대씩)를 모두 평가해 가장 좋은 개선 하나를 받는다. 개선이 없을 때만 v0의 `닫고 재배분`을 시도한다.")
    w("- 초기해 3개: 탐욕(CELF) + 남는 대수 채우기, 수요 상위 노선부터 n0씩, 같은 설정 피더안의 최종 배분. 각각 국소 탐색 후 근사 최대를 "
      "고른다(동률이면 이 순서). 피더안 배분은 우리 풀의 같은 정차열 후보로 옮겨 넣는다. 피더 풀 정차열이 우리 풀에 없으면 그 후보를 "
      "우리 풀에 더한다(이번 실행에서 더한 후보 수: "
      + ", ".join(f"{c['name']} {sum(len(i['sp_routes_added_to_ours']) for i in man[c['name']]['variants'].values())}"
                  for c in conds) + ").")
    w("- 우리 안 근사 < 피더안 근사이면 생성기가 멈춘다. 단위 테스트 `test_result_not_below_feeder_plan`(임의 사례 24개)과 "
      "`test_feeder_start_lifts_stuck_case`(탐욕·수요 상위 초기해만으로는 피더안보다 낮아지는 사례)가 이것을 확인한다.")
    w("- 피더안도 같은 배분기(초기해: 탐욕·수요 상위)로 다시 배분했으므로 v0 피더안과 대수가 다를 수 있다.")
    w("- 거절 사유 진단: 최단경로를 못 잇거나 정류장이 겹쳐 v0에서 `no_path_or_repeat` 하나로 묶이던 판정을, 상한 없는 최단경로로 "
      "이어 붙인 정차열에 제약을 모두 매겨 제약별로 센다(결과 후보에는 영향 없음 — v0와 기본 조건의 후보·삽입 시도 수가 같다). "
      "단계 이름에 L_max를 붙였다(`seed_L20`, `insertion_L30` …).")
    w("")
    w("**파라미터**")
    w("")
    w("- 기본 조건(`base`)은 v0 기본값 그대로다: τ 0.2, τ_step 0.05, 군집 상한 3, R 3km, F_min 540, L_max 20·30분, N_max 8, "
      "우회율 1.6, 섹터 2·3·4, 피더 seed 상위 15, Jaccard 0.8.")
    w("- 민감도(`sens/N{N}_R{R}`, affected만)는 지시대로 N_max ∈ {8, 12, 제한 없음} × R ∈ {1.5, 2, 3}km만 바꿨다. "
      "τ 0.2·군집 상한 3을 포함한 나머지는 기본값이다. 결과를 보고 다른 파라미터를 바꾼 것은 없다.")
    w("")

    # ------------------------------------------------------------ 3 표 1
    w("## 3. 기본 조건 8안 (표 1)")
    w("")
    w("시뮬레이터 충족률 = 충족 ÷ 잔여 수요(날마다), 5일 평균(최소–최대). v0 열은 v0 보고서와 같은 실행 결과에서 다시 계산했다.")
    w("")
    w("| 변형 | 대수 | 우리 안 | 피더안 | 우리−피더 | v0 우리 안 | v0 피더안 | 우리 안 v0 대비 | 피더안 v0 대비 | 두 안 같음 |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|")
    for v in VARIANTS:
        for f in FLEETS:
            o, s = rates(runs["base"][("ours", v, f)]), rates(runs["base"][("sp", v, f)])
            o0, s0_ = rates(v0runs[("ours", v, f)]), rates(v0runs[("sp", v, f)])
            same = (v, f) in same_plan
            w(f"| `{v}` | {f} | {span_pct(o)} | {span_pct(s)} | {pp(statistics.mean(o) - statistics.mean(s))} | "
              f"{pct(statistics.mean(o0))} | {pct(statistics.mean(s0_))} | {pp(statistics.mean(o) - statistics.mean(o0))} | "
              f"{pp(statistics.mean(s) - statistics.mean(s0_))} | {'같음' if same else ''} |")
    w("")
    changed = [(lab, v, f) for (lab, v, f), p in plans["base"].items()
               if not _same_routes(p, v0plans[(lab, v, f)])]
    w(f"- v0와 정차열·대수가 달라진 안: {len(changed)}/16 — "
      + (", ".join(f"{LABEL[lab]} `{v}` {f}대" for lab, v, f in sorted(changed)) or "없음") + ".")
    w("")
    w("**근사·κ·용량 상한** (근사 = 배분기의 충족 근사 v2, κ = 1. κ = 시뮬레이터 충족 5일 평균 ÷ 근사. "
      "이론 최대 = Σ_r n_r × 45 × 14400 / C_r — 4시간 동안 모든 운행이 만석일 때)")
    w("")
    w("| 안 | 노선 수 | 근사 | 시뮬레이터 충족(5일 평균) | κ | 이론 최대 | 충족 ÷ 이론 최대 |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for v in VARIANTS:
        for f in FLEETS:
            for lab in ("ours", "sp"):
                p = plans["base"][(lab, v, f)]
                sv = avg_served("base", (lab, v, f))
                tm = theo_max(p)
                w(f"| {LABEL[lab]} `{v}` {f}대 | {len(p['routes'])} | {n(p['approx_served'])} | {n(sv)} | "
                  f"{sv / p['approx_served']:.3f} | {n(tm, 0)} | {pct(sv / tm)} |")
    kap = [avg_served("base", k) / plans["base"][k]["approx_served"] for k in plans["base"]]
    eff = [avg_served("base", k) / theo_max(plans["base"][k]) for k in plans["base"]]
    w("")
    w(f"- κ 범위 {min(kap):.3f}–{max(kap):.3f}(중앙값 {statistics.median(kap):.3f}). "
      f"충족 ÷ 이론 최대 {pct(min(eff))}–{pct(max(eff))}.")
    w("")
    w("**미충족 사유**(5일 평균 인원; M2 `unserved_by_reason`, 우선순위 no_connection → service_end → journey → wait)")
    w("")
    w("| 안 | 미충족 | no_connection | service_end | wait | journey |")
    w("|---|---:|---:|---:|---:|---:|")
    for v in VARIANTS:
        for f in FLEETS:
            for lab in ("ours", "sp"):
                rs = runs["base"][(lab, v, f)]
                tot = statistics.mean(r["total"] - r["served"] for r in rs)
                cells = [n(statistics.mean(r["reasons"].get(x, 0) for r in rs)) for x in REASONS]
                w(f"| {LABEL[lab]} `{v}` {f}대 | {n(tot)} | " + " | ".join(cells) + " |")
    w("")
    gap = sum(plans["base"][k]["approx_served"] - avg_served("base", k) for k in plans["base"])
    se_w = sum(statistics.mean(r["reasons"].get("service_end", 0) + r["reasons"].get("wait", 0) for r in runs["base"][k])
               for k in plans["base"])
    w(f"- 16안 합: 근사 − 시뮬레이터 = {n(gap)}명, service_end + wait = {n(se_w)}명. no_connection은 노선이 덮지 않는 "
      "OD(근사에서도 0)라 κ 차이를 설명하지 않는다.")
    w("")

    # ------------------------------------------------------------ 4 표 3
    w("## 4. 국소 탐색 (표 3)")
    w("")
    w("이동 종류별 `수락/시도`(초기해 3개 또는 2개의 국소 탐색을 합친 수)와 초기해별 국소 탐색 후 근사. ★ = 고른 초기해.")
    w("")
    w("| 조건 | 안 | " + " | ".join(MOVE_NAME[m] for m in MOVE_NAME) + " | 탐욕 | 수요 상위 | 피더안 배분 | 최종 근사 |")
    w("|---|---|" + "---:|" * (len(MOVE_NAME) + 4))
    for c in conds:
        for key in sorted(plans[c["name"]], key=lambda k: (VARIANTS.index(k[1]), k[2], k[0] != "ours")):
            p = plans[c["name"]][key]
            lab, v, f = key
            ms = p["move_stats"]
            cells = [f"{ms[m]['accepts']}/{ms[m]['tries']:,}" for m in MOVE_NAME]
            st = []
            for sname in START_NAME:
                if sname in p["starts"]:
                    x = p["starts"][sname]
                    st.append(f"{n(x['initial'])} → {n(x['final'])}{' ★' if p['chosen_start'] == sname else ''}")
                else:
                    st.append("")
            w(f"| {c['name']} | {LABEL[lab]} `{v}` {f}대 | " + " | ".join(cells) + " | " + " | ".join(st)
              + f" | {n(p['approx_served'])} |")
    w("")
    tot = Counter()
    for c in conds:
        for p in plans[c["name"]].values():
            for m, x in p["move_stats"].items():
                tot[(m, "a")] += x["accepts"]
                tot[(m, "t")] += x["tries"]
    w("- 전체 합: " + ", ".join(f"{MOVE_NAME[m]} {tot[(m, 'a')]}/{tot[(m, 't')]:,}" for m in MOVE_NAME) + ".")
    lifted = [(c["name"], k) for c in conds for k, p in plans[c["name"]].items()
              if k[0] == "ours" and p["chosen_start"] == "sp_plan"
              and max(p["starts"][s]["final"] for s in ("greedy", "top_demand")) < p["starts"]["sp_plan"]["final"] - 1e-6]
    stuck = [(c["name"], k) for c in conds for k, p in plans[c["name"]].items()
             if k[0] == "ours" and max(p["starts"][s]["final"] for s in ("greedy", "top_demand"))
             < plans[c["name"]][("sp", k[1], k[2])]["approx_served"] - 1e-6]
    w(f"- 탐욕·수요 상위 초기해만으로는 피더안 근사보다 낮게 끝난 우리 안: {len(stuck)}개"
      + (" — " + ", ".join(f"{cn} `{k[1]}` {k[2]}대" for cn, k in stuck) if stuck else "")
      + f". 피더안 배분 초기해가 최종 해가 된 우리 안(다른 초기해보다 엄밀히 큼): {len(lifted)}개.")
    v0_aff13 = v0plans[("ours", "affected", 13)]["approx_served"], v0plans[("sp", "affected", 13)]["approx_served"]
    w(f"- v0에서 문제였던 `affected` 13대: v0 근사 우리 안 {n(v0_aff13[0])} < 피더안 {n(v0_aff13[1])} → v1 우리 안 "
      f"{n(plans['base'][('ours', 'affected', 13)]['approx_served'])}, 피더안 {n(plans['base'][('sp', 'affected', 13)]['approx_served'])}.")
    w("")

    # ------------------------------------------------------------ 5 표 2
    w("## 5. 민감도: N_max × R (affected, 표 2)")
    w("")
    w("충족률은 5일 평균(최소–최대). 후보 수 = 생성 + 피더(중복 제거 전). 피더와 같은 비율 = 생성 후보 중 정차열이 피더 후보와 같은 것. "
      "삽입 통과율 = 섹터 삽입 시도 중 제약을 지킨 비율(L_max 20·30 합). 평균 C = 후보 전체의 주기 평균, 노선 C = 우리 안 노선의 대수 가중 평균.")
    w("")
    w("| N_max | R | 대수 | 우리 안 | 피더안 | 우리−피더 | 후보 수 | 피더와 같은 비율 | 삽입 통과율 | 평균 C(분) | 노선 C(분) | κ | 충족 ÷ 이론 최대 |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    score = {}
    for c in sens:
        cs = cand_stats(cand[(c["name"], "affected")])
        for f in FLEETS:
            ko, ks = ("ours", "affected", f), ("sp", "affected", f)
            o, s = rates(runs[c["name"]][ko]), rates(runs[c["name"]][ks])
            p = plans[c["name"]][ko]
            sv = avg_served(c["name"], ko)
            nc = sum(r["vehicles"] * r["cycle_s"] for r in p["routes"].values()) / sum(r["vehicles"] for r in p["routes"].values())
            w(f"| {nmax_label(man[c['name']]['params']['max_stops'])} | {c['radius_m'] / 1000:g}km | {f} | {span_pct(o)} | "
              f"{span_pct(s)} | {pp(statistics.mean(o) - statistics.mean(s))} | {cs['n']} | "
              f"{pct(cs['gen_same'] / cs['gen'])} ({cs['gen_same']}/{cs['gen']}) | {pct(cs['ins_ok'] / cs['ins_n'])} | "
              f"{cs['mean_c'] / 60:.1f} | {nc / 60:.1f} | {sv / p['approx_served']:.3f} | {pct(sv / theo_max(p))} |")
        score[c["name"]] = statistics.mean(statistics.mean(rates(runs[c["name"]][("ours", "affected", f)])) for f in FLEETS)
    w("")
    # R별 O
    w("**R별 겹침 O와 실제 쓰인 τ**(허브 쌍 전체의 O_ij, τ는 군집마다 — 상한 3을 넘어 올린 값 포함)")
    w("")
    w("| R | 허브 | 허브 쌍 | O 중앙값 | O 90% | O ≥ 0.2 쌍 | 군집 | 군집별 τ | 권역 정류장 수(허브 평균) |")
    w("|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    for r in sorted({c["radius_m"] for c in sens}):
        c = next(x for x in sens if x["radius_m"] == r)
        vi = man[c["name"]]["variants"]["affected"]
        O = list(vi["overlaps"].values())
        taus = Counter(cl["tau"] for cl in vi["clusters"])
        w(f"| {r / 1000:g}km | {len(vi['hubs'])} | {len(O)} | {quantile(O, 0.5):.3f} | {quantile(O, 0.9):.3f} | "
          f"{sum(o >= 0.2 for o in O)} | {len(vi['clusters'])} | "
          + ", ".join(f"{t:g}×{k}" for t, k in sorted(taus.items())) + f" | "
          f"{statistics.mean(h['catchment'] for h in vi['hubs'].values()):.0f} |")
    w("")
    w("(O·군집은 N_max와 무관하다 — 같은 R의 세 조건에서 같다: "
      + str(all(man[a["name"]]["variants"]["affected"]["clusters"] == man[b["name"]]["variants"]["affected"]["clusters"]
                for a in sens for b in sens if a["radius_m"] == b["radius_m"])) + ")")
    w("")
    # N_max: 삽입 거절의 단독 원인
    w("**삽입 거절 사유**(섹터 삽입 시도마다. 사유는 겹쳐 붙는다. 단독 = 그 제약 하나만 걸린 시도)")
    w("")
    w("| N_max | R | 시도 | 통과 | " + " | ".join(f"{C_NAME[x]} (단독)" for x in CONSTRAINTS) + " |")
    w("|---|---:|---:|---:|" + "---:|" * len(CONSTRAINTS))
    for c in sens:
        cd = cand[(c["name"], "affected")]
        t = reason_table(cd["reasons"], cd["reason_sets"], "insertion", ("access", "egress", "connect"))
        w(f"| {nmax_label(man[c['name']]['params']['max_stops'])} | {c['radius_m'] / 1000:g}km | {t['tries']:,} | {t['ok']:,} | "
          + " | ".join(f"{t['per'][x]:,} ({t['sole'][x]:,})" for x in CONSTRAINTS) + " |")
    w("")
    # 피더 seed 밀려남
    w("**N_max를 풀면 피더 후보가 바뀌는 이유**: 피더는 허브·방향마다 수요 상위 seed 중 제약을 지키는 **처음 15개**다. "
      "N_max를 풀면 전에는 정차 수에 걸리던 수요 큰 seed가 들어오고 그만큼 다른 seed가 밀려난다.")
    w("")
    w("| R | N_max | 피더 후보 | N_max 8 대비 빠진 피더 seed | 새로 들어온 피더 seed | 새 피더의 정차 수(역 제외) |")
    w("|---:|---|---:|---:|---:|---|")
    for r in sorted({c["radius_m"] for c in sens}):
        ref = next(c for c in sens if c["radius_m"] == r and man[c["name"]]["params"]["max_stops"] == 8)
        def seeds(cname):
            out = {}
            for x in cand[(cname, "affected")]["candidates"]:
                if x["origin"] == "shortest_path_feeder":
                    for t in x["made_by"]:
                        out[(x["kind"], tuple(x["hubs"]), t)] = x["n_stops"]
            return out
        s8 = seeds(ref["name"])
        for c in sens:
            if c["radius_m"] != r or c is ref:
                continue
            sx = seeds(c["name"])
            new = [sx[k] for k in sx if k not in s8]
            w(f"| {r / 1000:g}km | {nmax_label(man[c['name']]['params']['max_stops'])} | {len(sx)} | "
              f"{sum(1 for k in s8 if k not in sx)} | {len(new)} | "
              + (", ".join(f"{k}개×{m}" for k, m in sorted(Counter(new).items())) or "—") + " |")
    w("")
    # 피더안·우리 안 노선 중 바뀐 피더 seed를 쓴 것
    w("")

    # ------------------------------------------------------------ 6 편도 연결
    w("## 6. 편도 연결이 거의 만들어지지 않는 이유 (1-4, 기본 조건)")
    w("")
    w("편도 연결 패턴(역 i egress 정류장 → 정류장들 → 역 j access 정류장)의 seed 판정. seed 하나만 넣은 정차열이 제약을 지키는지다"
      "(패턴 × L_max × 권역 정류장마다 1회). 고치지 않고 센 것만 적는다.")
    w("")
    w("| 변형 | L_max | 시도 | 통과 | " + " | ".join(f"{C_NAME[x]} (단독)" for x in CONSTRAINTS) + " |")
    w("|---|---:|---:|---:|" + "---:|" * len(CONSTRAINTS))
    agg = {"tries": 0, "ok": 0, "per": Counter(), "sole": Counter(), "combos": Counter()}
    for v in VARIANTS:
        cd = cand[("base", v)]
        for Lm in man["base"]["params"]["l_max_min"]:
            t = reason_table(cd["reasons"], cd["reason_sets"], f"seed_L{Lm}", ("connect",))
            for k in ("tries", "ok"):
                agg[k] += t[k]
            for k in ("per", "sole", "combos"):
                agg[k].update(t[k])
            w(f"| `{v}` | {Lm}분 | {t['tries']:,} | {t['ok']:,} | "
              + " | ".join(f"{t['per'][x]:,} ({t['sole'][x]:,})" for x in CONSTRAINTS) + " |")
    w(f"| 합 | | {agg['tries']:,} | {agg['ok']:,} | "
      + " | ".join(f"{agg['per'][x]:,} ({agg['sole'][x]:,})" for x in CONSTRAINTS) + " |")
    w("")
    rej = agg["tries"] - agg["ok"]
    top = agg["combos"].most_common(5)
    w(f"- 거절 {rej:,}회 중 사유 조합 상위: " + ", ".join(
        f"{' + '.join(C_NAME[x] for x in k.split('+'))} {v_:,}회({pct(v_ / rej)})" for k, v_ in top) + ".")
    w(f"- 편도 L_max에 걸린 시도 {agg['per']['l_max']:,}회({pct(agg['per']['l_max'] / rej)}), 우회율 "
      f"{agg['per']['detour']:,}회({pct(agg['per']['detour'] / rej)}), 정차 수 {agg['per']['max_stops']:,}회"
      f"({pct(agg['per']['max_stops'] / rej)}), 정류장 겹침 {agg['per']['repeat']:,}회({pct(agg['per']['repeat'] / rej)}).")
    used = sum(1 for p in plans["base"].values() for r in p["routes"].values() if r["kind"] == "connect")
    ncon = sum(1 for v in VARIANTS for x in cand[("base", v)]["candidates"] if x["kind"] == "connect")
    w(f"- 편도 연결 후보 {ncon}개, 기본 조건 16안에 쓰인 연결 노선 {used}개.")
    w("")

    # ------------------------------------------------------------ 7 제안
    w("## 7. v1 기본값 제안")
    w("")
    w("고르는 규칙(실험 데이터 10/26~30 결과만 씀): 민감도 9개 조건 중 우리 안의 시뮬레이터 충족률(11·13대 5일 평균의 평균)이 "
      "가장 높은 조건을 고른다. 다만 기본값(N_max 8, R 3km)보다 날짜·대수 10쌍 **모두**에서 높을 때만 바꾸고, 아니면 기본값을 유지한다.")
    w("")
    ranked = sorted(sens, key=lambda c: (-score[c["name"]], c["name"]))
    w("| 순위 | 조건 | 우리 안 충족률(11·13대 평균) | 기본값 대비 | 기본값보다 높은 날짜·대수 쌍 |")
    w("|---:|---|---:|---:|---:|")
    base_name = "N8_R3000"
    def paired(cname):
        wins = []
        for f in FLEETS:
            a = rates(runs[cname][("ours", "affected", f)])
            b = rates(runs[base_name][("ours", "affected", f)])
            wins += [x - y for x, y in zip(a, b)]
        return wins
    for i, c in enumerate(ranked, start=1):
        d = paired(c["name"])
        w(f"| {i} | N_max {nmax_label(man[c['name']]['params']['max_stops'])} · R {c['radius_m'] / 1000:g}km | "
          f"{pct(score[c['name']], 2)} | {pp(score[c['name']] - score[base_name])} | "
          f"{sum(x > 1e-12 for x in d)}/{len(d)} |")
    w("")
    best = ranked[0]
    # 요약에 민감도 결론을 넣는다(여기서 계산한 값)
    r3 = [c for c in sens if c["radius_m"] == 3000]

    def missing_base_routes(cname):
        seqs = {tuple(x["stops"]) for x in cand[(cname, "affected")]["candidates"]}
        bb = {x["route_id"]: x for x in cand[("base", "affected")]["candidates"]}
        rs = {tuple(bb[r["candidate"]]["stops"]) for f in FLEETS for r in plans["base"][("ours", "affected", f)]["routes"].values()}
        return f"{sum(1 for x in rs if x not in seqs)}/{len(rs)}"
    t8 = reason_table(cand[("N8_R3000", "affected")]["reasons"], cand[("N8_R3000", "affected")]["reason_sets"],
                      "insertion", ("access", "egress", "connect"))
    ins_rates = [cand_stats(cand[(c["name"], "affected")]) for c in sens]
    o_med = {c["radius_m"]: quantile(list(man[c["name"]]["variants"]["affected"]["overlaps"].values()), 0.5) for c in sens}
    lines = [
        f"- **민감도(affected 18안)**: 우리 안 충족률(11·13대 평균)이 가장 높은 조건은 N_max "
        f"{nmax_label(man[best['name']]['params']['max_stops'])} · R {best['radius_m'] / 1000:g}km({pct(score[best['name']], 2)})이고 "
        f"나머지 8개는 {pp(max(score[c['name']] for c in sens if c['name'] != best['name']) - score[best['name']])} 이하로 낮다. "
        "v1 기본값으로 기본값 유지를 제안한다(§7).",
        f"- **정차 수 상한은 삽입을 막는 유일한 원인이 아니었다.** R 3km·N_max 8에서 삽입 거절 {t8['tries'] - t8['ok']:,}회 중 정차 수 "
        f"**하나만** 걸린 것은 {t8['sole']['max_stops']:,}회다. 나머지는 편도·우회율에도 함께 걸린다. 그래서 N_max를 12나 제한 없음으로 "
        f"풀어도 삽입 통과율은 {pct(min(x['ins_ok'] / x['ins_n'] for x in ins_rates))}–{pct(max(x['ins_ok'] / x['ins_n'] for x in ins_rates))}에 "
        "머문다. R 3km에서는 N_max를 풀면 충족률이 오히려 내려간다. 확인한 원인 하나는 피더 후보 규칙(수요 상위 seed 중 제약을 지키는 "
        "처음 15개)이다. 긴 피더가 들어오며 짧은 피더가 밀려나, 기본값 우리 안 노선 중 "
        + ", ".join(f"N_max {nmax_label(man[c['name']]['params']['max_stops'])} 후보에 없는 것 {missing_base_routes(c['name'])}개"
                    for c in r3 if c["name"] != base_name)
        + "가 된다(§5).",
        f"- **R을 줄이면 O 포화는 풀리지만 충족률은 내려간다.** O 중앙값 R 3km {o_med[3000]:.2f} → 2km {o_med[2000]:.2f} → "
        f"1.5km {o_med[1500]:.2f}. 그래도 τ 0.2에서는 군집이 상한 3을 넘어 실제 쓰인 τ의 최솟값이 "
        + ", ".join(f"{r / 1000:g}km {min(cl['tau'] for cl in man[f'N8_R{r}']['variants']['affected']['clusters']):g}"
                    for r in (1500, 2000, 3000))
        + "이다. 권역이 줄면 후보가 덮는 수요가 줄어, R별 최고 조건의 충족률이 R 3km 최고보다 "
        + ", ".join(f"{r / 1000:g}km {pp(max(score[c['name']] for c in sens if c['radius_m'] == r) - max(score[c['name']] for c in r3))}"
                    for r in (2000, 1500))
        + "다(§5·§7).",
        f"- 편도 연결 seed {agg['tries']:,}회 중 통과 {agg['ok']}회. 거절의 {pct(agg['combos'].most_common(1)[0][1] / rej)}는 "
        "편도·정차 수·우회율 셋에 동시에 걸렸다. 제약 하나만 걸린 거절은 "
        + ", ".join(f"{C_NAME[k]} {v_:,}회" for k, v_ in agg["sole"].most_common() if v_) + "뿐이다(§6).",
        f"- 구로구 지자체안: 전사본이 미병합 브랜치에 있으나 그대로는 변환기를 통과하지 못해 매트릭스에 올리지 않았다(§8).",
    ]
    L[summary_at:summary_at] = lines
    d = paired(best["name"])
    if best["name"] != base_name and all(x > 1e-12 for x in d):
        pick = best
        w(f"**제안: N_max {nmax_label(man[pick['name']]['params']['max_stops'])}, R {pick['radius_m'] / 1000:g}km** "
          f"(나머지 기본값 유지). 기본값보다 충족률 {pp(score[pick['name']] - score[base_name])}, 10쌍 모두 높다.")
    else:
        pick = next(c for c in sens if c["name"] == base_name)
        why = ("최고 조건이 기본값 자신" if best["name"] == base_name else
               f"최고 조건(N_max {nmax_label(man[best['name']]['params']['max_stops'])} · R {best['radius_m'] / 1000:g}km)도 "
               f"10쌍 중 {sum(x > 1e-12 for x in d)}쌍에서만 기본값보다 높음")
        w(f"**제안: 기본값 유지(N_max 8, R 3km)** — {why}.")
    w("")
    w("- 근거와 한계: 민감도는 `affected` 변형만 돌렸다. 5일은 같은 망·같은 정책이고 날짜마다 수요만 다르다. "
      "고른 값을 바꾸지 않고 테스트 셋(11/02~06)에서 확인하는 일은 다음 단계다.")
    w("")

    # ------------------------------------------------------------ 8 지자체안
    w("## 8. 구로구 지자체안")
    w("")
    if muni is None or not muni.get("found"):
        w("**없음.**")
    else:
        conv = muni["conversion"]
        fails = Counter(x["check"] for x in (conv.get("failures") or []))
        w(f"전사본이 **미병합 브랜치에 있다**: `{muni['ref']}` (`{muni['ref_commit'][:7]}`, main에 병합 "
          f"{'됨' if muni['in_main'] else '안 됨'}), `{muni['path']}` — {muni['plan_id']} rev{muni['revision']}, "
          f"노선 {muni['routes']}개 · {muni['vehicles']}대, 조건 `{muni['condition']}`(오전 11대만).")
        w("")
        w(f"- 그대로 변환기에 넣으면(`{conv['bundle']}` f{conv['fleet']}) 종료 코드 {conv['rc']}: "
          + ", ".join(f"{k} {v}건" for k, v in fails.items())
          + " — 설명 키(`_condition_note` 등)와 `network_snapshot_id: null` 때문이다.")
        w(f"- 그 형식 문제를 빼도 망에 없는 정류장 {muni['stops_not_in_network']}개"
          f"(" + ", ".join(f"{p['route_id']} {', '.join(p['stops_not_in_network'])}" for p in muni["per_route"]
                           if p["stops_not_in_network"]) + "), "
          f"망 구간이 없는 연속 정류장 쌍 {muni['links_not_in_network']}/{muni['links']}개가 남는다. "
          "같은 브랜치의 결정 기록이 임시 정류장 추가와 미관측 구간 규칙(U1–U5)을 다루고 있다.")
        w("- 그래서 이번 매트릭스에는 올리지 않았다. 전사본을 고쳐 돌리는 것은 그 브랜치(평가 담당)의 일이고, 병합되면 같은 매트릭스"
          "(`run_sims.py`)로 11대 조건만 함께 돌린다.")
    w("")

    # ------------------------------------------------------------ 9 재현
    w("## 9. 재현")
    w("")
    w("```")
    w("# 이 작업 폴더에서. 결과는 저장소 밖에 쓴다.")
    w(f"python3 experiments/guro_routegen/run_v1.py --root <guro_0610 폴더> --out <밖>/routegen_v1 --jobs 6 "
      f"--data-label {args.data_label or '<zip 이름>'}")
    w("PYTHONPATH=src python3 -m busstrike.planning.routegen --root <guro_0610 폴더> --out <밖>/routegen_v1/repeat_base   # 결정론 확인")
    w("python3 experiments/guro_routegen/check_municipal.py --root <guro_0610 폴더> --out <밖>/routegen_v1/municipal_check.json")
    w("python3 experiments/guro_routegen/make_report_v1.py --v1 <밖>/routegen_v1 --v0 <밖>/routegen_v0/default --root <guro_0610 폴더> \\")
    w(f"    --data-label {args.data_label or '<zip 이름>'} --out experiments/reports/guro_routegen_v1.md")
    w("```")
    w("")
    w(f"- 생성 시간(초, 조건별 생성+배분): " + ", ".join(f"{c['name']} {c['generate_seconds']}" for c in conds)
      + f"; 시뮬레이션(초): " + ", ".join(f"{c['name']} {c.get('sims_seconds')}" for c in conds) + ".")
    w("")
    args.out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {args.out} ({len(L)} lines)")
    return 0


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_routes(a: dict, b: dict) -> bool:
    """두 안의 노선 집합(정차열·대수)이 같은지. plan.json을 읽어 비교한다(route_id 순서와 무관)."""
    ra = json.loads(Path(a["file"]).read_text(encoding="utf-8"))["routes"]
    rb = json.loads(Path(b["file"]).read_text(encoding="utf-8"))["routes"]
    return sorted((tuple(r["stops"]), r["vehicles"]) for r in ra) == sorted((tuple(r["stops"]), r["vehicles"]) for r in rb)


if __name__ == "__main__":
    raise SystemExit(main())
