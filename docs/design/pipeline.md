# 파업 셔틀 파이프라인 (2026-11-25)

다음 실험(알고리즘 12종 × 형평 기준 단계)을 저장소 코드만으로, 진입점 하나로 돌리기 위한 설계 문서다. 노선 생성기 자체의 설계는 [routegen-design.md](routegen-design.md), 데이터 위치는 [routegen-data.md](routegen-data.md)에 있다.

## 1. 실행

```bash
PYTHONPATH=src python3 -m busstrike.pipeline --gu guro --strike 1.0 --fleet 11 --algos all \
    --floors none,0.50,0.52,0.54,0.56,0.58,0.60,0.62,0.64 --eval-days 2026-10-27..30 \
    --data-root <W> --sweep <W>/routegen_integrated/improve_1122/final/all25/guro/sweep \
    --muni <W>/routegen_integrated/improve_1122/final/all25/guro/muni07 --out <결과 폴더(저장소 밖)>
```
- `<W>` = 저장소 밖 작업 폴더(guro_exp_work). 데이터·결과는 Git에 올리지 않는다.
- `--stages demand,gen,eval,pareto,export`로 단계를 고를 수 있다. 끝난 단계·조합은 건너뛴다(이어 돌리기). 같은 `--out`에 다른 입력(구·파업·대수·날짜·데이터 경로)을 주면 멈춘다.
- `--par`(생성·배분 동시 실행 수, 기본 2), `--eval-groups`(평가 묶음 동시 실행 수, 기본 3).

## 2. 흐름

| 단계 | 내용 | 산출물(`--out` 아래) | 코드 |
|---|---|---|---|
| 1 입력 | 구, 파업 범위(1.0 = 전면, 0~1 = 노선 비율, 추첨 0번 고정 seed), 가용 버스 대수, 생성 수요 기간(실험 주간 묶음 10/26~30), 평가 날짜 | `run_config.json` | `pipeline/run.py RunConfig` |
| 2 수요 | 교통카드 집계 → 파업 몫 → 시내버스 흡수 → 마을·경기버스 정밀 흡수 뒤 셔틀 대상(생성기와 같은 수요) | `bundles/<망>/strike_<날짜>/` | `strike_eval.scenario_bundles`(그대로) |
| 3 후보 생성 | 3절 알고리즘 | `cand/cand_<알고리즘>.pkl` | `walk_eval.py --cand-cache` |
| 4 배분 | `allocate.Model` 하나로 노선 선택 + 대수, 형평 하한(4절) | `gen/<알고리즘>/floor_<기준>/plans/eqmc07-f<대수>.json` (+ plan_report.json) | `walk_eval.py --lean` |
| 5 시뮬레이션 | 변환기 → M05. 같은 plan(sha256)은 한 번만 | `eval/runs/` | `eval_rules.py`(그대로) |
| 6 검증 | M07. 평가 날짜 모두 통과한 plan만 7단계로 | `eval/results.json`(verify_pass) | `eval_rules.py` |
| 7 지표·파레토 | 5절 지표 → 지배당하지 않는 안 표시 | `metrics/<태그>/<날짜>.json`, `pareto.json` | `pipeline/metrics.py`, `diag.py`, `pareto.py` |
| 8 내보내기 | 운영자 대시보드용 pareto.json, 시민 길찾기용 운행안 | `export/<안 id>.json` | `pipeline/export.py` |

- 결정론: 난수 없음(파업 범위 추첨은 고정 seed 20261121), 동률은 (값, ID) 순. 같은 입력이면 같은 plan.json.
- 시뮬레이터(M05)·검증기(M07)·eval_rules는 부르기만 한다(고치지 않는다).
- 기존 동작(바꾸지 않음): walk_eval·eval_rules는 역 색인(정류장 → 역)을 만들 때 테스트 주간 묶음의 승객 흐름도 읽는다(`<구>_0610_v4_test`). 테스트 주간을 평가하지는 않는다. 평가 날짜는 실험 주간 안에서만 받고, 11/02~06은 거절한다.

