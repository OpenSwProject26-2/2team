# 운영자 대시보드 E2E: 입력 → 자동 조립 → 실행 버튼 → 곡선 → 확정 (2026-11-27)

운영자 대시보드(web/operator, PR #58·#59)와 충족률·형평성 곡선 패널(PR #60)에 **빈 곳만 이은** 작업이다. 새 화면을 따로 만들지 않고, #59 시뮬레이션 화면의 머리줄 아래에 **지표 띠**를 붙였다. 시민 쪽(셔틀 GTFS, OTP 파업 그래프 교체)은 별도 작업이고, 이 문서는 **확정 묶음**(이음새 계약)까지 다룬다.

```
[새 시뮬레이션] 입력 ─→ busstrike.data.assemble(입력 검사·manifest·입력 해시)
        │                       │ 같은 작업 해시 + 끝난 결과 → 바로 연다(이전 결과 재사용)
        ▼                       ▼
  POST /api/d/runs ──→ 대기열(동시에 1개) ──→ python -m busstrike.api.d_runjob <run_dir>(별도 프로세스)
                                              조립 → 생성 → 배분 → 시뮬레이션(M05) → 검증(M07) → [형평 단계] → … → 지표
                                              <pareto_root>/<구>/runs/<run_id>/{pareto.json, export/, status.json, run.log}
        ▼
  지표 띠 + 곡선 패널(run 고름) ── 점을 고름 ──→ [이 안으로 확정 → 시민 경로에 적용]
        ▼
  GET /api/d/confirm/preview(사전 검사) → POST /api/d/confirm
        → <confirm_root>/<구>/<confirm_id>/(confirm.json + 다섯 파일), CURRENT, data/d_selection.json(pareto_selected)
        → POST <시민 서버>/api/citizen-routing/apply (없거나 꺼져 있으면 "시민 경로 반영 대기")
```

## 1. 실행과 설정

```bash
cp config/local_paths.example.toml config/local_paths.toml   # 경로 채우기(커밋 안 함)
PYTHONPATH=src python3 web/operator/serve.py --port 8780 [기존 인자들 …] [--local-paths config/local_paths.toml] [--no-runs]
# 브라우저: http://127.0.0.1:8780/simulation → 구 선택 → 지표 띠·곡선 패널, 머리줄 "＋ 새 시뮬레이션"
```
- `config/local_paths.toml`이 있으면 작업 실행기·확정을 켠다. 없으면 입력 단추가 "연결 안 됨"이고 기존 화면은 그대로다.
- `--no-runs`: 작업을 받기만 하고 돌리지 않는다(같은 결과 폴더를 쓰는 서버를 둘 띄울 때 하나만 돌리게).
- 경로 키: `work_root`(region_all·osm·facilities), `pareto_root`(실행 결과), `confirm_root`(확정 묶음, 시민 쪽과 같은 경로), `precomputed_root`("전체" 설정의 미리 계산된 결과), `sweep`·`muni`(`{gu}` 자리표시), `[citizen] api·ui`, `[run] par·eval_groups`(3 상한).

## 2. 입력과 데이터 자동 조립 (`busstrike.data.assemble`)

| 입력 | 값 | 비고 |
|---|---|---|
| 구 | 25개 구 | 이번엔 구로만 실행(`d_runs.ENABLED_GU`), 나머지 "준비 중" |
| 파업 범위 | 전면 / 비율 + 시드 | 시드는 파이프라인 고정값 20261121만(`strike.draw_strike` 추첨 0번) |
| 가용 대수 | 기본 = 지자체안 대수(구로 11) | `muni07/plan.json` 대수 합 |
| 생성 수요 기간 | 10/26~30 | 파이프라인 `demand_period=exp`만 지원 |
| 평가 날짜 | 빠른 = 10/28, 전체 = 10/27~30 | 실험 주간 안에서만. **테스트 주간 11/02~06은 화면·API·assemble 모두에서 거절** |
| 실행 설정 | 빠른 / 전체 | 3절 |

`assemble(gu, strike, demand_days, eval_days)`는 D1(구 실험 주간 묶음: 정류장·역·소요시간·수요 원자료 집계·날짜별 수요 묶음), D2(버스 통행 도로망 `net/road.json` + `turns.json`, OSM 도로, 필수 시설, 구별 설정·호선 꼬리표), D3(지자체안 `plan.json`·`road_muni.json`)을 검사해 `<pareto_root>/_assemble/<구>/<입력 해시 16자>/manifest.json`(파일별 크기·수정 시각·sha256)을 남긴다. 하나라도 없으면 `AssembleError(missing=[{item, path, how}])` — 화면에는 무엇이 없고 어떻게 만드는지가 뜬다.
- 입력 해시 = sha256(구, 파업, 기간, manifest 파일 해시, 코드 커밋). 코드 커밋은 `git rev-parse HEAD`(+ `src/`·`experiments/`·`citizen-routing/backend/`에 커밋 안 한 변경이 있으면 `-dirty`). 화면 파일만 바뀌면 해시는 그대로다.
- sha256은 (경로, 크기, 수정 시각)이 같으면 `sha_cache.json`에서 재사용한다.

## 3. 실행 버튼 (작업 실행기 `d_runs`, `d_runjob`)

| API | 하는 일 |
|---|---|
| `GET /api/d/runs/config` | 입력 화면 고정 값(구 목록·실행 가능 여부, 기본 대수, 날짜, 테스트 주간, 미리 계산된 결과 유무, 시민 화면 주소) |
| `POST /api/d/runs` | 입력 → 조립 → 작업(run_id). 같은 작업 해시가 끝났으면 `reused: true`, 실패·취소였으면 같은 폴더에서 이어 돌림(`resumed`) |
| `GET /api/d/runs/{id}` | 상태(queued·assembling·generating·allocating·simulating·verifying·metrics·done·failed·cancelled), 단계별 진행(done/total), 진행률, 남은 시간 추정, 로그 끝 8줄 |
| `POST /api/d/runs/{id}/cancel` | 작업 프로세스 그룹에 SIGTERM, 상태 cancelled |
| `GET /api/d/runs?gu=` | 목록 |

- 파이프라인은 `python -m busstrike.api.d_runjob <run_dir>`(새 세션)로 돈다. 서버를 꺼도 계속 돌고, 다시 켜면 살아 있는 프로세스를 이어 지켜보고, 죽어 있으면 대기열로 돌려 같은 폴더에서 이어 돌린다(파이프라인이 끝난 조합·평가를 건너뜀).
- 동시에 1개(대기열). 같은 구 파이프라인이 겹치지 않는다.
- 남은 시간 = 같은 설정의 마지막 완료 작업의 단계별 시간(없으면 기본값) × 남은 몫.
- 로그(`run.log`)에는 단계·개수·시간만 쓰고, 내보낼 때 카드 ID·승객 ID처럼 보이는 줄은 거른다.
- 테스트 주간: 작업 실행기는 파이프라인을 `test_flows=False`(`--no-test-flows`)로 돌린다 — walk_eval에 실험 주간 묶음만 링크한 `<run_dir>/region_exp`를 주고, eval_rules의 `test_root`는 실험 주간 묶음 자신(같은 흐름을 두 번 더해도 역 고르기는 유량 비율로 정해져 결과가 같다). 기본 CLI 동작(요인 실험 재현)은 그대로다.

### 단계

| 단계 | 내용 | 진행 단위 |
|---|---|---|
| 조립 | assemble + 수요 묶음(`Pipeline.bundles`) | 2 |
| 생성 | 알고리즘마다 후보 생성 + 형평 기준 none 배분(후보 캐시) | 알고리즘 수 |
| 배분 | 역 밖 형평 하한 1.0 배분(형평 상단 hi 찾기, 요인 실험 부록 B) | 알고리즘 수 |
| 시뮬레이션·검증 | none 안 + 지자체안 M05·M07 | 평가 묶음 수 |
| (형평 단계) | lo = none 안 중 충족률 최대 안의 형평성(1%p 올림), hi = 하한 1.0 배분 형평성 최댓값(1%p 내림), 그 사이 n단계(요인 실험 지시문 2.2절 규칙, 0.5%p 반올림, 마지막 = hi, 좁으면 0.01 간격) → 배분 → 시뮬레이션 → 검증 | |
| 지표 | `pareto.json`, `export/` | 2 |

- **빠른**: 피더 순회 (0,0)·피더 순회 (1,1)·섹터 순회 (1,1)·편도 노선 생성 후 순회 (0,0) × 형평 기준(none, 1.0, lo~hi 3단계) × 평가 1일(10/28), 병렬 3.
- **전체**: 미리 계산된 결과(`precomputed_root/<구>/pareto.json`, 요인 실험)가 있고 조건(대수·파업·평가 날짜)이 같으면 그것을 여는 작업(다시 계산하지 않음, 화면에 "미리 계산된 결과"). 새로 돌리기는 12종 × (none, 1.0, 8단계) × 10/27~30.
- M07을 통과하지 못한 안은 `pareto.json` `excluded`로 빠지고, 지표 띠 오른쪽에 "M07 미통과 제외 N개"로 보인다.

## 4. 곡선 자료 (`d_pareto`)

- `GET /api/d/simulation/pareto?region=<구>[&run=<run_id>]`, `…/pareto-export?region&id[&run]`. `run`이 없으면 예전 그대로 `<pareto_root>/<구>/pareto.json`.
- 안마다 `refused_rate`(만차 거절률 = `refused_unserved` ÷ `demand_total`, 둘 다 하루 평균 명)를 덧붙인다. 지표 띠·확정 묶음이 같은 함수(`metrics_of`)를 쓴다.

## 5. 지표 띠(화면)

시안 "현재 시뮬레이터 + 지표 띠" 그대로 #59 시뮬레이션 화면 머리줄 아래 한 줄(`web/operator/e2e.js`, `e2e.css`, `index.html`). 시뮬레이션 모드에서만 보인다.

| 칸 | 값 |
|---|---|
| 곡선에서 고른 안 | 안 이름, "형평 기준 · 대표 역할 · 구 대수 · 평가 날짜", 곡선 출처 고르기(기본 곡선 / 실행 결과 / 미리 계산된 결과) |
| 충족률 [주 지표] | `served_rate`, 지자체안 대비 %p(같은 실행의 지자체안) |
| 형평성 (역 800m 밖) [주 지표] | `equity_far`, 소외율 `isolation`, 지자체안 대비 %p |
| 만차 거절률 [보조 지표] | `refused_rate`(없으면 "—", "자료 없음") |
| 이동시간 비 [보조 지표] | `time_ratio_rode`(탄 승객 중앙값) |
| 오른쪽 | M07 배지(k/k 초록, 아니면 빨강), 안내·제외 수·지금 적용 중인 확정, **"이 안으로 확정 → 시민 경로에 적용"**(M07 실패 안·지자체안이면 비활성) |

- `simulation.js`와는 이벤트로만 잇는다: 곡선을 읽으면 `pp:data`, 점·슬라이더로 고르거나 내리면 `pp:select`. 실행 결과를 고르면 `window.ppRun = run_id` + `pp:reload`.
- 실행 중인 작업을 고르면 지표 카드 자리에 진행 막대(단계 칩·진행률·지난/남은 시간·로그 끝 3줄·실행 취소)가 뜨고, 끝나면 그 곡선으로 바뀐다. 주소 `?run=<run_id>`로 바로 열 수 있다.

## 6. 확정과 확정 묶음 (`d_confirm`, 이음새 계약)

```
<confirm_root>/<gu>/<confirm_id>/          confirm_id = YYYYMMDDTHHMMSS(KST)-<plan_sha256 앞 8자>
  confirm.json        docs/schemas/confirm.schema.json
  scenario.json       M05 시나리오(그 안을 평가한 실행: <out>/bundles/road/strike_<날짜>/scenario_f<대수>.json)
  plan.json           C 형식 운행안(파이프라인 plan + scenario_id + status "candidate" + source{plan_ref, run_id, plan_sha256})
  timetable.json      M05가 쓴 시간표(<out>/eval/runs/…/run/timetable.json)
  verifier_report.json  M07 보고서(pass=true, failed_checks 0)
  stops.csv           stop_id, stop_lat, stop_lon, stop_name — 시간표의 모든 영업 정차점
<confirm_root>/<gu>/CURRENT                지금 적용 중인 confirm_id 한 줄
```
- 대표 날짜: 평가 날짜에 10/28이 있으면 그날, 아니면 첫 평가 날짜(시나리오·시간표·M07 보고서가 하루치라서).
- 확정 전 검사(모두 통과해야 씀): 시나리오 ID(실행 기록 ↔ 시나리오 파일), M07(평가 날짜 모두 통과), **순환 운행**(노선마다 영업 구간 1개 + 고리 닫는 빈 차 구간 ≤ 300초, 편도 구간 섞이면 거절), 시간표 plan_id·revision = 운행안, 정차점 좌표, `citizen-routing/backend/shuttle_export.export_checked`를 같은 코드로 임시 폴더에 실행(GTFS는 바로 지움), confirm.json 스키마.
- 쓰기는 `.<id>.partial` 폴더에 다 쓴 뒤 이름 바꾸기, CURRENT도 임시 파일 → 이름 바꾸기. 기록은 지우지 않고 쌓는다.
- `data/d_selection.json`: 기존 v4 선택(`selected`)은 그대로 두고 `pareto_selected`와 `history`(kind=pareto_confirm)에 더한다. `approved_for_service: false`.
- 그다음 `POST <citizen.api>/api/citizen-routing/apply {gu, confirm_id}`. 주소가 없거나 응답이 없으면 "시민 경로 반영 대기"로 표시하고 확정은 그대로 둔다.
- 주의: 파이프라인 plan_id는 대수별로 같은 이름(`plan-eqmc07-f11`, revision 1)이다. 서로 다른 안을 구별하는 값은 `confirm_id`와 `plan_sha256`이다.

## 7. 지킨 것

- 시뮬레이터(M05)·검증기(M07)·eval_rules·planning/·sim/·evaluation/은 고치지 않았다(부르기만).
- 데이터·실행 결과·plan.json·확정 묶음·`config/local_paths.toml`은 커밋하지 않는다(저장소 밖 경로).
- 카드 ID·승객 단위 정보는 화면·API·로그·확정 묶음에 없다(테스트 `tests/api/test_d_runs_http.py`가 응답 문자열을 검사).
- 테스트 주간(11/02~06) 수요·평가 자료는 열지 않는다(위 3절).
