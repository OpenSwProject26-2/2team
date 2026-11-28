# 충족률·형평성 곡선 패널 — D팀 대시보드 연결

2026-11-27. 노선 실험실 지도용으로 만든 곡선 패널(`web/lab/`)을 D팀 대시보드(MapLibre)에 넣는 법.

입력은 파이프라인(`python -m busstrike.pipeline`, 별도 PR로 올릴 예정)이 내는 두 가지다.
- `pareto.json`: 구·대수·평가일과 안마다 `served_rate`(충족률), `equity_far`(형평성, 역 800m 밖), `hull`(경계 위), `export_path`.
- `export/<plan id>.json`: 노선별 `shape`([lat, lon] 목록), `stops`(`kind: station`이면 역), `vehicles`, `headway_min`, `cycle_min`.

같은 형식의 실제 출력이 `guro_exp_work/routegen_integrated/factorial_1126/<구>/`에 있다(저장소 밖).

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
- **D팀 파일은 이 PR에서 고치지 않았다**(`web/operator`, `src/busstrike/api`). 실제 대시보드 연결 예시는 PR #59(#58 위).