## 3. 알고리즘 12종

후보 종류 3가지 × (호선 묶기, 소외 우선 삽입) 켬·끔. 두 옵션은 서로 독립이고 기본은 둘 다 끔. 12종 모두 배분기는 `allocate.Model`(노선별 대수는 배분기가 정한다, 개설 단위 ⌈순환 / 20분⌉).

| ID | 이름 | 후보 종류(walk_eval) | 생성 설정(--gen) | 실험 코드 |
|---|---|---|---|---|
| `sector_lg0_fill0` | 섹터 순회 | `--tour sector --tour-pool tour_sector` | — | pareto A3, tour_1124 T-sector |
| `sector_lg1_fill0` | 섹터 순회 + 호선 | 〃 | line_group | — |
| `sector_lg0_fill1` | 섹터 순회 + 소외 | 〃 | fill | — |
| `sector_lg1_fill1` | 섹터 순회 + 둘 다 | 〃 | line_group + fill | (가까운 것) newalgo S4·pareto A5 — 배차 예산·피더 풀이 같이 있어 같지 않음 |
| `feeder_lg0_fill0` | 피더 순회 | `--tour feeder --tour-pool tour_feeder` | — | pareto A2, tour_1124 T-feeder(구로 T-only) |
| `feeder_lg1_fill0` | 피더 순회 + 호선 | 〃 | line_group | — |
| `feeder_lg0_fill1` | 피더 순회 + 소외 | 〃 | fill | — |
| `feeder_lg1_fill1` | 피더 순회 + 둘 다 | 〃 | line_group + fill | (가까운 것) newalgo F4·pareto A4 — 배차 예산·tour_only가 같이 있어 같지 않음 |
| `oneway_loop_lg0_fill0` | 편도 노선 생성 후 순회 | (없음, R1 = gu_params_r1.json) | — | pareto A1, improve_1122 R1(gen_imp) |
| `oneway_loop_lg1_fill0` | 편도 후 순회 + 호선 | 〃 | line_group | — |
| `oneway_loop_lg0_fill1` | 편도 후 순회 + 소외 | 〃 | fill | — |
| `oneway_loop_lg1_fill1` | 편도 후 순회 + 둘 다 | 〃 | line_group + fill | — |

- 소외 우선 삽입 값(지금 G4 값 그대로): fill_bonus β = 1.0, fill_top = 5, fill_pool = 60. 호선 묶기 꼬리표 = `station_lines.csv` tags.
- 공통 조건: 순환 운행(고리 닫기 ≤ 300초), 버스 통행 도로망(road.json + turns.json)만, U턴 금지, 필수 시설 정차, 07:00 시작, 기존 길이·정차 상한. 편도 노선 생성 후 순회는 R1의 시작점 최적화와 길이 상한 목록(15·20·23·30분 상당)을 그대로 쓴다.
- 이름·ID는 코드 상수(`pipeline/algos.py`)·출력·이 문서에서 같다.

### 3.1 두 옵션의 규칙 (순회 생성기의 규칙을 옮김, 사용자 결정 2026-11-25)

**호선 묶기(line_group)** — 씨앗 하나에서 끝 역을 고르는 단계마다 같은 규칙:
- 수요 단위 = 꼬리표 t(허브가 하나라도 가진 꼬리표). 단위 역 = 꼬리표 t를 가진 역 전부. 권역·값 = 단위 역들의 권역·값 합.
- 끝 역 = 씨앗에서 망 거리 min(씨앗 → 역 access 정류장, 역 egress 정류장 → 씨앗)가 가장 작은 단위 역(동률 역 ID, `budget.nearest_end_station`). 기록 `lg:<꼬리표>`, `orig:<그 씨앗 수요가 가장 큰 역>`.
- 적용: ②-T 피더 순회(예전 그대로), ①-T 섹터 순회(단위의 정류장을 끝 역으로 나눈 뒤 그 역마다 예전 섹터 절차 — 중심 = 그 역 좌표, 값 = 단위 값), 편도 노선 생성 후 순회의 5단계 최단경로 피더(단위 = (꼬리표, 방향 access·egress)), 소외 삽입 골격.
- 적용 안 함: 편도 노선 생성 후 순회의 ① 섹터 삽입(겹침 군집 단위, 예전 그대로).

