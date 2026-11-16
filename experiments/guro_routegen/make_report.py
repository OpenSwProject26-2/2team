"""routegen 후보·운행안·시뮬레이터 실행 결과 → 보고서(markdown). 숫자는 모두 결과 파일에서 읽어 센다.

    python3 experiments/guro_routegen/make_report.py --routegen-out <routegen --out 폴더> --root <guro_0610 폴더> \
        --data-label guro_0610_2.zip --out experiments/reports/guro_routegen_v0.md

읽는 것: <routegen-out>/{manifest.json, bundle_check.json, candidates/<변형>.json, runs/run_sims.json,
runs/<변형>/runs.json}, 실행별 run/{metrics.json, timetable.json, passenger_outcomes.csv}, <root>/network/stops.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

VARIANTS = ["affected", "affected_maeul", "affected_gg", "affected_maeul_gg"]
VARIANT_DESC = {"affected": "체크 없음", "affected_maeul": "마을버스 흡수 제외",
                "affected_gg": "경기버스 흡수 제외(낙관적 근사)", "affected_maeul_gg": "마을·경기 흡수 모두 제외"}
LABEL = {"ours": "우리 알고리즘안", "sp": "최단경로 피더안"}
KIND = {"access": "회송 피더 access", "egress": "회송 피더 egress", "connect": "편도 연결"}
REASONS = ["no_connection", "service_end", "wait", "journey"]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def n(x: float, d: int = 1) -> str:
    if isinstance(x, int) or float(x).is_integer() and d == 0:
        return f"{int(round(x)):,}"
    return f"{x:,.{d}f}"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def span(xs: list[float], d: int = 1, f=None) -> str:
    f = f or (lambda v: n(v, d))
    avg = sum(xs) / len(xs)
    if min(xs) == max(xs):
        return f"{f(avg)} (5일 같음)"
    return f"{f(avg)} ({f(min(xs))}–{f(max(xs))})"


def hm(s: int) -> str:
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def run_facts(d: Path) -> dict:
    run = d / "run"
    m = load(run / "metrics.json")
    tt = load(run / "timetable.json")
    veh_route = {v: r["route_id"] for r in tt["routes"] for v in r["vehicle_ids"]}
    # passenger_outcomes.csv의 vehicle_id는 차 안에 남은 승객에만 채워지므로, 탄 차량은 events.jsonl의 boarded로 찾는다
    boarded_on: dict[str, str] = {}
    with open(run / "events.jsonl", encoding="utf-8") as f:
        for line in f:
            if '"boarded"' in line:
                e = json.loads(line)
                if e["kind"] == "boarded":
                    boarded_on[e["passenger_id"]] = e["vehicle_id"]
    served_by_route: Counter = Counter()
    with open(run / "passenger_outcomes.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["outcome"] == "served":
                served_by_route[veh_route[boarded_on[row["passenger_id"]]]] += 1
    trips = defaultdict(list)
    for t in tt["trips"]:
        if t["in_service"]:
            trips[t["route_id"]].append(t["stop_times"][0]["sched_s"])
    routes = {r["route_id"]: {"period_s": r["period_s"], "vehicles": len(r["vehicle_ids"]),
                              "departures": len(trips[r["route_id"]]),
                              "last_departure_s": max(trips[r["route_id"]]) if trips[r["route_id"]] else None}
              for r in tt["routes"]}
    guro = m["M5"]["by_origin_area"].get("구로구", {})
    return {"served": m["M1"]["served"], "total": m["M1"]["total"], "censored": m["M1"]["censored"],
            "reasons": m["M2"]["unserved_by_reason"], "wait_mean": m["M3"]["boarded"]["mean_s"],
            "wait_p90": m["M3"]["boarded"]["p90_s"], "boarded": m["M3"]["boarded"]["n"],
            "guro_rate": guro.get("served_rate"), "served_by_route": dict(served_by_route), "routes": routes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routegen-out", required=True, type=Path)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--data-label", default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    rg = args.routegen_out.resolve()
    man = load(rg / "manifest.json")
    check = load(rg / "bundle_check.json")
    cands = {v: load(rg / "candidates" / f"{v}.json") for v in VARIANTS}
    sims = load(rg / "runs" / "run_sims.json")
    stops = {}
    with open(args.root / "network" / "stops.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            stops[r["stop_id"]] = r

    plans = {(p["label"], p["variant"], p["fleet"]): p for p in man["plans"]}
    runs: dict[tuple, list[dict]] = defaultdict(list)
    run_meta = {}
    for v in VARIANTS:
        R = load(rg / "runs" / v / "runs.json")
        run_meta[v] = R["meta"]
        for r in R["runs"]:
            rec = dict(r)
            if r.get("verify_pass") is True:
                rec["facts"] = run_facts(Path(r["dir"]))
            runs[(r["label"], v, r["fleet"])].append(rec)

    # 실행에 쓴 plan.json 내용 = 지금 manifest의 plan 파일 내용인가
    plan_same = 0
    for v, meta in run_meta.items():
        for mp in meta["plans"]:
            p = plans[(mp["label"], v, mp["fleet"])]
            if json.loads(Path(p["file"]).read_text(encoding="utf-8")) == mp["content"]:
                plan_same += 1
    all_runs = [r for rs in runs.values() for r in rs]
    ok_runs = [r for r in all_runs if r.get("verify_pass") is True]
    engine_envs = Counter(json.dumps(r.get("engine_env"), sort_keys=True, ensure_ascii=False) for r in all_runs)
    not_run = Counter(tuple(r.get("not_run_checks") or []) for r in all_runs)
    not_verified = Counter(tuple(r.get("not_verified_checks") or []) for r in all_runs)

    # 근사 C = 시뮬레이터 period_s
    c_total = c_same = 0
    for key, rs in runs.items():
        p = plans[key]
        for r in rs:
            for rid, info in r.get("facts", {}).get("routes", {}).items():
                c_total += 1
                c_same += int(info["period_s"] == p["routes"][rid]["cycle_s"])

    # 고유 노선(정차열) 번호
    cand_by = {v: {c["route_id"]: c for c in cands[v]["candidates"]} for v in VARIANTS}
    uniq: dict[tuple, str] = {}
    uniq_info: dict[str, dict] = {}
    for key in sorted(plans, key=lambda k: (VARIANTS.index(k[1]), k[0], k[2])):
        p = plans[key]
        for rid, r in p["routes"].items():
            c = cand_by[key[1]][r["candidate"]]
            st = tuple(c["stops"])
            if st not in uniq:
                uid = f"U{len(uniq) + 1:02d}"
                uniq[st] = uid
                uniq_info[uid] = {"stops": st, "kind": c["kind"], "hubs": c["hubs"], "cycle_s": c["cycle_s"],
                                  "oneway_s": c["oneway_s"], "length_m": c["length_m"], "detour": c["detour"],
                                  "origins": set(), "plans": []}
            uid = uniq[st]
            uniq_info[uid]["origins"].add(c["origin"])
            uniq_info[uid]["plans"].append(f"{p['plan_id']}/{rid}")
    station_name = {}
    for v in VARIANTS:
        for k, h in cands[v]["hubs"].items():
            station_name[k] = h["name"]

    L: list[str] = []
    w = L.append
    w("# 구로구 후보 노선 생성 + 구 단독 배분 v0 — 시뮬레이터 결과")
    w("")
    w("> 이 문서는 `experiments/guro_routegen/make_report.py`가 생성 결과(`manifest.json`, `candidates/*.json`, "
      "`bundle_check.json`)와 실행 결과(`runs.json`, 실행별 `metrics.json`·`timetable.json`·`passenger_outcomes.csv`)를 "
      "읽어 만든다. 숫자를 손으로 고치지 않는다. 실행 원본·plan.json·후보 목록은 저장소 밖에 있고 커밋하지 않는다.")
    w("")

    # ------------------------------------------------------------ 요약
    w("## 요약")
    w("")
    w(f"- 설계 v1 4절(후보 노선 생성)·5절(구별 예산 제약 배분) 중 **구로구 하나, per_gu**만 구현해 운행안 "
      f"{len(plans)}개(우리 알고리즘안 {sum(1 for k in plans if k[0] == 'ours')}개 + 비교 기준 최단경로 피더안 "
      f"{sum(1 for k in plans if k[0] == 'sp')}개)를 만들고 각 안을 해당 변형·차량 조건의 5일 묶음으로 돌렸다.")
    w(f"- 실행 {len(all_runs)}회 중 변환기 S1–S6 통과·M05 완료·M07 pass {len(ok_runs)}회, 실패 {len(all_runs) - len(ok_runs)}회.")
    w("- **파라미터는 기본값 그대로다.** 결과를 보고 바꾼 값은 없다(§7).")
    rows = []
    for v in VARIANTS:
        for f in (11, 13):
            o = [r["facts"] for r in runs[("ours", v, f)] if "facts" in r]
            s = [r["facts"] for r in runs[("sp", v, f)] if "facts" in r]
            if o and s:
                ro = sum(x["served"] for x in o) / sum(x["total"] for x in o)
                rs_ = sum(x["served"] for x in s) / sum(x["total"] for x in s)
                rows.append((v, f, ro, rs_))
    w("- 시뮬레이터 충족률(5일 합 기준) 우리 안 / 최단경로 피더안: " + "; ".join(
        f"`{v}` {f}대 {pct(a)} / {pct(b)}" for v, f, a, b in rows) + ".")
    def route_set(p):
        return sorted((tuple(r["stops"]), r["vehicles"]) for r in load_plan(p))
    same_plans = [(v, f) for v in VARIANTS for f in (11, 13)
                  if route_set(plans[("ours", v, f)]) == route_set(plans[("sp", v, f)])]
    if same_plans:
        w(f"- 두 안의 정차열·대수가 완전히 같은 조건: {', '.join(f'`{v}` {f}대' for v, f in same_plans)}. "
          "이 조건에서는 삽입 휴리스틱이 최단경로 피더보다 나은 후보를 배분에 올리지 못했다(§9).")
    kap = []
    for key, rs in runs.items():
        fs = [r["facts"] for r in rs if "facts" in r]
        if fs:
            kap.append((sum(x["served"] for x in fs) / len(fs)) / plans[key]["approx_served"])
    w(f"- κ = 시뮬레이터 충족 ÷ 근사 충족(κ=1): 안 {len(kap)}개에서 {min(kap):.3f}–{max(kap):.3f}, "
      f"중앙값 {statistics.median(kap):.3f}(§4).")
    w(f"- 근사의 주기 C와 시뮬레이터 시간표의 `period_s`: 노선·실행 {c_total}건 중 {c_same}건 일치.")
    w("- 미충족의 대부분은 no_connection이다. service_end는 현행 운영 종료 규칙(회송까지 10:00 안) 그대로의 결과다(§5).")
    w("- 구로구 지자체안 전사본: **없음**(§8).")
    w("")

    # ------------------------------------------------------------ 1 입력
    w("## 1. 입력과 실행 조건")
    w("")
    w(f"- 입력 묶음: `{args.data_label or '?'}` — README 제목 \"{man['readme_title'].lstrip('# ')}\", "
      f"망 ID `{man['network_snapshot_id']}`.")
    w("  - 작업 지시는 `guro_0610.zip`을 지정했지만 그 파일의 망 ID는 `guro-buf2km-0610-v1`이라 지시의 망 ID"
      "(`guro-buf2km-0610-v2`)와 다르다. 망 ID가 맞는 `guro_0610_2.zip`(v3, 실험 10/26~30)을 썼다. "
      "이전 보고서(`guro_exp_1026_30.md` §7)와 같은 판단이다.")
    w("  - 테스트 묶음(`guro_0610_test`)은 열지 않았다.")
    nb = len(check)
    nm = sum(1 for c in check if c["match"])
    w(f"- **잔여 수요 대조**: `walkable == False` · `station_stop_id` 있음 · 변형별 `maeul_reach`/`gg_reach` 제외로 만든 "
      f"r(s→k, t)를 시뮬레이터 정수화 함수(`busstrike.sim.arrivals.realize_bins`, seed 42, 3600초 구간)에 넣은 결과가 "
      f"묶음 `demand_bins.csv`와 **행 단위로 같은 묶음 {nm}/{nb}개**.")
    w("")
    w("| 변형 | 묶음 | 유량 합(5일 평균) | 정수화 인원(5일 평균) | 행 일치 |")
    w("|---|---|---|---|---|")
    for v in VARIANTS:
        cs = [c for c in check if c["variant"] == v]
        w(f"| `{v}` | {len(cs)} | {n(sum(c['flow_sum'] for c in cs) / len(cs))} | "
          f"{n(sum(c['passengers_bundle'] for c in cs) / len(cs))} | {sum(c['match'] for c in cs)}/{len(cs)} |")
    w("")
    pol = next(r["policy"] for r in all_runs if r.get("policy"))
    w(f"- 정책(시나리오에서 읽음, 전 실행 같음): 대기 한도 {pol['max_wait_s']}초, 총 이동 한도 {pol['max_journey_s']}초, "
      f"기본 정차 {pol['dwell_base_s']}초, 회차 {pol['turnaround_s']}초, 순환 휴식 {pol['cycle_rest_s']}초. 좌석 "
      f"{man['alloc_params']['seat']}, 차량 11대 / 13대.")
    w(f"- 조정(후보 생성·배분)은 5일 평균 수요, 평가는 날짜별 묶음 5개를 각각 돌린 결과다.")
    w(f"- 생성 코드: 브랜치 `{man['branch']}`(기준 `{man['commit'][:7]}`, 커밋 전 작업본). 파일별 sha256은 "
      "`manifest.json`의 `code_sha256`.")
    env = json.loads(next(iter(engine_envs)))
    w(f"- 엔진 기록(`env`, 실행 {len(all_runs)}회{' 모두 같음' if len(engine_envs) == 1 else f' 중 서로 다른 값 {len(engine_envs)}개'}): commit `{env['commit'][:7]}`, "
      f"dirty={env['dirty']}, `{env['sim_version']}`. 시뮬레이터·검증기는 이 작업 폴더의 손대지 않은 코드"
      f"(추적 파일 변경 {sims['src_tracked_changes']})로 돌렸다.")
    w(f"- 실행기: `run_matrix.py`(sha256 `{sims['run_matrix_sha256'][:16]}…`)를 `--src <이 작업 폴더>/src`로 불렀다. "
      f"이 파일은 `feat/guro-exp-1026-30` 작업의 **커밋 전 파일**이라 원래 체크아웃에 있는 것을 그대로 썼다. "
      f"실행 시점에 그 체크아웃은 `{sims['run_matrix_repo_branch']}` 브랜치였지만 엔진은 위 dirty=False 코드다. "
      "변형마다 그 변형 묶음 5개만 든 폴더(심볼릭 링크)를 `--bundles`로 넘겼다(`run_sims.py`).")
    w(f"- 실행한 plan.json 내용 = 현재 생성 결과의 plan 파일: {plan_same}/{len(plans)}. "
      "생성기를 두 번 돌려 16개 plan.json이 바이트 단위로 같음을 확인했다(프로토콜 §2.3).")
    w(f"- M07 미실행 검사: {dict((','.join(k) or '없음', c) for k, c in not_run.items())} · "
      f"미검증: {dict((','.join(k) or '없음', c) for k, c in not_verified.items())}(정책에 임계값 없음·V16 항상 미검증).")
    w("")

    # ------------------------------------------------------------ 2 후보
    P = man["params"]
    w("## 2. 후보 생성")
    w("")
    w("### 2.1 파라미터(기본값)")
    w("")
    w("| 항목 | 값 |")
    w("|---|---|")
    w(f"| F_min = 2 × 좌석 × (240 ÷ C₀) | 2 × {P['seat']} × (240 ÷ {P['c0_s'] // 60}) = {n(P['f_min'], 0)}명(4시간 합, 5일 평균) |")
    w(f"| 권역 반경 R | {n(P['radius_m'] / 1000, 1)} km (역 좌표 ↔ 정류장 좌표 직선거리) |")
    w(f"| 겹침 τ / 군집 상한 / 넘칠 때 τ 증가 폭 | {P['tau']} / 역 {P['cluster_max']}개 / {P['tau_step']} |")
    w(f"| 섹터 수 m · 편도 상한 L_max | {{{', '.join(map(str, P['sectors']))}}} · {{{', '.join(map(str, P['l_max_min']))}}}분 |")
    w(f"| 정차 상한(역 지정 정류장 제외) · 우회율 상한 | {P['max_stops']}개 · {P['max_detour']} |")
    w(f"| 최단경로 피더 seed | 허브·방향마다 수요 상위 {P['sp_top']}개 |")
    w(f"| 중복 제거 | 같은 방향끼리 정차 정류장 Jaccard ≥ {P['jaccard']} |")
    w(f"| 배분 대기 W · κ | {man['alloc_params']['wait_s']}초 · {man['alloc_params']['kappa']} |")
    w("")
    w("구현에서 정한 해석(설계 문서에 값이 없는 곳):")
    w("")
    w("- F_k와 권역 수요 r(s↔k)는 access와 egress를 더한다.")
    w("- 편도 = 06~09시 프로필마다 Σ 구간 시간을 따로 잰 최댓값 + 기본 정차 × 구간 수(시뮬레이터 `planned_cycle`의 영업 leg와 같은 정차 규칙).")
    w("- 정류장 사이 최단경로의 가중치도 구간 프로필 최댓값. 경로 위 정류장은 모두 정차로 센다(통과 정류장 표현 없음).")
    w("- 우회율 = 노선 경로 길이(망 구간 거리 합) ÷ 첫 정류장–끝 정류장 직선거리. 망 구간 거리도 정류장 좌표 직선거리다(README).")
    w("- 섹터: 중심(허브 역, 연결은 두 역의 중점)에서 본 방위각 순으로 늘어놓고 가장 큰 빈 각에서 시작해 수요가 거의 같은 m조각. "
      "seed = argmax v_s × dist(s, 중심). seed는 혼자서 제약을 지키는 정류장만 후보로 둔다.")
    w("- 삽입 점수 = 증가 덮개 수요 ÷ 증가 편도 시간(시간이 늘지 않으면 무한대). 덮개 수요는 노선이 덮는 모든 OD(다른 역 포함).")
    w("- 2-opt: 고정 끝(역 정류장)을 뺀 anchor 구간 뒤집기를 한 번 훑는다. 편도가 줄고 덮개가 줄지 않을 때만 받는다.")
    w("- 최단경로 피더도 같은 제약(L_max는 큰 값 30분)을 지키는 seed 중 상위 15개.")
    w("- 중복 제거는 같은 방향(kind + 고정 역 정류장)끼리만 비교한다. access A→역과 egress 역→A는 정류장 집합이 같아도 다른 승객을 태운다. "
      "역 ID가 아니라 정류장으로 보므로 지정 정류장을 같이 쓰는 역(온수 1821·2752)의 같은 노선은 하나로 합쳐진다.")
    w("- 우리 안의 후보 풀 = 생성 + 최단경로 피더를 함께 넣고 중복 제거, 비교 기준 풀 = 최단경로 피더만 중복 제거.")
    w("")
    w("### 2.2 허브와 권역")
    w("")
    for v in VARIANTS:
        c = cands[v]
        w(f"**`{v}`** — 잔여 수요 5일 평균 {n(c['demand_total_5day_avg'])}명, 허브 {len(c['hubs'])}개")
        w("")
        w("| 역 ID | 역 | F_k | 권역 정류장 |")
        w("|---|---|---|---|")
        for k, h in sorted(c["hubs"].items(), key=lambda x: -x[1]["F"]):
            w(f"| {k} | {h['name']} | {n(h['F'])} | {h['catchment']} |")
        w("")
    stations_xy = {}
    with open(args.root / "network" / "stations.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            stations_xy[r["station_id"]] = (float(r["stop_lat"]), float(r["stop_lon"]))
    hub_all = sorted({k for v in VARIANTS for k in cands[v]["hubs"]})
    close = total_pairs = 0
    for i, a in enumerate(hub_all):
        for b in hub_all[i + 1:]:
            total_pairs += 1
            close += int(haversine(*stations_xy[a], *stations_xy[b]) < 2 * P["radius_m"])
    w("### 2.3 겹침 병합")
    w("")
    w(f"| 변형 | 허브 쌍 | O ≥ τ({P['tau']})인 쌍 | O 중앙값 | O 최댓값 | 군집(역) | 쓴 τ | cross_gu |")
    w("|---|---|---|---|---|---|---|---|")
    for v in VARIANTS:
        c = cands[v]
        Os = list(c["overlaps"].values())
        cl = c["clusters"]
        w(f"| `{v}` | {len(Os)} | {sum(1 for o in Os if o >= P['tau'])} | {statistics.median(Os):.2f} | {max(Os):.2f} | "
          + " · ".join("+".join(station_name.get(h, h) for h in x["hubs"]) for x in cl) + " | "
          + " · ".join(f"{x['tau']:.2f}" for x in cl) + " | "
          + " · ".join(("예(" + ",".join(x["gu"]) + ")") if x["cross_gu"] else "아니오" for x in cl) + " |")
    w("")
    w(f"- O_ij는 공통 정류장의 두 역 수요 합을 작은 쪽 F로 나누므로 1을 넘을 수 있다. R = {n(P['radius_m'] / 1000, 1)} km이면 허브 권역이 "
      f"대부분 겹쳐(허브로 뽑힌 역 쌍 중 역 사이 직선거리 < 2R인 쌍: {close}/{total_pairs}) τ = {P['tau']}에서는 거의 모든 쌍이 한 군집으로 묶인다. 그래서 실제 군집은 \"역 3개 상한 → 그 군집만 τ 올림\" 규칙이 정했다"
      "(쓴 τ 열). τ가 1을 넘으면 O의 \"겹침 비율\" 해석은 의미가 약해진다(§9).")
    w("- cross_gu는 군집 역의 지정 정류장이 구로구 밖에 있으면 표시한다. 노선 단위 표시(정류장 하나라도 구로구 밖)는 후보 목록의 `cross_gu`에 있다.")
    w("")
    w("### 2.4 후보 수")
    w("")
    w("| 변형 | 생성 access | 생성 egress | 생성 연결 | 피더 access | 피더 egress | 합 | 생성 중 피더와 정차열이 같은 것 | "
      "제약 위반 seed | 변환 실패 | 우리 풀(제거) | 피더 풀(제거) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in VARIANTS:
        c = cands[v]
        bo = c["by_origin_kind"]
        sp_stops = {tuple(x["stops"]) for x in c["candidates"] if x["origin"] == "shortest_path_feeder"}
        gen = [x for x in c["candidates"] if x["origin"] == "generated"]
        same = sum(1 for x in gen if tuple(x["stops"]) in sp_stops)
        w(f"| `{v}` | {bo.get('generated/access', 0)} | {bo.get('generated/egress', 0)} | {bo.get('generated/connect', 0)} | "
          f"{bo.get('shortest_path_feeder/access', 0)} | {bo.get('shortest_path_feeder/egress', 0)} | {c['candidates_count'] if 'candidates_count' in c else len(c['candidates'])} | "
          f"{same}/{len(gen)} | {c['dropped']['seed_infeasible']} | {c['dropped']['conversion_failed']} | "
          f"{c['pools']['ours']['kept']}({c['pools']['ours']['removed_jaccard']}) | {c['pools']['sp']['kept']}({c['pools']['sp']['removed_jaccard']}) |")
    w("")
    w("- 생성 후보는 (m, L_max) 6조합 × 섹터에서 나온 정차열을 하나로 합친 수다(같은 정차열은 `made_by`에 조합을 모음).")
    w("")
    w("**제약 판정 집계**(seed = 혼자서 노선이 되는지, 삽입 = 섹터 삽입의 시도마다). 거절 사유는 여러 개가 함께 붙을 수 있어 "
      "사유 열의 합이 거절 수보다 크다. `no_path_or_repeat` = 편도 상한 최댓값(30분) 안 최단경로 없음 또는 이어 붙인 정차열에 같은 정류장(S2).")
    w("")
    w("| 변형 | 단계 | 패턴 | 시도 | 통과 | 통과율 | 경로 없음·S2 | 편도 | 정차 수 | 우회율 |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for v in VARIANTS:
        R = cands[v]["reasons"]
        for stage in ("seed", "insertion"):
            for kind in ("access", "egress", "connect"):
                get = lambda x: R.get(f"{stage}/{kind}/{x}", 0)
                tried = get("ok") + get("rejected")
                if not tried:
                    continue
                w(f"| `{v}` | {stage} | {KIND[kind]} | {tried:,} | {get('ok'):,} | {pct(get('ok') / tried)} | "
                  f"{get('no_path_or_repeat'):,} | {get('l_max'):,} | {get('max_stops'):,} | {get('detour'):,} |")
    w("- 제약 위반 seed = 권역 정류장 중 혼자서도 편도·정차 수·우회율·S2를 못 지켜 seed·삽입 대상에서 뺀 수(패턴 × L_max마다 셈).")
    w("- 변환 실패 = 변환기에서 공차 복귀 경로가 없어(RETURN) 주기를 못 구한 후보.")
    w("")
    w("### 2.5 소요 시간(초)")
    w("")
    w("| 변형 | 허브·권역·병합 | 섹터·삽입·2-opt | 최단경로 피더 | 덮개·주기(변환기·시간표 함수) | 생성 합 | 배분 4건 합 |")
    w("|---|---|---|---|---|---|---|")
    for v in VARIANTS:
        s = man["variants"][v]["seconds"]
        a = man["variants"][v]["alloc_seconds"]
        w(f"| `{v}` | {s['setup']} | {s['insertion']} | {s['shortest_path_feeders']} | {s['cover_cycle']} | "
          f"{s['generate_total']} | {round(sum(a.values()), 2)} |")
    w("")
    w(f"생성기 전체(묶음 대조 20개 포함) 벽시계 {man['wall_seconds']}초. 시뮬레이터 실행 벽시계: "
      + ", ".join(f"`{v}` {sims['variants'][v]['seconds']}초" for v in VARIANTS) + "(6병렬, 실행 20회씩).")
    w("")

    # ------------------------------------------------------------ 3 운행안
    w("## 3. 운행안")
    w("")
    w("plan_id는 출처를 드러내지 않는다(schema.md §1): `plan-01-*` = 우리 알고리즘안, `plan-02-*` = 최단경로 피더안. "
      "노선은 §6의 고유 번호(U..)로 적는다. 근사 충족은 κ = 1, 5일 평균 수요, 4시간 합.")
    w("")
    w("| plan_id | 변형 | 차량 | 노선(대수) | 근사 덮개 수요 | 근사 충족 | 한도로 잘린 몫 | 탐욕 후 | 국소 탐색 이동 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for key in sorted(plans, key=lambda k: (VARIANTS.index(k[1]), k[2], k[0])):
        p = plans[key]
        cs = cand_by[key[1]]
        rl = " · ".join(f"{uniq[tuple(cs[r['candidate']]['stops'])]}({r['vehicles']})" for r in p["routes"].values())
        w(f"| `{p['plan_id']}` | `{key[1]}` | {key[2]} | {rl} | {n(p['approx_demand_covered'])} | {n(p['approx_served'])} | "
          f"{n(p['approx_cap_cut'])} | {n(p['value_greedy'])} | {len(p['local_search'])} |")
    w("")
    w("- \"탐욕 후\" = CELF 탐욕이 끝난 시점의 근사 값(남은 대수 채우기·국소 탐색 전). 국소 탐색(1대 옮기기, 가치 낮은 노선 닫고 재배분)이 "
      "받아들인 이동 수가 마지막 열이다.")
    w("")

    # ------------------------------------------------------------ 4 κ
    gap_rows: list[tuple[float, float]] = []
    w("## 4. 근사 충족 vs 시뮬레이터 충족")
    w("")
    w("시뮬레이터 충족 = M07 pass 실행의 M1 served, 5일 평균(최소–최대). κ = 시뮬레이터 5일 평균 ÷ 근사(κ=1). "
      "날짜별 κ 범위는 그날 served ÷ 같은 근사 값. 마지막 두 열은 5일 평균: 근사가 모르는 미충족(service_end·wait)과 차이를 견주려고 둔다.")
    w("")
    w("| 변형 | 차량 | 안 | 근사 충족 | 시뮬레이터 충족 | κ | 날짜별 κ | 근사 − 시뮬레이터 | service_end + wait |")
    w("|---|---|---|---|---|---|---|---|---|")
    for v in VARIANTS:
        for f in (11, 13):
            for lab in ("ours", "sp"):
                fs = [r["facts"] for r in runs[(lab, v, f)] if "facts" in r]
                ap_ = plans[(lab, v, f)]["approx_served"]
                sv = [x["served"] for x in fs]
                se = sum(x["reasons"].get("service_end", 0) + x["reasons"].get("wait", 0) for x in fs) / len(fs)
                gap_rows.append((ap_ - sum(sv) / len(sv), se))
                w(f"| `{v}` | {f} | {LABEL[lab]} | {n(ap_)} | {span(sv)} | {sum(sv) / len(sv) / ap_:.3f} | "
                  f"{min(sv) / ap_:.3f}–{max(sv) / ap_:.3f} | {n(ap_ - sum(sv) / len(sv))} | {n(se)} |")
    w("")
    w("### 4.1 노선별 κ")
    w("")
    w("시뮬레이터 노선별 충족 = served 승객(`passenger_outcomes.csv`)을 `events.jsonl`의 boarded 차량 → 시간표 노선으로 붙여 센 것. "
      "같은 정차열(U..)이 여러 안에 있으면 안마다 따로 적는다.")
    w("")
    w("| plan_id | 노선 | 고유 | 종류 | 대수 | C(초) | 근사 | 시뮬레이터(5일 평균) | κ | 영업 출발 수 | 마지막 영업 출발 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for key in sorted(plans, key=lambda k: (VARIANTS.index(k[1]), k[2], k[0])):
        p = plans[key]
        fs = [r["facts"] for r in runs[key] if "facts" in r]
        cs = cand_by[key[1]]
        for rid, r in p["routes"].items():
            a = sum(r["approx_served_by_hour"])
            s = sum(x["served_by_route"].get(rid, 0) for x in fs) / len(fs)
            deps = {x["routes"][rid]["departures"] for x in fs}
            last = {x["routes"][rid]["last_departure_s"] for x in fs}
            w(f"| `{p['plan_id']}` | {rid} | {uniq[tuple(cs[r['candidate']]['stops'])]} | {KIND[r['kind']]} | {r['vehicles']} | "
              f"{r['cycle_s']} | {n(a)} | {n(s)} | {(s / a if a else float('nan')):.3f} | "
              f"{'/'.join(map(str, sorted(deps)))} | {'/'.join(hm(x) for x in sorted(last) if x is not None)} |")
    w("")

    # ------------------------------------------------------------ 5 충족률·사유
    w("## 5. 충족률과 미충족 사유")
    w("")
    w("각 칸은 **5일 평균 (최소–최대)**. M07 pass 실행만 집계. 변형끼리 섞지 않는다. M2 사유 우선순위는 "
      "no_connection → service_end → journey → wait. M3는 탑승 승객 대기, M5는 출발 정류장이 구로구인 승객의 충족률.")
    w("")
    cens = sum(r["facts"]["censored"] for r in ok_runs)
    w(f"측정 종료 시점에 차 안에 있던 승객(censored)은 전 실행 합 {cens}명.")
    w("")
    for v in VARIANTS:
        w(f"### `{v}` — {VARIANT_DESC[v]}")
        w("")
        cols = [(lab, f) for f in (11, 13) for lab in ("ours", "sp")]
        w("| 지표 | " + " | ".join(f"{LABEL[l]} {f}대" for l, f in cols) + " |")
        w("|---|" + "---|" * len(cols))
        F = {c: [r["facts"] for r in runs[(c[0], v, c[1])] if "facts" in r] for c in cols}

        def row(name, fn, d=1, f=None):
            w(f"| {name} | " + " | ".join(span([fn(x) for x in F[c]], d, f) for c in cols) + " |")
        row("대상 인원 (M1 total)", lambda x: x["total"])
        row("충족 인원 (M1 served)", lambda x: x["served"])
        row("**M1 충족률**", lambda x: x["served"] / x["total"], f=pct)
        for rsn in REASONS:
            row(f"M2 {rsn}", lambda x, rsn=rsn: x["reasons"].get(rsn, 0))
        row("M3 탑승 승객 수", lambda x: x["boarded"])
        row("M3 대기 평균(초)", lambda x: x["wait_mean"], 0)
        row("M3 대기 p90(초)", lambda x: x["wait_p90"], 0)
        row("**M5 구로구 충족률**", lambda x: x["guro_rate"], f=pct)
        w("")
    w("- no_connection = 운행안의 어떤 노선도 그 출발 → 도착(역 지정 정류장)을 같은 영업 leg에서 순서대로 지나지 않음. 11·13대로는 "
      "역 4~9개 수요 중 일부 정류장만 잇는다.")
    w("- service_end = 현행 시간표 규칙(차량 순환이 회송까지 10:00 안에 끝나야 넣음)으로 막차가 일찍 끊겨 빠진 승객. "
      "**운영 종료 규칙은 다른 브랜치에서 바꾸는 중이며 이 보고서는 현행 규칙 그대로다.** 근사(v2)는 이 효과를 모르므로 κ에 섞인다.")
    w("")

    # ------------------------------------------------------------ 6 노선 목록
    w("## 6. 노선 지도용 정류장 목록")
    w("")
    w("운행안에 쓰인 고유 정차열. 좌표는 `network/stops.csv`(WGS84). 정차 순서대로이며 모든 정류장이 정차다. "
      "공차 복귀 경로는 변환기 규칙(06:00 구간 시간 합 최소)으로 정해지고 승객을 태우지 않는다.")
    w("")
    for uid, info in uniq_info.items():
        hubs = " → ".join(station_name.get(h, h) for h in info["hubs"])
        orig = " / ".join(sorted("생성" if o == "generated" else "최단경로 피더" for o in info["origins"]))
        w(f"**{uid}** · {KIND[info['kind']]} · 역 {hubs} · 출처 {orig} · C {info['cycle_s']}초 · 편도(최대) "
          f"{info['oneway_s']}초 · 길이 {n(info['length_m'] / 1000, 2)} km · 우회율 {info['detour']:.2f}")
        w("")
        w(f"쓰인 안: {', '.join(info['plans'])}")
        w("")
        w("| 순서 | stop_id | 이름 | 위도 | 경도 | 구 |")
        w("|---|---|---|---|---|---|")
        for i, s in enumerate(info["stops"], start=1):
            st = stops.get(s, {})
            w(f"| {i} | {s} | {st.get('stop_name', '?')} | {st.get('stop_lat', '')} | {st.get('stop_lon', '')} | {st.get('gu', '')} |")
        w("")

    # ------------------------------------------------------------ 7 파라미터
    w("## 7. 파라미터 변경")
    w("")
    w("**없음.** 위 결과는 모두 §2.1 기본값으로 돌린 것이다. 결과를 보고 τ·R·F_min·L_max·제약값을 바꾸지 않았다. "
      "바꿀 만한 곳은 §9에 적었다(바꾸면 기본값 결과와 따로 남긴다).")
    w("")

    # ------------------------------------------------------------ 8 지자체안
    w("## 8. 구로구 지자체안")
    w("")
    w("**없음.** `experiments/plans/`에 지자체안 전사본(plan-NN/plan.json·conversion.md)이 없다(로컬 브랜치·원격 추적 브랜치 전체 확인). "
      "schema.md §2.1의 7개 노선 연결 목록은 \"전사 전 참고용\"이라 쓰지 않았다. 전사본이 들어오면 같은 매트릭스"
      "(`run_sims.py`에 `--plan` 추가)로 함께 돌린다.")
    w("")

    # ------------------------------------------------------------ 9 관찰
    w("## 9. 관찰과 한계")
    w("")
    ls_moves = sum(len(p["local_search"]) for p in plans.values())
    ttj = load(args.root / "network" / "travel_times.json")
    seg_n = len(ttj["segments"])
    node_n = len({x for s_ in ttj["segments"] for x in (s_["from_stop_id"], s_["to_stop_id"])})
    lower = []
    for v in VARIANTS:
        ours_pool = {tuple(cand_by[v][c]["stops"]) for c in cands[v]["pool_members"]["ours"]}
        for f in (11, 13):
            po, ps = plans[("ours", v, f)], plans[("sp", v, f)]
            if po["approx_served"] < ps["approx_served"] - 1e-6:
                missing = [rid for rid, r in ps["routes"].items()
                           if tuple(cand_by[v][r["candidate"]]["stops"]) not in ours_pool]
                lower.append((v, f, po["approx_served"], ps["approx_served"], missing))
    sp_not_in_ours = {v: sum(1 for c in cands[v]["pool_members"]["sp"]
                             if tuple(cand_by[v][c]["stops"]) not in
                             {tuple(cand_by[v][x]["stops"]) for x in cands[v]["pool_members"]["ours"]})
                      for v in VARIANTS}
    greedy_eq = sum(1 for p in plans.values() if abs(p["value_greedy"] - p["approx_served"]) < 1e-6)
    def stage_rate(stage, kinds):
        ok = rej = 0
        for v in VARIANTS:
            R = cands[v]["reasons"]
            for k in kinds:
                ok += R.get(f"{stage}/{k}/ok", 0)
                rej += R.get(f"{stage}/{k}/rejected", 0)
        return ok, ok + rej
    gen_all = sum(1 for v in VARIANTS for x in cands[v]["candidates"] if x["origin"] == "generated")
    gen_same = sum(1 for v in VARIANTS for x in cands[v]["candidates"] if x["origin"] == "generated" and tuple(x["stops"]) in
                   {tuple(y["stops"]) for y in cands[v]["candidates"] if y["origin"] == "shortest_path_feeder"})
    ins_ok, ins_n = stage_rate("insertion", ("access", "egress"))
    w(f"- **삽입 휴리스틱이 최단경로 피더와 거의 같은 노선을 낸다.** 생성 후보 {gen_all}개 중 {gen_same}개가 피더와 정차열이 같다. "
      f"삽입 시도 {ins_n:,}회 중 제약을 지킨 것은 {ins_n and ins_ok / ins_n:.1%}({ins_ok:,}회)이고, 거절 사유는 경로 없음·S2, 정차 수, 우회율, "
      f"편도가 비슷한 크기로 나온다(§2.4 제약 판정 집계). 망은 정류장 {node_n:,}개·구간 {seg_n:,}개(정류장당 나가는 구간 평균 {seg_n / node_n:.2f}개)이고 "
      "경로 위 정류장을 모두 정차로 센다. 통과 정류장 표현(설계 수정 제안 1번)이 정해지면 정차 수 제약이 풀려 이 결과가 달라질 수 있다.")
    only_sp = sum(sp_not_in_ours.values())
    if only_sp == 0:
        w("- **우리 안의 후보 풀은 피더 풀의 정차열을 모두 포함한다**(피더 풀에만 있는 정차열 0개). 같은 정차열이 생성·피더 둘 다에서 "
          "나오면 중복 제거에서 생성 쪽 이름으로 남는다. 그러므로 피더안은 우리 풀에서도 고를 수 있는 안이다.")
    else:
        w("- **우리 안의 후보 풀이 피더 풀을 다 포함하지 않는다.** 피더 풀에만 있는 정차열 수: "
          + ", ".join(f"`{v}` {sp_not_in_ours[v]}" for v in VARIANTS) + ". \"Jaccard ≥ 0.8이면 주기가 짧은 쪽\" 규칙이 "
          "생성+피더 풀에서 피더를 지우고 피더 풀에서는 남기기 때문이다.")
    for v, f, a, b, missing in lower:
        why = (f"피더안 노선 중 우리 풀에 없는 것: {', '.join(missing)}." if missing else
               "피더안의 노선은 모두 우리 풀에 있으므로 이 차이는 **CELF 탐욕 + 국소 탐색(1대 옮기기·노선 닫고 재배분)이 국소해에 멈춘 것**이다. "
               "근사 목적함수에서도 우리 배분기가 더 나은 안을 놓쳤다.")
        w(f"  - `{v}` {f}대: 근사 값 우리 안 {n(a)} < 피더안 {n(b)}. {why}")
    if not lower:
        w("  - 근사 값이 우리 안 < 피더안인 조건은 없다.")
    w(f"- 국소 탐색이 받아들인 이동: 안 {len(plans)}개 합 {ls_moves}개. 탐욕 직후 값 = 최종 값인 안 {greedy_eq}/{len(plans)}개.")
    gap_sum = sum(g for g, _ in gap_rows)
    se_sum = sum(s_ for _, s_ in gap_rows)
    w(f"- 근사는 대기 한도·수송 한도만 보고 운영 종료(service_end), 만차 대기열, 날짜별 정수화를 모른다. 안 {len(gap_rows)}개 합으로 "
      f"근사 − 시뮬레이터 = {n(gap_sum)}명, 같은 안의 service_end + wait = {n(se_sum)}명(§4 표). 운영 종료 규칙이 바뀌면 κ를 다시 구해야 한다.")
    w("- 겹침 지표 O가 R = 3 km에서 포화한다(§2.3). 병합을 의미 있게 쓰려면 R을 줄이거나 O를 min(F) 대신 F_i + F_j로 나누는 정규화를 검토한다. "
      "이번에는 바꾸지 않았다.")
    con_ok, con_n = stage_rate("seed", ("connect",))
    con_np = sum(cands[v]["reasons"].get("seed/connect/no_path_or_repeat", 0) for v in VARIANTS)
    con_gen = sum(cands[v]["by_origin_kind"].get("generated/connect", 0) for v in VARIANTS)
    con_used = sum(1 for p in plans.values() for x in p["routes"].values() if x["kind"] == "connect")
    w(f"- 편도 연결은 seed 판정 {con_n:,}회 중 {con_ok}회만 통과해 후보가 {con_gen}개뿐이고, 운행안에 쓰인 연결 노선은 {con_used}개다. 거절 중 "
      f"경로 없음·S2가 {con_np:,}회로 가장 많다(역 i egress 정류장 → 정류장 → 역 j access 정류장이 30분 안 망 경로로 안 이어지거나 정류장이 겹침). "
      "나머지는 편도·정차 수·우회율이 함께 걸린다.")
    w("- 순환·양방향 영업은 만들지 않았다(비교 입력 S2, 엔진의 영업 leg 1개 제약).")
    w("- 파업 노선 등록부 v0, 구간시간 기간(9/21~10/25) ≠ 수요 기간, 경기 흡수 낙관적 근사, 대기·이동 한도 승인 전 — 입력 묶음의 한계는 "
      "이전 보고서(`guro_exp_1026_30.md` §6)와 같다.")
    w("")

    # ------------------------------------------------------------ 10 재현
    w("## 10. 재현")
    w("")
    w("```sh")
    w("# <DATA> = guro_0610_2.zip을 푼 guro_0610/, <OUT> = 저장소 밖 결과 폴더, <RM> = feat/guro-exp-1026-30의 experiments/guro_exp/run_matrix.py")
    w("PYTHONPATH=src python3 -m busstrike.planning.routegen --root <DATA> --out <OUT>")
    w("python3 experiments/guro_routegen/run_sims.py --routegen-out <OUT> --bundles <DATA>/bundles --run-matrix <RM> \\")
    w("    --jobs 6 --data-label guro_0610_2.zip")
    w("python3 experiments/guro_routegen/make_report.py --routegen-out <OUT> --root <DATA> --data-label guro_0610_2.zip \\")
    w("    --out experiments/reports/guro_routegen_v0.md")
    w("PYTHONPATH=src python3 -m unittest tests.planning.test_routegen")
    w("```")
    w("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {args.out} ({len(L)} lines)")
    return 0


def load_plan(p: dict) -> list[dict]:
    return json.loads(Path(p["file"]).read_text(encoding="utf-8"))["routes"]


if __name__ == "__main__":
    raise SystemExit(main())
