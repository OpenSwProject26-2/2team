# 충족률·형평성 곡선 패널 — 시작 전 확인(0절)

2026-11-27, 브랜치 `feat/pipeline-core`(PR B, 아직 main에 머지 전이라 그 위에 이어 붙임).

## 1. lab 화면 코드

| 항목 | 확인한 것 |
|---|---|
| 기존 lab 화면 | `web/operator/lab.html`, `web/operator/lab.js`(노선 실험실, `/lab`). `src/busstrike/api/d_dashboard.py`가 `--lab-all`로 함께 띄운다 |
| 지도 라이브러리 | **MapLibre GL JS**(Leaflet 아님). 운영자 화면은 `web/operator/vendor/maplibre-gl.mjs`(저장소에 포함), lab.html은 unpkg 6.18.0 |
| 노선 그리기 | lab.js 안의 `plan`·`plan-casing` 층(MapLibre line). 따로 떼어 쓸 수 있는 함수는 없음 |
| 데이터 읽기 | 서버 API(`/api/lab/*`)를 fetch. 정적 파일 아님 |
| 여는 방법 | 로컬 서버(`web/operator/serve.py … --lab-all …` → `http://127.0.0.1:<port>/lab`). file:// 아님 |
| 다크 모드 | 없음 → 패널도 라이트만(지시문 "있을 때") |

**구조 차이와 정한 것**
- 기존 lab 화면 파일이 `web/operator/` 아래에 있다. 지시문은 이 폴더(D팀)를 고치지 말라고 하므로 **lab.html·lab.js는 손대지 않고**, 같은 MapLibre를 쓰는 새 화면 `web/lab/index.html`(지도 전체 + 왼쪽 아래 패널 + 오른쪽 위 정보 카드)을 만들었다. `web/operator/vendor/`의 MapLibre 파일은 읽기만 한다.
- MapLibre에는 `L.Control`이 없으므로 같은 효과의 `map.addControl({onAdd, onRemove}, 'bottom-left')`(IControl)로 붙인다. 컨트롤 방식이 맞으므로 묻지 않고 진행했다.

## 2. 이미 있는 패널·지도 연결

- `mountParetoPanel`, `showPlan`: 저장소 어디에도 없음(새로 만듦).
- 운영자 대시보드 `web/operator/simulation.js`에는 manifest 안끼리 비교하는 작은 산점도(`renderComparisonPlot`)가 있지만, pareto.json·hull·형평성 기준 슬라이더·export 지도 연결은 없다. 이 작업에서는 고치지 않는다.

## 3. 입력 데이터

- `pareto.json`, `export/<plan id>.json`(pipeline.md 5절 형식)이 있다 → 변환 스크립트를 만들지 않았다.
  - 개발·확인: `guro_exp_work/routegen_integrated/factorial_1126/guro/`(안 75개, 경계 위 15개, 지자체안, 평가 10/27~30, 11대). 다른 구(강서·마포·노원·강북·종로·영등포)도 같은 형식.
  - 저장소 안 작은 예시: `tests/fixtures/pipeline/pareto_guro_smoke.json`, `export_guro_smoke.json`(이미 있음, 카드 ID 없음).
- `pareto.json`에는 지자체안 `export_path`가 없다(파이프라인은 지자체안 운행안을 내보내지 않음). factorial_1126에는 `export/muni.json`이 따로 있다 → 패널 옵션 `muniExportPath`(lab 화면 기본 `export/muni.json`)로 지자체안 점도 누를 수 있게 했다. 파일이 없으면 고를 때 패널에 오류 한 줄, 지도는 이전 안 그대로.
- `rep`(대표안 역할)는 pareto.json에 없다 → lab 화면에서는 표시하지 않는다. 운영자 대시보드 연결 예시(로컬)는 서버가 `demo/representative.json`의 역할을 `rep`로 덧붙여 준다.

## 4. 시제품 규칙

- 지시문의 시제품 `pareto_1125/figs/guro_pareto_select.html`은 **이 PC에 없다**(`pareto_1125/figs`에는 guro_pareto.html·guro_pareto_present.html 등만 있음).
  - 선택 규칙은 지시문 문장 그대로: 형평성 ≥ 기준인 안 중 충족률 최대, 동률이면 형평성 높은 쪽 → plan id 순.
  - 색 규칙은 같은 계열 그림 `factorial_1126/guro/figs/pareto.html`의 라이트 색: 섹터 순회 `#257de0`, 피더 순회 `#f47230`, 편도 노선 생성 후 순회 `#00b894`, 지자체안 `#52514e`(네모).
  - 툴팁은 지시문대로 알고리즘 이름·충족률·형평성(소수 한 자리).
- 시제품 대조(5절 3번)는 같은 규칙을 파이썬으로 따로 구현해 대조했다(PR 설명 표).