**소외 우선 삽입(fill)** — 순회의 규칙 그대로, 배차 예산만 뺌:
- 소외 수요 = 동네 끝 정류장이 역 지정 정류장 도로 도보 800m 밖인 OD의 시간대 가중 수요(운행 시작 07:00부터).
- 단위마다 삽입 후보 = (값 + β × 소외 수요) 큰 순 fill_pool개, 씨앗 = 값 큰 순, 채운 노선이 나온 씨앗 fill_top개까지.
- 탐욕 삽입: 점수 = Δ(탑승 근사 + β × 덮는 소외 수요) ÷ Δ순환(≤ 0이면 ∞), Δ값 ≤ 1e-9 버림, 동률 (점수, Δ값, −자리, 정류장 ID). 정류장 사이는 U턴 없는 최단경로.
- 탑승 근사(`budget.RideApprox`, 배분기 segment 방식과 같은 식)의 대수 n = max(1, ⌈순환 / 1200초⌉) = 배분기 개설 단위. 예전에는 n ∈ {1,2,3}, 한도 n × 20분(배차 예산)이었다.
- 한도 = 그 후보 종류의 기존 한도. 순회: 피더 골격과 같은 2L(L = 길이 목록 최댓값). 편도 노선 생성 후 순회: 필수 정류장 삽입과 같은 검사(가는 길 편도·정차·우회율·U턴 + `LoopBuilder.full_ok`의 돌아오는 길·고리 닫기·U턴).
- 골격과 시점:
  - 순회: ②-T 피더 골격 [역 E, 씨앗, 역 A]. origin `tour_fill`. 섹터 순회 + 소외도 같은 골격이다(예전 S4와 같음). 배분 풀 `tour_sector`·`tour_feeder`에 그 실행의 `tour_fill`이 들어간다.
  - 편도 노선 생성 후 순회: 5단계와 같은 최단경로 피더(가는 길) → 6b 수요 돌아오는 길 → 한 바퀴 정차열 전체를 앵커로 삽입(노선 모양이 정해진 뒤) → 6c 필수 정류장 → 6d 시작점 최적화(끼운 정류장까지 보고 시작점을 고른다). origin `fill`, 풀 `ours`. 원래 R1 후보는 그대로 두고 더한다.
- 대수는 고정하지 않는다(배분기가 정한다).
- 둘 다 끄면 예전과 바이트 동일(7절 관문 1).

## 4. 형평 기준(배분기 하한)

`--floors`(쉼표)의 값마다 하나의 배분(같은 후보 캐시).
| 값 | 뜻 | walk_eval 인자 |
|---|---|---|
| `none` | 전체·역 밖 하한 모두 끔 | `--equity-floor muni --far-floor none` |
| `muni` | 지자체안 값(같은 규칙으로 잰 전체·역 밖 형평, 예전 기본) | `--equity-floor muni` |
| 숫자(예 `0.58`) | 역 800m 밖 형평 ≥ 그 값(절대값), 전체 하한은 지자체안 값 | `--equity-floor muni --far-floor 0.58` |
| `+x` | 지자체안 역 밖 값 + x | `--far-floor +x` |
| `off` | 역 밖 하한 0(전체 하한은 지자체안 값) | `--far-floor off` |

실험 기본 단계(실험 지시문에서 다시 정함): none, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64. 하한을 못 맞춘 조합도 배분기의 최선안을 내고, pareto.json에 `floor_met: false`와 부족분(`short_far`, 명)을 남긴다.

## 5. 출력 형식

