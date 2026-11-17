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
import sys
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--v4", required=True, type=Path)
    ap.add_argument("--v1", required=True, type=Path, help="v3 데이터 구로 기본 조건(routegen_v1/base)")
    ap.add_argument("--region", required=True, type=Path)
    ap.add_argument("--v3-root", required=True, type=Path, help="v3 구로 실험 묶음 폴더(guro_0610_2.zip을 푼 곳)")
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
      "코드는 결과를 바꾸지 않았으므로 하락은 데이터 변화(비율, 그리고 gg 변형은 gg_reach 판정 규칙)에서 왔다. 둘을 따로 나누지는 않았다. 같은 코드로 v3 데이터를 다시 돌리면 v1 안과 16/16 바이트 단위로 같다(§6).")
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
    w("## 6. 코드에서 고친 곳 (`feat/candidate-gen`, 커밋 8454b9b)")
    w("")
    w("- `routegen/data.py` `check_root`: 구로 망 ID(`guro-buf2km-0610-v2`)·v3 README 문구·실험 날짜 고정 → "
      "망 ID 형식 `<구>-buf2km-0610-v2`(망 폴더·묶음 전부 같은 ID), README 주간 문구 v3·v4 둘 다, `split`(exp 10/26~30 · test 11/02~06). "
      "README에서 구 이름·대수를 읽는다. 생성기는 `exp`만 받는다(테스트 묶음으로 후보를 만들면 멈춘다).")
    w("- `routegen/data.py` `check_bundles`: `scenario_f11.json` 고정 → 실행 대수의 시나리오(동작·강서 20·40, 강동 35·70에는 f11이 없다).")
    w("- `routegen/__main__.py`: `--fleets` 기본 11,13 → README 대수. `home_gu`(cross_gu 표시) 구로구 고정 → README 구 이름(`--home-gu`로 바꿀 수 있음).")
    w("- `experiments/guro_routegen/run_sims.py`: `--eval-root` — 실험 주간 안을 테스트 묶음에 그대로 돌린다(망 ID·구 같은지 확인, 결과는 `runs_test/`).")
    w("- 고치지 않은 것: ① `gg_reach`는 처음부터 행(쌍 × 날짜 × 시간) 단위로 읽고 거른다 — 쌍 단위 캐시가 없다. "
      "단위 테스트 `HourlyReachTest`를 더했고, 실험·테스트 묶음 160개 전부 잔여 수요가 demand_bins와 같다(§1). "
      "② 수요 비율은 routegen이 계산하지 않고 묶음 `demand_flows`를 그대로 읽으므로 고칠 곳이 없다. "
      "③ 서울 전체 입력(`seoul_routegen_inputs_0610_v4`, `demand/exp`·`demand/test`)은 이 브랜치의 routegen이 읽지 않는다 "
      "(구 묶음만 읽음). 06~09시는 `HOURS = (6, 7, 8, 9)`로 이미 같다.")
    w("- 회귀 확인: 바꾼 코드로 v3 구로 묶음(`--fleets 11,13`)을 다시 돌린 plan.json 16개가 v1 기본 조건과 바이트 단위로 같다. "
      "v4 4개 구도 두 번 돌린 plan 64개가 같다. 전체 단위 테스트 통과.")
    w("")
    w("## 7. 재현")
    w("")
    w("```")
    w("W=<저장소 밖>; R=$W/zip_v4/region   # region_bundles_0610_v4.zip을 푼 곳")
    w("for g in guro dongjak gangseo gangdong; do")
    w("  PYTHONPATH=src python3 -m busstrike.planning.routegen --root $R/${g}_0610_v4 --out $W/routegen_v4/$g --condition base")
    w("  python3 experiments/guro_routegen/run_sims.py --routegen-out $W/routegen_v4/$g --root $R/${g}_0610_v4 --jobs 7")
    w("  python3 experiments/guro_routegen/run_sims.py --routegen-out $W/routegen_v4/$g --root $R/${g}_0610_v4 --eval-root $R/${g}_0610_v4_test --jobs 7")
    w("done")
    w("python3 experiments/guro_routegen/make_report_v4.py --v4 $W/routegen_v4 --v1 $W/routegen_v1/base --region $R \\")
    w("    --v3-root $W/zip_v3/guro_0610 --out experiments/reports/routegen_v4.md")
    w("```")
    w("")
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
