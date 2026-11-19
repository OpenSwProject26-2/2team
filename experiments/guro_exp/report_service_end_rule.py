"""운영 종료 규칙 두 가지로 돌린 run_matrix.py 결과 두 벌을 비교하는 보고서를 만든다.

    python3 experiments/guro_exp/report_service_end_rule.py \
        --current <OUT>/runs_current --new <OUT>/runs_new [--previous <이전 실행 runs>] \
        --out experiments/reports/service_end_rule.md

표의 숫자는 전부 실행 결과 파일(metrics.json·timetable.json·passenger_outcomes.csv·verifier_report.json·
network.json)에서 계산한다. 보고서를 손으로 고치지 않고 이 스크립트를 고친다.
`--previous`를 주면 현행 규칙 실행의 산출물이 그 실행(이전 커밋)과 바이트 단위로 같은지 센다.
`--engine-commit`을 주면 실행 때의 미커밋 엔진 변경이 그 커밋의 `src/`와 같은지 확인해 적는다
(작업 트리 `src/` = 그 커밋의 `src/`, 그리고 바뀐 src 파일이 모두 실행 시작 전에 수정됨).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import mean

from make_report import VARIANT_NOTE, VARIANT_ORDER, load_run, num, pct, table

REPO = Path(__file__).resolve().parents[2]
RULES = {
    "current": ("cycle_within_window", "현행 규칙"),
    "new": ("start_within_window_return_by_available", "새 규칙"),
}
COMPARE_FILES = ("scenario/network.json", "run/events.jsonl", "run/passenger_outcomes.csv", "run/metrics.json",
                 "run/timetable.json")


def hms(s: int | None) -> str:
    return "—" if s is None else f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def avg_range(vals: list, fmt) -> str:
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return f"{fmt(lo)} (5일 같음)"
    return f"{fmt(mean(vals))} ({fmt(lo)}–{fmt(hi)})"


def same_or_range(vals: list, fmt) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "—"
    lo, hi = min(vals), max(vals)
    return fmt(lo) if lo == hi else f"{fmt(lo)}–{fmt(hi)}"


def pp(x: float) -> str:
    return f"{x * 100:+.1f}%p"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_set(runs_dir: Path, rule: str) -> tuple[dict, list[dict], list[dict]]:
    index = json.loads((runs_dir / "runs.json").read_text(encoding="utf-8"))
    recs = index["runs"]
    ok = []
    for rec in recs:
        if "stage_failed" in rec:
            continue
        r = load_run(rec)
        d = Path(rec["dir"])
        net = json.loads((d / "scenario" / "network.json").read_text(encoding="utf-8"))
        meas = json.loads((d / "run" / "measurement.json").read_text(encoding="utf-8"))
        r["spec_rule"] = net["spec"].get("service_end_rule", "cycle_within_window")
        r["measurement_rule"] = meas["policy"].get("service_end_rule", "cycle_within_window")
        r["max_return_s"] = max(t["return_s"] for t in net["trips"])
        r["avail_end"] = net["spec"].get("vehicle_available_end_s")
        r["drain_s"] = meas["config"]["drain_s"]
        r["service_end_s"] = net["spec"]["service_end_s"]
        r["max_trip_start_s"] = max(t["arrivals"][0] for t in net["trips"])
        ok.append(r)
        if r["spec_rule"] != rule or r["measurement_rule"] != rule:
            raise SystemExit(f"{rec['dir']}: 규칙 {r['spec_rule']}/{r['measurement_rule']} ≠ 기대 {rule}")
    return index["meta"], recs, ok


def git(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False)


def engine_line(meta: dict, runs_dirs: list[Path], engine_commit: str | None, envs: list[dict]) -> str:
    """실행에 쓴 엔진을 적는다. 실행은 기준 커밋 + 미커밋 변경으로 돌았으므로, 그 변경이 들어간 커밋을 확인해 잇는다."""
    base = meta["repo"]["commit"]
    env_note = (f"엔진 기록 env 종류 {len(envs)}개"
                f"({', '.join(sorted({e.get('sim_version', '?') for e in envs}))}).")
    head = (f"- 엔진: 실행 시점 브랜치 `{meta['repo']['branch']}`, 기준 커밋 `{base[:7]}`(main), "
            f"추적 파일 변경 {meta['repo']['tracked_changes']}.")
    if not engine_commit:
        return f"{head} {env_note}"
    full = git("rev-parse", "--verify", f"{engine_commit}^{{commit}}").stdout.strip()
    if not full:
        raise SystemExit(f"--engine-commit {engine_commit}: 커밋이 없다")
    if git("merge-base", "--is-ancestor", base, full).returncode != 0:
        raise SystemExit(f"--engine-commit {full[:7]}이 기준 커밋 {base[:7]}의 후손이 아니다")
    if git("diff", "--quiet", full, "--", "src").returncode != 0:
        raise SystemExit(f"작업 트리 src/가 {full[:7]}의 src/와 다르다. 그 커밋을 체크아웃한 상태에서 만든다")
    changed = [f for f in git("diff", "--name-only", base, full, "--", "src").stdout.split() if f]
    last_edit = max((REPO / f).stat().st_mtime for f in changed if (REPO / f).exists())
    starts = []
    for d in runs_dirs:
        idx = d / "runs.json"
        starts.append(idx.stat().st_mtime - float(json.loads(idx.read_text(encoding="utf-8"))["meta"]["wall_seconds"]))
    before = last_edit < min(starts)
    if not before:
        raise SystemExit("바뀐 src 파일 중 실행 시작 뒤에 수정된 것이 있다. 커밋한 엔진과 실행 엔진이 같다고 할 수 없다")
    return (f"{head} 그 변경은 커밋 `{full[:7]}`(`fix/service-end-rule`)의 `src/`와 같다"
            f"(보고서 작성 시점 작업 트리 `src/` = 그 커밋의 `src/`, 바뀐 src 파일 {len(changed)}개의 마지막 수정이 "
            f"두 실행의 시작보다 이르다). {env_note}")


def build(cur_dir: Path, new_dir: Path, prev_dir: Path | None, engine_commit: str | None = None) -> str:
    sets = {}
    for key, d in (("current", cur_dir), ("new", new_dir)):
        sets[key] = load_set(d, RULES[key][0])
    meta_c, recs_c, ok_c = sets["current"]
    meta_n, recs_n, ok_n = sets["new"]
    fleets = sorted({r["fleet"] for r in recs_c})
    variants = [v for v in VARIANT_ORDER if any(r["variant"] == v for r in recs_c)]
    dates = sorted({r["date"] for r in recs_c})

    grp: dict = {k: defaultdict(list) for k in sets}
    for k, (_, _, ok) in sets.items():
        for r in ok:
            grp[k][(r["variant"], r["fleet"])].append(r)

    L: list[str] = []
    w = L.extend

    def line(*xs: str) -> None:
        w(list(xs))

    line("# 운영 종료 규칙 비교 — 구로 실험 묶음(10/26~30) × 스모크 안",
         "",
         "> 이 문서는 `experiments/guro_exp/report_service_end_rule.py`가 두 벌의 실행 결과(`runs.json`과 실행별 "
         "`metrics.json`·`timetable.json`·`passenger_outcomes.csv`·`network.json`·`verifier_report.json`)를 읽어 만든다. "
         "숫자를 손으로 고치지 않는다. 실행 원본은 저장소 밖에 있고 커밋하지 않는다.",
         "")

    # ---------- 요약
    n_pass = {k: sum(1 for r in ok if r.get("verify_pass") is True) for k, (_, _, ok) in sets.items()}
    line("## 요약", "")
    line(f"- 실행 {len(recs_c)}회 × 규칙 2개. M07 검증 pass: 현행 규칙 {n_pass['current']}/{len(recs_c)}, "
         f"새 규칙 {n_pass['new']}/{len(recs_n)} (`--max-recompute 200000`).")
    se_c = mean(r["reasons"]["service_end"] for r in ok_c)
    se_n = mean(r["reasons"]["service_end"] for r in ok_n)
    rate_c = mean(r["rate"] for r in ok_c)
    rate_n = mean(r["rate"] for r in ok_n)
    line(f"- service_end 인원(40회 평균): {num(round(se_c, 1))}명 → {num(round(se_n, 1))}명. "
         f"충족률(40회 평균): {pct(rate_c)} → {pct(rate_n)}.")
    # 노선별 마지막 출발(차량 조건 첫 번째, 모든 실행에서 같으면 값 하나)
    f0 = fleets[0]
    rid_last = {}
    for k, (_, _, ok) in sets.items():
        for r in ok:
            if r["fleet"] != f0:
                continue
            for rid, x in r["routes"].items():
                rid_last.setdefault((k, rid), set()).add(x["last_dep_s"])
    longest = max(ok_c[0]["routes"], key=lambda rid: ok_c[0]["routes"][rid]["period_s"] or 0)
    p_long = ok_c[0]["routes"][longest]["period_s"]
    line(f"- 순환이 가장 긴 노선 {longest}(주기 {p_long / 60:.1f}분, {f0}대 조건)의 마지막 영업 출발: "
         f"{'/'.join(hms(x) for x in sorted(rid_last[('current', longest)]))} → "
         f"{'/'.join(hms(x) for x in sorted(rid_last[('new', longest)]))}.")
    max_ret = max(r["max_return_s"] for r in ok_n)
    line(f"- 새 규칙에서 가장 늦은 차량 복귀는 {hms(max_ret)}이고, 모든 운행편 복귀가 차량 가용 종료 안이다"
         f"({'예' if all(r['max_return_s'] <= min(r['avail_end'].values()) for r in ok_n) else '아니오'}). "
         f"가장 늦은 운행편 시작은 {hms(max(r['max_trip_start_s'] for r in ok_n))}(운영창 끝 "
         f"{hms(ok_n[0]['service_end_s'])}보다 이르다: "
         f"{'예' if all(r['max_trip_start_s'] < r['service_end_s'] for r in ok_n) else '아니오'}).")
    line("- 돌린 안은 **스모크 안 하나뿐**이고 비교안이 아니다. 규칙이 운행안 순위를 어떻게 바꾸는지는 이 실행으로 "
         "보이지 못한다. 노선 길이에 따른 편향이 줄어드는지는 §4·§5의 노선별 값으로만 본다.", "")

    # ---------- 1. 조건
    line("## 1. 규칙과 실행 조건", "")
    line("| 규칙 | `policy.service_end_rule` | 순환을 넣는 조건 |", "|---|---|---|",
         "| 현행 규칙 | `cycle_within_window`(필드 없음 = 기본값) | 순환 전체(영업 + 회차 + 공차 복귀)가 "
         "min(운영창 끝, 차량 가용 종료) 안에 끝남 |",
         "| 새 규칙 | `start_within_window_return_by_available` | 순환 시작(첫 영업 leg 출발) < 운영창 끝, "
         "순환 전체 ≤ 그 차량의 `available_end_s` |", "")
    env_c = {json.dumps(r.get("engine_env"), sort_keys=True) for r in recs_c + recs_n}
    envs = [json.loads(e) for e in env_c]
    ids_c = sorted({r["input_bundle_id"] for r in recs_c})
    ids_n = sorted({r["input_bundle_id"] for r in recs_n})
    line(f"- 입력 묶음: `{meta_c.get('data_label')}` — README 제목 "
         f"\"{(meta_c.get('bundles_readme_title') or '').lstrip('# ')}\". 묶음 {len({r['bundle'] for r in recs_c})}개 = "
         f"변형 {len(variants)}개 × 날짜 {len(dates)}일({', '.join(d[4:6] + '/' + d[6:] for d in dates)}), "
         f"차량 조건 {', '.join(f'{f}대' for f in fleets)}.",
         "- 작업 지시에는 `guro_0610.zip`이라고 적혀 있었으나, 지시한 사람이 `guro_0610_2.zip`(v3, 망 "
         "`guro-buf2km-0610-v2`)이 맞다고 확인했다. 파일 이름은 그쪽에서 정리한다. 지시가 인용한 숫자(순환 74분 "
         "노선 막차 08:29, service_end 약 18%)도 이 묶음의 실행 결과다.",
         "- 테스트 묶음(`guro_0610_test`)은 열지도 돌리지도 않았다.",
         f"- 새 규칙 입력: 원본 묶음의 사본에 `policy.service_end_rule`만 더했다"
         "(`experiments/guro_exp/make_rule_bundles.py`). 그 때문에 `run_inputs`의 `scenario_sha256`·`checksum` "
         "두 값만 다시 계산했다. 수요·구간시간·seed·`input_bundle_id`는 원본과 같다"
         f"(ID 집합 같음: {'예' if ids_c == ids_n else '아니오'}). 원본 묶음 파일은 바꾸지 않았다.",
         engine_line(meta_c, [cur_dir, new_dir], engine_commit, envs))
    pol = ok_c[0]["policy"] if ok_c[0].get("policy") else None
    line(f"- 정책(전 실행 같음, 규칙 필드만 다름): 대기 한도 {ok_c[0]['policy']['max_wait_s']}초, "
         f"총 이동 한도 {ok_c[0]['policy']['max_journey_s']}초, 정차 {ok_c[0]['policy']['dwell_base_s']}초, "
         f"회차 {ok_c[0]['policy']['turnaround_s']}초." if pol else "- 정책: runs.json에 없음. ")
    drains = {r["drain_s"] for r in ok_c + ok_n}
    avails = {v for r in ok_n for v in r["avail_end"].values()}
    line(f"- 운영창 끝 {hms(ok_c[0]['service_end_s'])}, "
         f"차량 가용 종료 {', '.join(hms(a) for a in sorted(avails))}, 관측 끝 drain "
         f"{', '.join(hms(d) for d in sorted(drains))}(= 운영창 끝 + 총 이동 한도). 가용 종료가 drain보다 늦지 않아 "
         "새 규칙의 모든 방문이 관측 안에 든다.",
         "", "**새 규칙의 가정** (결과 해석 전에 볼 것)", "",
         f"- 차량 가용 종료 {', '.join(hms(a) for a in sorted(avails))}은 **시나리오 가정이다(실측 아님).** "
         "운전자 근무시간·차량 반납 시각은 확인하지 않았다.",
         "- 10:00 이후 구간시간은 **09시 값을 쓴다.** 묶음에 10시 프로필이 없어서다(구간시간 표의 마지막 시간대가 "
         "09:00, simulator.md 가정 A28). 10시 프로필은 묶음 쪽에서 추가할 예정이다.", "")

    # ---------- 2. 검증
    line("## 2. M07 검증", "")
    rows = []
    for k, (_, recs, ok) in sets.items():
        for f in fleets:
            rs = [r for r in recs if r["fleet"] == f]
            oks = [r for r in ok if r["fleet"] == f]
            fails = [r for r in rs if "stage_failed" in r]
            nv = sorted({c for r in oks for c in (r.get("not_verified") or [])})
            failed_checks = sorted({c for r in rs for c in (r.get("failed_checks") or [])})
            rows.append([RULES[k][1], f"{f}대", len(rs), sum(1 for r in oks if r.get("verify_pass") is True),
                         len(fails), ",".join(failed_checks) or "—", ",".join(nv) or "—"])
    w(table(["규칙", "차량", "실행", "pass=true", "실패", "실패 검사", "미검증(정상)"], rows))
    line("", "- 미검증 V12·V15는 정책에 임계값이 없어서, V16은 R10·R13 자료가 없어서 늘 미검증이다. "
         "새 규칙 실행의 V16 R14는 `network.json` `spec.service_end_rule`·`vehicle_available_end_s`로 "
         "검사했고 위반이 없다(위반이 있으면 실패 검사에 V16이 나온다).")
    if prev_dir is not None:
        prev = {(r["label"], r["bundle"], r["fleet"]): Path(r["dir"])
                for r in json.loads((prev_dir / "runs.json").read_text(encoding="utf-8"))["runs"]}
        prev_meta = json.loads((prev_dir / "runs.json").read_text(encoding="utf-8"))["meta"]
        same = {name: 0 for name in COMPARE_FILES}
        n = 0
        for r in ok_c:
            p = prev.get((r["label"], r["bundle"], r["fleet"]))
            if p is None:
                continue
            n += 1
            for name in COMPARE_FILES:
                if sha(p / name) == sha(Path(r["dir"]) / name):
                    same[name] += 1
        line(f"- 기본값 회귀 확인: 현행 규칙 실행 {n}회를 이전 실행(커밋 `{prev_meta['repo']['commit'][:7]}`, "
             "이 변경 전 엔진)과 바이트 비교했다. " +
             ", ".join(f"`{name.split('/')[-1]}` {same[name]}/{n}" for name in COMPARE_FILES) + " 같음.")
    line("")

    # ---------- 3. 지표
    line("## 3. 규칙별 충족률·service_end (변형 × 차량, 5일 평균)", "")
    line("각 칸은 **5일 평균 (최소–최대)**. 차이는 같은 날짜끼리 (새 − 현행)의 5일 평균이다. "
         "\"노선이 잇는 승객\" = 대상 − no_connection(규칙과 무관하게 같다).", "")

    def paired(v, f, fn):
        cur = {r["date"]: r for r in grp["current"][(v, f)]}
        new = {r["date"]: r for r in grp["new"][(v, f)]}
        return [fn(new[d]) - fn(cur[d]) for d in sorted(cur) if d in new]

    for v in variants:
        line(f"### 3.{variants.index(v) + 1} `{v}` — {VARIANT_NOTE.get(v, '')}", "")
        rows = []
        metrics = [
            ("대상 인원 (M1 total)", lambda r: r["total"], num, False),
            ("노선이 잇는 승객", lambda r: r["total"] - r["reasons"]["no_connection"], num, False),
            ("충족 인원 (M1 served)", lambda r: r["served"], num, True),
            ("**M1 충족률**", lambda r: r["rate"], pct, True),
            ("M2 service_end", lambda r: r["reasons"]["service_end"], num, True),
            ("노선이 잇는 승객 중 service_end", lambda r: r["reasons"]["service_end"] /
             max(1, r["total"] - r["reasons"]["no_connection"]), pct, True),
            ("M2 wait", lambda r: r["reasons"]["wait"], num, True),
            ("M2 journey", lambda r: r["reasons"]["journey"], num, True),
            ("M3 대기 평균(탑승 승객)", lambda r: r["wait_mean_s"], lambda x: f"{x:,.0f}초", True),
            ("M5 구로구 충족률", lambda r: r["guro_rate"], pct, True),
            ("M6 차량 거리 합(직선거리 기준)", lambda r: r["distance_km"], lambda x: f"{x:,.1f} km", True),
        ]
        header = ["지표"]
        for f in fleets:
            header += [f"{f}대 현행", f"{f}대 새 규칙", f"{f}대 차이"]
        for name, fn, fmt, show_diff in metrics:
            row = [name]
            for f in fleets:
                row.append(avg_range([fn(r) for r in grp["current"][(v, f)]], fmt))
                row.append(avg_range([fn(r) for r in grp["new"][(v, f)]], fmt))
                if show_diff:
                    d = paired(v, f, fn)
                    dfmt = pp if fmt is pct else (lambda x, fmt=fmt: ("+" if x >= 0 else "") + fmt(x))
                    row.append(dfmt(mean(d)))
                else:
                    row.append("—")
            rows.append(row)
        w(table(header, rows))
        line("")

    # ---------- 4. 노선별 시각
    line("## 4. 노선별 마지막 출발 시각", "")
    line("시간표는 수요와 무관해 같은 차량 조건이면 변형·날짜가 달라도 같다(다르면 범위로 적는다). "
         "\"마지막 순환 종료\" = 그 노선 차량 duty 끝(공차 복귀 도착) 중 가장 늦은 값.", "")
    for f in fleets:
        line(f"**스모크 안 {f}대**", "")
        rows = []
        rids = sorted(ok_c[0]["routes"])
        for rid in rids:
            def vals(k, field):
                return [r["routes"][rid][field] for r in sets[k][2] if r["fleet"] == f]
            rows.append([rid, same_or_range(vals("current", "vehicles"), str),
                         same_or_range(vals("current", "period_s"), lambda x: f"{x / 60:.1f}분"),
                         same_or_range(vals("current", "service_trips"), str),
                         same_or_range(vals("new", "service_trips"), str),
                         same_or_range(vals("current", "last_dep_s"), hms),
                         same_or_range(vals("new", "last_dep_s"), hms),
                         same_or_range(vals("current", "last_end_s"), hms),
                         same_or_range(vals("new", "last_end_s"), hms)])
        w(table(["노선", "대수", "순환 주기", "영업 출발 수 현행", "새 규칙", "마지막 영업 출발 현행", "새 규칙",
                 "마지막 순환 종료 현행", "새 규칙"], rows))
        line("")

    # ---------- 5. 노선별 service_end
    line("## 5. 노선별 service_end (5일 평균)", "")
    line("service_end 승객을 그 출발·도착을 잇는 노선에 붙였다(둘 이상이면 `r2+r3`처럼 묶음). "
         "각 칸은 `현행 → 새 규칙`.", "")
    for f in fleets:
        line(f"**스모크 안 {f}대**", "")
        keys = sorted({k for s in ("current", "new") for r in sets[s][2] if r["fleet"] == f
                       for k in r["se_by_route"]})
        rows = []
        for key in keys:
            row = [key]
            for v in variants:
                c = mean(r["se_by_route"].get(key, 0) for r in grp["current"][(v, f)])
                n_ = mean(r["se_by_route"].get(key, 0) for r in grp["new"][(v, f)])
                row.append(f"{num(round(c, 1))} → {num(round(n_, 1))}")
            rows.append(row)
        w(table(["노선"] + [f"`{v}`" for v in variants], rows))
        line("")

    # ---------- 6. 해석·한계
    line("## 6. 해석과 한계", "",
         "- 새 규칙은 **차량을 가용 종료(11:00)까지 쓴다**는 가정이다. 운전자 근무시간·차량 반납 시각은 확인되지 "
         "않았다(§1 가정). ADR-001은 \"복귀 완료가 운영 종료를 넘는 운행은 생성하지 않는다\"(현행 규칙)를 적고 있다. "
         "비교 실험을 새 규칙으로 돌릴지는 팀 결정이 필요하다(`docs/adr/ADR-002.md` 초안, `fix/service-end-rule` "
         "브랜치). 코드의 기본값은 현행 규칙 그대로다.",
         "- 승객 발생은 두 규칙 모두 06:00~10:00 안이다. 새 규칙에서 남는 service_end는 마지막으로 10:00 전에 "
         "출발한 차가 그 정류장을 지난 뒤 온 승객과, 대기 한도 안에 차가 오지 않는 승객이다.",
         "- 새 규칙에서 M2 wait가 조금 는다. 사유 우선순위가 no_connection → service_end → journey → wait라서, "
         "현행 규칙에서 service_end였던 승객 중 새로 생긴 늦은 운행편이 대기 한도(1200초) 뒤에 오는 승객은 "
         "wait로 옮겨 간다. service_end 감소분이 모두 충족으로 바뀐 것은 아니다(§3의 served·wait 차이).",
         "- 10:00 뒤 구간시간은 09:00 시간대 값이다(관측값이 아니다, §1 가정). 10시대 혼잡이 다르면 10:00 뒤 도착 "
         "시각이 달라진다. 묶음에 10시 프로필이 들어오면 새 규칙 실행을 다시 돌린다.",
         "- 운행안이 하나(스모크 안)라 **순위 변화는 보이지 못한다.** 다만 현행 규칙은 순환이 긴 노선일수록 "
         "운영시간 후반을 더 많이 비우고(§4), 새 규칙은 그 차이를 줄인다. 비교안이 생기면 두 규칙으로 같이 돌려 "
         "순위가 바뀌는지 봐야 한다.",
         "- C의 근사 평가(`busstrike.planning`)는 현행 규칙(`departure + cycle <= end`)을 쓴다. 새 규칙 실행과 C 근사는 "
         "시간표가 같지 않다(simulator.md §1.3). 이번 작업은 planning 쪽을 바꾸지 않았다.",
         "- M6 거리는 직선거리 기준이다. 새 규칙에서 늘어난 거리는 10:00 뒤 운행분이다.", "")

    # ---------- 7. 재현
    line("## 7. 재현", "", "```sh",
         "# <DATA> = guro_0610_2.zip을 푼 폴더(guro_0610/), <OUT> = 저장소 밖 결과 폴더",
         "# 스모크 13대 안은 guro_exp_1026_30.md §9의 add_vehicles.py로 만든 것을 그대로 쓴다",
         "python3 experiments/guro_exp/make_rule_bundles.py --bundles <DATA>/bundles \\",
         "    --rule start_within_window_return_by_available --out <OUT>/bundles_new/bundles",
         "python3 experiments/guro_exp/run_matrix.py --bundles <DATA>/bundles \\",
         "    --plan smoke:11=<DATA>/smoke/plan_smoke_comparison.json --plan smoke:13=<OUT>/plan_smoke_f13.json \\",
         "    --out <OUT>/runs_current --data-label guro_0610_2.zip --jobs 6",
         "python3 experiments/guro_exp/run_matrix.py --bundles <OUT>/bundles_new/bundles \\",
         "    --plan smoke:11=<DATA>/smoke/plan_smoke_comparison.json --plan smoke:13=<OUT>/plan_smoke_f13.json \\",
         "    --out <OUT>/runs_new --data-label 'guro_0610_2.zip (+policy.service_end_rule 사본)' --jobs 6",
         "python3 experiments/guro_exp/report_service_end_rule.py --current <OUT>/runs_current --new <OUT>/runs_new \\",
         "    --previous <이전 실행 runs> --engine-commit <fix/service-end-rule 커밋> \\",
         "    --out experiments/reports/service_end_rule.md",
         "# --engine-commit을 쓰려면 그 커밋의 src/가 작업 트리에 있어야 한다(fix/service-end-rule 체크아웃)",
         "```", "",
         f"벽시계: 현행 {meta_c.get('wall_seconds')}초, 새 규칙 {meta_n.get('wall_seconds')}초(병렬 6).")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--new", required=True, type=Path)
    ap.add_argument("--previous", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--engine-commit", default=None,
                    help="실행 때의 미커밋 엔진 변경을 담은 커밋(작업 트리 src/가 이 커밋과 같아야 한다)")
    args = ap.parse_args()
    args.out.write_text(build(args.current.resolve(), args.new.resolve(),
                              args.previous.resolve() if args.previous else None, args.engine_commit),
                        encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