### 5.1 pareto.json (운영자 대시보드용)
대시보드에서 그리는 법과 주의점은 [d-pareto-handoff.md](d-pareto-handoff.md).
```json
{"gu": "guro", "fleet": 11, "strike": 1.0, "eval_days": ["20261028"], "demand_total": 9876.0,
 "plans": [{"id": "feeder_lg0_fill0__floor_none", "base": "feeder", "algo_name": "피더 순회", "line_group": false, "fill": false,
            "floors": ["none"], "plan_sha256": "…", "served_rate": 0.378, "equity_far": 0.49, "isolation": 0.51,
            "time_ratio_rode": 1.26, "time_ratio_common": 1.63, "refused_unserved": 12.0,
            "reasons": {"noconn": 0.0, "order": 0.0, "wait": 0.0, "refused": 12.0},
            "routes": 6, "cycle_sum_min": 300.0, "floor_met": true, "short_far": 0.0, "hull": true,
            "export_path": "export/feeder_lg0_fill0__floor_none.json"}],
 "muni": {"id": "muni", "served_rate": …, "equity_far": …, …},
 "excluded": [{"id": "…", "why": "M07 통과 3/4"}]}
```
| 필드 | 뜻 |
|---|---|
| `base` | sector \| feeder \| oneway_loop |
| `floors` | 이 plan을 낸 형평 기준들. 같은 알고리즘에서 같은 plan(sha256)은 한 항목으로 합친다(`floor_check`에 기준별 `floor_met`·`short_far`) |
| `served_rate` | 충족률 = Σ 도보 반영 탑승 ÷ Σ 셔틀 대상 수요(평가 날짜 합) |
| `equity_far` | 형평성(역 800m 밖) = 평가 규칙(same_line, 도보 400m)으로 탈 수 있게 된 역 밖 수요 몫 |
| `isolation` | 소외율 = 1 − equity_far |
| `time_ratio_rode` | 원래 버스 대비 시간(탄 승객 중앙값) = (도보 + 대기 + 차내) ÷ 원래 버스(대기 + 차내) |
| `time_ratio_common` | 같은 승객 기준(지자체안에서도 같은 날 탄 승객) 중앙값 |
| `refused_unserved` | 만차로 끝내 못 탄 승객(하루 평균) |
| `reasons` | 못 탄 사유(하루 평균): noconn = 동네 쪽 400m 안 셔틀 정류장 없음 또는 역 쪽 노선 없음, order = 둘 다 있으나 같은 노선·영업 구간에서 순서가 안 맞음, wait = 대기 한도 초과, refused = 만차 |
| `routes`, `cycle_sum_min` | 운행 노선 수, 순환 시간 합(분, M05 시간표) |
| `floor_met`, `short_far` | 배분기 하한 충족 여부, 역 밖 부족분(명) — 첫 기준 기준 |
| `hull` | 지배당하지 않음(다른 우리 안이 충족률 ≥, 형평성 ≥, 하나는 엄격히 큼이면 지배당함). 지자체안은 hull 계산에 넣지 않는다. 우리 안끼리의 경험적 비지배 경계이지 이론적 파레토 곡선이 아니다 |
| `excluded` | 평가 날짜 중 M07을 통과하지 못했거나 지표가 없는 안 |

### 5.2 export/<plan id>.json (시민 길찾기·지도용)
```json
{"plan_id": "feeder_lg0_fill0__floor_none",
 "routes": [{"route_id": "r01", "name": "r01 ○○정류장 순환(○○역 경유)", "vehicles": 2, "cycle_min": 38.5, "headway_min": 19.25,
             "stops": [{"seq": 1, "stop_id": "116000200", "name": "…", "lat": 37.49, "lon": 126.88, "kind": "station"}],
             "shape": [[37.49, 126.88], …], "shape_straight": 0}]}
```
- 정류장 이름·좌표 = 묶음 network/stops.csv, kind = 역 지정 정류장이면 station.
- shape = 정류장 사이 실제 버스 도로 좌표(`roadnet.RoadNet.path_any`), 고리를 닫는 마지막 → 첫 구간 포함. 지도 앱 길찾기를 거치지 않는다. 도로 경로가 없어 직선으로 이은 구간 수 = `shape_straight`.
- 순환 시간 = 생성기 순환 시간(배분기와 같은 값), 배차 = 순환 ÷ 대수.
- 카드 ID(TRCR_ID)는 어떤 출력에도 없다.
- 예시: `tests/fixtures/pipeline/`(구로 스모크 결과를 줄인 것).

