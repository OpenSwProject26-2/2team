"""run_matrix.py 결과(runs.json + 실행별 metrics.json 등)로 보고서 markdown을 만든다.

    python3 experiments/guro_exp/make_report.py --runs <결과 폴더> --bundle-root <묶음 루트> \
        --out experiments/reports/guro_exp_1026_30.md

표의 숫자는 전부 여기서 실행 결과 파일을 읽어 계산한다. 보고서를 손으로 고치지 않고 이 스크립트를 고친다.
M07 검증이 pass=false인 실행은 표 2·service_end 집계에서 빼고 표 1의 실패 목록에만 적는다.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

REASONS = ("no_connection", "service_end", "wait", "journey")
EXPECTED_NOT_VERIFIED = {"V12", "V15", "V16"}
VARIANT_NOTE = {
    "affected": "체크 없음",
    "affected_maeul": "마을버스 흡수 제외",
    "affected_gg": "경기버스 흡수 제외(낙관적 근사)",
    "affected_maeul_gg": "마을·경기 흡수 모두 제외",
}
VARIANT_ORDER = list(VARIANT_NOTE)
PLAN_NAME = {"smoke": "스모크 안"}
# 실행하지 않은 운행안. 상태 판단 근거는 보고서 §2에 적는다.
ABSENT_PLANS = [
    ("구로구 지자체안", "전사본 없음"),
    ("우리 알고리즘안", "노선 생성 코드 없음"),
]


# ---------- 읽기 ----------

def load_run(rec: dict) -> dict:
    d = Path(rec["dir"])
    m = json.loads((d / "run" / "metrics.json").read_text(encoding="utf-8"))
    tt = json.loads((d / "run" / "timetable.json").read_text(encoding="utf-8"))
    rep = json.loads((d / "verifier_report.json").read_text(encoding="utf-8"))
    conv = json.loads((d / "conversion_report.json").read_text(encoding="utf-8"))

    # 노선이 잇는 (출발, 도착) 쌍: 영업 trip의 정차 순서에서 앞 → 뒤
    od_routes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for trip in tt["trips"]:
        if not trip["in_service"]:
            continue
        seq = [s["stop_id"] for s in trip["stop_times"]]
        for i, o in enumerate(seq):
            for dst in seq[i + 1:]:
                od_routes[(o, dst)].add(trip["route_id"])
    se_by_route: Counter = Counter()
    with open(d / "run" / "passenger_outcomes.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["reason"] == "service_end":
                routes = od_routes.get((row["origin"], row["destination"]))
                se_by_route["+".join(sorted(routes)) if routes else "(노선 없음)"] += 1

    routes = {}
    for r in tt["routes"]:
        rid = r["route_id"]
        svc = [t for t in tt["trips"] if t["route_id"] == rid and t["in_service"]]
        deps = [t["stop_times"][0]["sched_s"] for t in svc]
        duties = [x for x in tt["duties"] if x["route_id"] == rid]
        routes[rid] = {
            "vehicles": len(r["vehicle_ids"]),
            "period_s": r["period_s"],
            "service_trips": len(svc),
            "first_dep_s": min(deps) if deps else None,
            "last_dep_s": max(deps) if deps else None,
            "last_end_s": max(x["end_s"] for x in duties) if duties else None,
            "stops": len(svc[0]["stop_times"]) if svc else None,
        }
    for c in conv.get("routes", []):
        if c["route_id"] in routes:
            routes[c["route_id"]]["return_stops"] = len(c["return_stops"])

    m1, m3 = m["M1"], m["M3"]["boarded"]
    guro = m["M5"]["by_origin_area"].get("구로구", {})
    return {
        **rec,
        "total": m1["total"], "served": m1["served"], "censored": m1["censored"],
        "rate": m1["rate"], "lower": m1["lower"], "upper": m1["upper"],
        "reasons": {k: m["M2"]["unserved_by_reason"].get(k, 0) for k in REASONS},
        "wait_mean_s": m3["mean_s"], "wait_p90_s": m3["p90_s"], "boarded_n": m3["n"],
        "guro_rate": guro.get("served_rate"), "guro_total": guro.get("total"),
        "distance_km": sum(v["distance_m"] for v in m["M6"]["vehicles"]) / 1000,
        "vehicles_in_m6": len(m["M6"]["vehicles"]),
        "routes": routes, "se_by_route": dict(se_by_route),
        "not_verified": set(rep.get("not_verified_checks") or []),
    }


# ---------- 형식 ----------

def hhmm(s: int | None) -> str:
    return "—" if s is None else f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def stat(vals: list, fmt) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "NA"
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return f"{fmt(lo)} (5일 같음)" if len(vals) > 1 else fmt(lo)
    return f"{fmt(mean(vals))} ({fmt(lo)}–{fmt(hi)})"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def num(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def sec(x: float) -> str:
    return f"{x:,.0f}초"


def km(x: float) -> str:
    return f"{x:,.1f} km"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def plan_name(label: str) -> str:
    return PLAN_NAME.get(label, label)


def same_or_range(vals: list, fmt) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "—"
    lo, hi = min(vals), max(vals)
    return fmt(lo) if lo == hi else f"{fmt(lo)}–{fmt(hi)}"


# ---------- 본문 ----------

def build(runs_dir: Path, bundle_root: Path | None) -> str:
    index = json.loads((runs_dir / "runs.json").read_text(encoding="utf-8"))
    meta, recs = index["meta"], index["runs"]
    ok = [load_run(r) for r in recs if "stage_failed" not in r]
    failed = [r for r in recs if "stage_failed" in r]
    labels = list(dict.fromkeys(r["label"] for r in recs))
    fleets = sorted({r["fleet"] for r in recs})
    variants = [v for v in VARIANT_ORDER if any(r["variant"] == v for r in recs)]
    variants += sorted({r["variant"] for r in recs} - set(variants))
    dates = sorted({r["date"] for r in recs})
    group = defaultdict(list)
    for r in ok:
        group[(r["label"], r["variant"], r["fleet"])].append(r)

    L: list[str] = []

    def w(*lines: str) -> None:
        L.extend(lines)
    w("# 구로구 실험 데이터(10/26~30) 셔틀 운행안 시뮬레이션 결과")
    w("")
    w("> 이 문서는 `experiments/guro_exp/make_report.py`가 실행 결과(`runs.json`, 실행별 `metrics.json`·"
      "`timetable.json`·`passenger_outcomes.csv`·`verifier_report.json`)를 읽어 만든다. "
      "숫자를 손으로 고치지 않는다. 실행 원본은 저장소 밖에 있고 커밋하지 않았다.")
    w("")

    # 요약
    n_runs = len(recs)
    w("## 요약")
    w("")
    w(f"- 실행 {n_runs}회 중 M07 검증 pass {len(ok)}회, 실패 {len(failed)}회. "
      f"돌린 안은 **스모크 안 하나뿐**이다. 지자체안·우리 알고리즘안은 **없음**(§2).")
    w("- 스모크 안은 파이프라인 확인용 4개 노선이고 **비교안이 아니다.** 아래 충족률은 운행안 평가로 읽지 않는다.")
    if ok:
        nc = [r["reasons"]["no_connection"] / r["total"] for r in ok]
        se = [r["reasons"]["service_end"] for r in ok]
        w(f"- 미충족의 대부분은 no_connection(노선이 그 출발·도착을 잇지 않음)이다: 대상 대비 "
          f"{pct(min(nc))}–{pct(max(nc))}. service_end는 실행당 {num(min(se))}–{num(max(se))}명(§5).")
    w("")

    # 1. 입력과 조건
    w("## 1. 입력과 실행 조건")
    w("")
    repo = meta["repo"]
    envs = {json.dumps(r.get("engine_env"), sort_keys=True) for r in recs if r.get("engine_env")}
    env0 = json.loads(next(iter(envs))) if len(envs) == 1 else None
    ids = sorted({r["input_bundle_id"] for r in recs if r.get("input_bundle_id")})
    w(f"- 입력 묶음: `{meta.get('data_label')}` — README 제목 \"{(meta.get('bundles_readme_title') or '').lstrip('# ')}\"")
    w(f"- 묶음 {len({r['bundle'] for r in recs})}개 = 변형 {len(variants)}개 × 날짜 {len(dates)}일"
      f"({', '.join(d[4:6] + '/' + d[6:] for d in dates)}). 차량 조건 {', '.join(f'{f}대' for f in fleets)}.")
    w(f"- 실행 묶음 ID가 모두 실험(`-exp-`) 묶음인가: "
      f"{'예' if ids and all('-exp-' in i for i in ids) else '아니오 — 확인 필요'} ({len(ids)}개 ID, 예: `{ids[0] if ids else '-'}`)")
    w("- 테스트 묶음(`guro_0610_test`, 11/02~06)은 열지도 돌리지도 않았다.")
    if env0:
        w(f"- 엔진 기록(`env`): commit `{env0['commit'][:7]}`, dirty={env0['dirty']}, `{env0.get('sim_version')}`. "
          f"실행 브랜치 `{repo['branch']}`(추적 파일 변경 {repo['tracked_changes']}). Python {meta['python']}.")
    else:
        w(f"- 엔진 기록이 실행마다 다르다({len(envs)}종). 확인 필요.")
    pols = {json.dumps({k: v for k, v in (r.get("policy") or {}).items() if k != "name"}, sort_keys=True) for r in recs}
    if len(pols) == 1:
        p = json.loads(next(iter(pols)))
        w(f"- 정책(전 실행 같음): 대기 한도 {p['max_wait_s']}초, 총 이동 한도 {p['max_journey_s']}초, "
          f"기본 정차 {p['dwell_base_s']}초, 회차 {p['turnaround_s']}초, 순환 휴식 {p['cycle_rest_s']}초. "
          f"`wait_threshold_s`·`max_headway_s`는 null(→ V12·V15 미검증).")
    w("- 운행안별 원본 plan.json 내용은 `runs.json`의 `meta.plans`에 그대로 남겼다.")
    for p in meta["plans"]:
        rs = p["content"]["routes"]
        w(f"  - {plan_name(p['label'])} {p['fleet']}대: `plan_id={p['content']['plan_id']}`, "
          f"`network_snapshot_id={p['content']['network_snapshot_id']}`, 노선 {len(rs)}개, "
          f"대수 {'/'.join(str(r['vehicles']) for r in rs)} (합 {sum(r['vehicles'] for r in rs)})")
    w("")

    # 2. 운행안 현황
    w("## 2. 운행안 현황")
    w("")
    w(*table(["운행안", "상태", "근거"], [
        ["스모크 안", "실행", ("`smoke/plan_smoke_comparison.json`(11대). 13대는 묶음의 `scripts/run_all_bundles.py`와 같은 규칙"
         "(r1·r4에 1대씩 추가)으로 만든 사본(`experiments/guro_exp/add_vehicles.py`). 정류장·노선은 그대로.")],
        ["구로구 지자체안", "**없음**", ("`experiments/plans/`에 전사본(plan-NN/plan.json·conversion.md)이 없다. main 324ae0c와 "
         "원격 브랜치 전체를 확인했다. schema.md §2.1의 7개 노선 연결 목록은 \"전사 전 참고용이며 이것만으로 plan.json을 "
         "만들지 않는다\"고 적혀 있어 쓰지 않았다.")],
        ["우리 알고리즘안", "**없음**", ("`busstrike.planning`은 입력에 주어진 **후보 노선(구간 ID 열)에 차량을 배분**할 뿐 노선을 "
         "생성하지 않는다(README: \"자동 후보 생성 … 이 PR 범위 밖\"). 구로 묶음을 planning 입력(`Inputs`: 구간·허용 전이·회차 "
         "정류장·원 노선별 수요)으로 바꾸는 코드도 없다. 후보 노선을 우리가 정하면 안을 만들어 내는 것이므로 생성하지 않았다.")],
    ]))
    w("")
    w("변환기 검사(S1–S6·RETURN)에서 걸린 안은 없다(실행한 안이 스모크 안뿐).")
    w("")

    # 3. 표 1
    w("## 3. 표 1 — 실행 현황")
    w("")
    w("성공 = 변환기 S1–S6 통과 → M05 실행 → M07 `pass=true`. 미검증 V12·V15·V16은 정상(정책에 임계값 없음·V16 항상 미검증).")
    w("")
    rows = []
    for label in labels:
        for v in variants:
            for f in fleets:
                rs = [r for r in recs if (r["label"], r["variant"], r["fleet"]) == (label, v, f)]
                if not rs:
                    continue
                good = [r for r in ok if (r["label"], r["variant"], r["fleet"]) == (label, v, f)]
                bad = [r for r in rs if "stage_failed" in r]
                nv = sorted(set().union(*(r["not_verified"] for r in good))) if good else []
                odd = [r["date"] for r in good if r["not_verified"] != EXPECTED_NOT_VERIFIED]
                rows.append([plan_name(label), f"`{v}`", f"{f}대", len(rs), len(good), len(bad),
                             "; ".join(f"{r['date']} {r['stage_failed']}: {r['reason']}" for r in bad) or "—",
                             (",".join(nv) or "없음") + (f" (예상 밖: {','.join(odd)})" if odd else "")])
    for name, why in ABSENT_PLANS:
        rows.append([name, "전체", "11·13대", 0, "—", "—", f"실행 안 함: {why}(§2)", "—"])
    w(*table(["운행안", "변형", "차량", "실행", "성공", "실패", "실패 원인", "미검증 항목"], rows))
    w("")
    if failed:
        w("**실패 목록**")
        w("")
        for r in failed:
            w(f"- {plan_name(r['label'])} `{r['bundle']}` {r['fleet']}대 — {r['stage_failed']}: {r['reason']}")
        w("")

    # 4. 표 2
    w("## 4. 표 2 — 주요 지표(변형별)")
    w("")
    w("각 칸은 **5일 평균 (최소–최대)**. M07 pass인 실행만 집계한다. 변형끼리 한 표에 섞지 않는다.")
    w("")
    w("- M1 충족률 = served / total. M2는 미충족 사유별 인원(우선순위 no_connection → service_end → journey → wait).")
    w("- M3는 **탑승 승객**(`M3.boarded`) 대기시간. M5는 출발 정류장이 구로구인 승객의 충족률.")
    w("- M6는 차량 11/13대 운행거리의 합이며 **직선거리 기준**(정류장 좌표 사이 직선거리, 공차 회송 포함). 실제 운행거리로 쓰지 않는다.")
    cens = sum(r["censored"] for r in ok)
    w(f"- 측정 종료 시점에 차 안에 있던 승객(censored)은 전 실행 합 {cens}명"
      + ("이라 충족률은 점추정 하나로 적는다." if cens == 0 else ". censored가 있는 실행은 하한–상한을 함께 봐야 한다."))
    w("")
    cols = [(label, f) for label in labels for f in fleets if any(k[0] == label and k[2] == f for k in group)]
    for i, v in enumerate(variants, 1):
        tot = [r["total"] for r in ok if r["variant"] == v and r["fleet"] == fleets[0] and r["label"] == labels[0]]
        w(f"### 4.{i} `{v}` — {VARIANT_NOTE.get(v, '')}")
        w("")
        if tot:
            w(f"셔틀 대상(M1 total, 정수화된 승객 수) 5일 평균 {num(mean(tot))}명 ({num(min(tot))}–{num(max(tot))}).")
            w("")
        spec = [
            ("대상 인원 (M1 total)", lambda r: r["total"], num),
            ("충족 인원 (M1 served)", lambda r: r["served"], num),
            ("**M1 충족률**", lambda r: r["rate"], pct),
            ("M2 no_connection", lambda r: r["reasons"]["no_connection"], num),
            ("M2 service_end", lambda r: r["reasons"]["service_end"], num),
            ("M2 wait", lambda r: r["reasons"]["wait"], num),
            ("M2 journey", lambda r: r["reasons"]["journey"], num),
            ("M3 탑승 승객 수", lambda r: r["boarded_n"], num),
            ("M3 대기 평균", lambda r: r["wait_mean_s"], sec),
            ("M3 대기 p90", lambda r: r["wait_p90_s"], sec),
            ("**M5 구로구 충족률**", lambda r: r["guro_rate"], pct),
            ("M6 차량 거리 합 (직선거리 기준)", lambda r: r["distance_km"], km),
        ]
        hdr = ["지표"] + [f"{plan_name(l)} {f}대" for l, f in cols]
        body = [[name] + [stat([get(r) for r in group[(l, v, f)]], fmt) for l, f in cols] for name, get, fmt in spec]
        w(*table(hdr, body))
        w("")

    # 5. service_end
    w("## 5. service_end — 막차가 일찍 끊겨 빠진 승객")
    w("")
    w("시간표 생성기는 차량 순환(영업 leg + 종점→기점 공차 회송)이 **운영 종료 10:00 안에 끝나야** 그 순환을 넣는다. "
      "차량은 11:00까지 쓸 수 있지만 생성기가 `end = min(window.end_s, available_end_s)`로 10:00을 쓴다"
      "(`src/busstrike/sim/timetable.py` 129·136행, 324ae0c). 그래서 순환이 긴 노선은 막차가 10:00보다 한참 앞에서 끊기고, "
      "그 뒤에 정류장에 온 승객은 탈 차가 없어 `service_end`로 빠진다. "
      "**운영 종료 규칙은 팀 결정 전이다(1단계 미결정 5번).** 이 보고서는 현행 규칙 그대로 돌렸다.")
    w("")
    w("### 5.1 운행안·변형·차량 조건별")
    w("")
    w("\"노선이 잇는 승객\" = 대상 − no_connection. 스모크 안이 잇는 OD 안에서 service_end가 차지하는 비율을 함께 적는다.")
    w("")
    rows = []
    for label in labels:
        for v in variants:
            for f in fleets:
                g = group.get((label, v, f))
                if not g:
                    continue
                rows.append([plan_name(label), f"`{v}`", f"{f}대",
                             stat([r["reasons"]["service_end"] for r in g], num),
                             stat([r["reasons"]["service_end"] / r["total"] for r in g], pct),
                             stat([r["total"] - r["reasons"]["no_connection"] for r in g], num),
                             stat([r["reasons"]["service_end"] / (r["total"] - r["reasons"]["no_connection"])
                                   if r["total"] > r["reasons"]["no_connection"] else None for r in g], pct)])
    w(*table(["운행안", "변형", "차량", "service_end 인원", "대상 대비", "노선이 잇는 승객", "그중 service_end"], rows))
    w("")
    w("### 5.2 노선별 막차 시각과 service_end")
    w("")
    w("시간표는 수요와 무관해 변형·날짜가 달라도 같은 차량 조건이면 같게 나왔다(값이 다르면 범위로 적는다). "
      "service_end 인원은 승객의 출발·도착을 잇는 노선에 붙였다(둘 이상이면 `r2+r3`처럼 묶음). 각 칸은 5일 평균.")
    w("")
    for label in labels:
        for f in fleets:
            g_all = [r for r in ok if r["label"] == label and r["fleet"] == f]
            if not g_all:
                continue
            rids = sorted({rid for r in g_all for rid in r["routes"]})
            keys = sorted({k for r in g_all for k in r["se_by_route"]}, key=lambda k: (k.startswith("("), k))
            w(f"**{plan_name(label)} {f}대**")
            w("")
            hdr = ["노선", "정류장", "대수", "순환 주기", "영업 출발 수", "첫 출발", "마지막 영업 출발", "마지막 순환 종료"]
            body = []
            for rid in rids:
                rr = [r["routes"][rid] for r in g_all if rid in r["routes"]]
                body.append([rid, same_or_range([x["stops"] for x in rr], str),
                             same_or_range([x["vehicles"] for x in rr], str),
                             same_or_range([x["period_s"] for x in rr], lambda s: f"{s / 60:.1f}분"),
                             same_or_range([x["service_trips"] for x in rr], str),
                             same_or_range([x["first_dep_s"] for x in rr], hhmm),
                             same_or_range([x["last_dep_s"] for x in rr], hhmm),
                             same_or_range([x["last_end_s"] for x in rr], hhmm)])
            w(*table(hdr, body))
            w("")
            hdr = ["service_end를 낸 노선"] + [f"`{v}`" for v in variants]
            body = []
            for k in keys:
                body.append([k] + [num(mean(r["se_by_route"].get(k, 0) for r in group[(label, v, f)]))
                                   if group.get((label, v, f)) else "—" for v in variants])
            w(*table(hdr, body))
            w("")

    # 6. 한계
    w("## 6. 결과 해석 시 한계")
    w("")
    w("- **비교안 없음.** 돌린 안은 스모크 안뿐이고, 스모크 안은 비교안이 아니다. 운행안끼리의 비교·순위는 이 보고서로 말할 수 없다.")
    reg = None
    if bundle_root:
        p = bundle_root / "registry" / "strike_route_registry.csv"
        if p.exists():
            with open(p, encoding="utf-8", newline="") as f:
                rr = list(csv.DictReader(f))
            reg = (Counter(r.get("registry_version") for r in rr), Counter(r.get("strike_state") for r in rr))
    if reg:
        w(f"- **파업 노선 등록부 v0.** `registry_version` {dict(reg[0])}, 상태 {dict(reg[1])}. GTFS 노선 유형 규칙으로만 분류했고 "
          "공식 1월 파업 노선과 대조하지 않았다. 셔틀 대상 수요 자체가 이 분류에 달려 있다.")
    else:
        w("- **파업 노선 등록부 v0.** GTFS 노선 유형 규칙으로만 분류했고 공식 자료와 대조하지 않았다.")
    src = None
    if bundle_root:
        p = bundle_root / "network" / "travel_time_sources.csv"
        if p.exists():
            with open(p, encoding="utf-8", newline="") as f:
                src = Counter(r["source"] for r in csv.DictReader(f))
    if src:
        filled = sum(v for k, v in src.items() if k != "observed")
        w(f"- **구간시간 기간이 수요 기간과 다르다.** 구간시간은 OA-21217 9/21~10/25 평일 중앙값이고 수요는 10/26~30이다. "
          f"구간·시간 칸 {sum(src.values()):,}개 중 관측값이 아닌 칸 {filled}개({filled / sum(src.values()) * 100:.1f}%: "
          + ", ".join(f"{k} {v}" for k, v in sorted(src.items()) if k != "observed")
          + "). 원자료에 정차 시간이 들어 있는지 확인되지 않았다.")
    else:
        w("- **구간시간 기간이 수요 기간과 다르다.** 구간시간은 OA-21217 9/21~10/25 평일 중앙값이고 수요는 10/26~30이다.")
    w("- **경기버스 흡수는 낙관적 근사다.** 출발·도착 두 곳에 모두 서는 경기 노선이 있으면 흡수로 보며, 정차 순서·배차·여유 정원을 보지 않는다. "
      "`affected_gg`·`affected_maeul_gg`의 대상 인원은 실제보다 적게 잡혔을 수 있다. 마을버스 흡수도 \"전부 뺀다\"는 가정이다.")
    w("- **대기·이동 한도(1200/3600초)는 승인 전 제안값(M00 D07)이다.** wait·journey 사유 인원과 충족률이 이 값에 직접 달려 있다.")
    w("- M6 거리는 직선거리 기준이라 실제 운행거리보다 짧다. 시간 계산에는 쓰이지 않는다.")
    w("- M5는 승객 **출발 정류장** 기준이라 egress 승객(역 → 정류장)은 역이 있는 구로 잡힌다. 구로구 충족률은 구로 정류장에서 출발한 access와, 역 지정 정류장이 구로 안에 있는 egress로 이루어진다.")
    w("- 역 지정 정류장이 역에서 400m를 넘는 역(신림·보라매·당곡)의 수요는 묶음에서 빠져 있다(README: 하루 약 51.6명).")
    w("- 수요 날짜(10월)와 파업·셔틀 계획(1월)이 다르다. 결과는 \"1월 재현\"이 아니라 시나리오다.")
    w("")

    # 7. 입력 확인 사항
    w("## 7. 입력 묶음 관련 확인 필요")
    w("")
    w("작업 지시는 `guro_0610.zip`을 실험 데이터로 지정했지만, 그 파일은 **이전 빌드(README에 버전 표기 없음)** 였고 지시의 설명과 맞지 않았다. "
      "같은 폴더의 `guro_0610_2.zip`(README \"v3 … 실험(조정) 데이터 10/26~30\")이 지시의 설명과 모두 맞아 그것으로 돌렸다.")
    w("")
    w(*table(["항목", "작업 지시", "`guro_0610.zip`", "`guro_0610_2.zip` (사용)"], [
        ["망 ID", "`guro-buf2km-0610-v2`", "`guro-buf2km-0610-v1`", "`guro-buf2km-0610-v2`"],
        ["`affected` 5일 평균", "16,039명", "16,089명", "16,039명"],
        ["구간시간 출처", "OA-21217 9/21~10/25 중앙값", "11/02 TOPIS 구간시간 기반 근사", "OA-21217 9/21~10/25 중앙값"],
        ["`network/travel_time_sources.csv`", "있음(53칸)", "없음", "있음"],
        ["먼 지정 정류장 역 제외", "신림·보라매·당곡", "없음", "신림·보라매·당곡"],
        ["`scripts/run_all_bundles.py`", "있음", "없음", "있음"],
    ]))
    w("")
    w("망 ID가 v1이면 `network_snapshot_id = guro-buf2km-0610-v2`인 안은 변환기 망 일치 검사(C6)에 걸려 돌 수 없다. "
      "두 zip 모두 10/26~30 실험 묶음이고 테스트 묶음은 아니다. 파일 이름을 정리할지 팀에 확인이 필요하다. 위 표의 값은 "
      "두 zip의 README·`build_stats.json`·`travel_times.json`에서 확인한 것이다(이 표만 실행 결과가 아닌 입력 파일 비교다).")
    w("")

    # 8. 제안
    w("## 8. 제안 (이번 작업에서는 바꾸지 않음)")
    w("")
    w("- **운영 종료 규칙(1단계 미결정 5번)을 먼저 정한다.** 현행 규칙은 회송까지 10:00 안에 끝내야 해서, 순환이 긴 노선은 운영 시간 후반을 "
      "비운다(§5.2). 후보: (가) 현행 유지, (나) 마지막 **영업** 도착만 10:00 안이면 되고 회송은 차량 가용 종료(11:00)까지 허용, "
      "(다) 측정 종료(10:00) 이후 도착도 이동 한도 안이면 충족으로 셈. 정해지면 같은 40회를 다시 돌려 service_end 변화를 본다.")
    w("- **지자체안 전사본**을 schema.md §2.1 절차(방향별 정차 순서, `network/stops.csv` 매칭, 미매칭은 UNKNOWN으로 기록)로 만든다. 그 전에는 비교가 성립하지 않는다.")
    w("- **우리 알고리즘안**을 내려면 (1) 후보 노선 생성 단계 또는 (2) 구로 묶음 → `busstrike.planning` 입력 변환기가 필요하다. 후보 노선 집합을 누가·어떤 규칙으로 정하는지 먼저 정한다.")
    w("- 실험 묶음 파일 이름을 정리한다(§7). 보고서·재현 명령이 어느 zip을 가리키는지 분명해야 한다.")
    w("")

    # 9. 재현
    w("## 9. 재현")
    w("")
    w("```sh")
    w("# <DATA> = guro_0610_2.zip을 푼 폴더(guro_0610/), <OUT> = 저장소 밖 결과 폴더")
    w("python3 experiments/guro_exp/add_vehicles.py --plan <DATA>/smoke/plan_smoke_comparison.json \\")
    w("    --add 1,0,0,1 --suffix=-f13 --out <OUT>/plan_smoke_f13.json")
    w("python3 experiments/guro_exp/run_matrix.py --bundles <DATA>/bundles \\")
    w("    --plan smoke:11=<DATA>/smoke/plan_smoke_comparison.json --plan smoke:13=<OUT>/plan_smoke_f13.json \\")
    w("    --out <OUT>/runs --data-label guro_0610_2.zip --jobs 6")
    w("python3 experiments/guro_exp/make_report.py --runs <OUT>/runs --bundle-root <DATA> \\")
    w("    --out experiments/reports/guro_exp_1026_30.md")
    w("```")
    w("")
    w(f"이번 실행: {n_runs}회, 벽시계 {meta['wall_seconds']}초(병렬). 실행당 결과 폴더는 `<OUT>/runs/<안>/<묶음>/f<대수>/`.")
    w("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", required=True, type=Path, help="run_matrix.py의 --out 폴더")
    ap.add_argument("--bundle-root", type=Path, default=None, help="묶음 루트(guro_0610/). 등록부·구간시간 출처 집계용")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    text = build(args.runs.resolve(), args.bundle_root.resolve() if args.bundle_root else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"보고서: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