## 6. 지운 것과 이유 (PR A `chore/pipeline-cleanup`)

| 지운 것 | 이유 |
|---|---|
| 배차 예산(`budget_h_s`, `budget_ns`, `budget_start_s`, `budget_alloc.FixedNModel`, `Candidate.vehicles`, `tour_only`) | F 사다리 F1→F2에서 충족률 −0.2%p, 형평성 −1.6%p, 대기·만차로 못 탄 승객 약 −6%. 배분기가 이미 배차 20분을 보장해 역할이 겹침 |
| 역 쌍 순회(`tour` pair)와 역 쌍 병합(`merge`) | 실험 12종에 없음 |
| 후보 풀 합집합(`--tour-pool tour`, `all`) | 실험은 한 종류 풀만 |
| 상위 k개(`--topk`, `Model.banned`), U턴 허용(`allow_uturn`) | 안 씀, 공통 조건이 U턴 금지 |
| lab 스크립트 `summarize_sweep.py`, `summarize_walk.py`, `run_all_gus.py` | 일회성, 참조 0건 |
| `routegen/delay.py`(평소 대비 지연 도구)와 `test_delay.py` | 평상시 표가 오지 않아 실행된 적 없음, 참조 0건. 같은 질문을 지표 `time_ratio_rode`·`time_ratio_common`이 답함 |
| `routegen/__main__.py`(`python -m busstrike.planning.routegen`, v0~v4 CLI) | 저장소 안 import 0건, 옛 보고서 재현 명령에만 쓰임(그 보고서에 47ed2bd 이전 커밋에서 재현하라고 안내). 지금 진입점은 `python -m busstrike.pipeline` |

## 7. 관문(검증)

| 관문 | 방법 | 결과 |
|---|---|---|
| 1 바이트 동일 | 두 옵션 끔·형평 하한 = 지자체안 값에서 세 후보 종류 plan을 기준 plan과 `cmp` | 2026-11-25 구로 11대: 편도 노선 생성 후 순회 = improve_1122 gen_imp, 피더 순회 = tour_1124 gen_Tfeeder, 섹터 순회 = tour_1124 gen_Tsector, 모두 동일 |
| 2 숫자 재현 | 구로 10/27~30: 피더 순회 충족률 37.80%·소외율 51.0%, 편도 노선 생성 후 순회 33.99%·42.5% (±0.1%p) | 37.80%·51.0%, 33.99%·42.5%(차이 0), M07 4/4 |
| 3 시뮬레이터·검증기 그대로 | `git diff origin/main -- src/busstrike/sim src/busstrike/evaluation experiments/routegen_lab/eval_rules.py` 비어 있음 | 비어 있음 |
| 4 테스트 | `PYTHONPATH=src python3 -m pytest tests` | PR 설명 참고(기존 문제 1건 `test_d_dashboard::test_zip` 제외 통과) |
| 5 스모크 | 구로 10/28 하루, 11대, 12종 × 기준 없음, 2~8단계, M07 통과 | 12종 모두 M07 통과, 내보내기 12개 |

## 8. D팀 대시보드 연결 (충족률·형평성 곡선 패널)

노선 실험실 화면용으로 만든 곡선 패널과 지도 그리기 모듈을 D팀 대시보드(MapLibre)에 그대로 넣는 법. 빌드 도구 없이 ES module로 넣고, 외부 파일(CDN·글꼴·아이콘)을 받지 않는다. 화면 예시는 `python3 web/lab/serve.py --run <파이프라인 --out 폴더>` → `http://127.0.0.1:8795/`(시작 전 확인 기록은 [web/lab/UI_INVENTORY.md](../../web/lab/UI_INVENTORY.md)).

| 넣을 파일 | 하는 일 |
|---|---|
| `web/lab/pareto_panel.js`, `pareto_panel.css` | 지도 왼쪽 아래 패널: 점(경계 위 진하게, 나머지 옅게, 지자체안 네모), 경계 계단선, 형평성 기준 슬라이더, 접기·펴기. 지도 그리기를 모른다 |
| `web/lab/plan_layer.js` | `export/<plan id>.json`을 MapLibre 층으로: 노선별 색 선, 빈 차 구간(`shape_return`, 있을 때) 옅은 점선, 정류장 점, 역 고리, 진행 방향 꺾쇠, 소외 우선 삽입 정류장(`fill_inserted`, 있을 때) 마름모. 패널을 모른다 |

```html
<link rel="stylesheet" href="/lab-panel/pareto_panel.css">
<script type="module">
import {mountParetoPanel} from '/lab-panel/pareto_panel.js';
import {showPlan} from '/lab-panel/plan_layer.js';
const cache = new Map();
const panel = mountParetoPanel(map, {            // map = 이미 만든 maplibregl.Map
  paretoUrl: '/data/guro/pareto.json',           // 또는 paretoData: 객체(file://로 열 때)
  storageKey: 'lab.paretoPanel.open',
  onSelect: async (plan) => {                    // plan.export_url = pareto.json 기준으로 푼 export 주소
    const ex = cache.get(plan.id) ?? await (await fetch(plan.export_url)).json();
    cache.set(plan.id, ex);
    showPlan(map, ex, {fit: false});             // 다시 부르면 이전 층을 지운다
  }});
</script>
```

- **데이터 경로**: `pareto.json`과 그 파일 기준 상대 경로 `export/<plan id>.json`(pareto.json의 `export_path`)만 읽는다. 서버 계산 없음. 주소 규칙이 다르면 `exportUrl: (plan) => '…'`로 바꾼다. 지자체안 점을 누르게 하려면 `muni.export_path` 또는 `muniExportPath`.
- **이벤트**: 패널 → `onSelect(plan)` 하나. 지도 쪽에서 패널을 부를 때는 `panel.select(id)`, `panel.setOpen(bool)`, `panel.getSelected()`, `panel.clear()`, `panel.error(text)`, `panel.destroy()`.
- **옵션**: `initialPlanId`(예: 주소 해시 `#plan=<id>`), `autoSelect`(기본 true = 처음에 경계 위 충족률 최대 안을 고름. false면 사용자가 고를 때까지 지도를 건드리지 않음), `position`(기본 `'bottom-left'`). HTML 요소를 넘기면 지도 컨트롤 대신 그 안에 붙는다.
- **localStorage 키**: 접기·펴기 상태 `lab.paretoPanel.open`(`'1'`/`'0'`). 처음 방문이면 화면 너비 ≥ 768px에서 펴짐. 저장소가 막혀도 동작한다.
- **선택 규칙**(슬라이더): 형평성 ≥ 기준인 안 중 충족률 최대, 동률이면 형평성 높은 쪽 → plan id 순. 점을 직접 누르면 슬라이더는 그 점의 형평성으로 옮겨질 뿐 다시 고르지 않는다. 단위 테스트 `tests/web/test_pareto_panel.py`.
- **배치 주의**: 패널은 지도 컨트롤 영역(왼쪽 아래)에 붙는다. 조작 패널이 지도 위에 떠 있는 화면이면 그만큼 `.pp-root`의 `margin-left`를 늘린다. 축척·저작권은 오른쪽 아래에 둔다(좁은 화면은 축척을 위로).
- **D팀 파일은 이 PR에서 고치지 않았다**(`web/operator`, `src/busstrike/api`).
