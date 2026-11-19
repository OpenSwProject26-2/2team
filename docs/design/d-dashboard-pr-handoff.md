# D 운영자 대시보드 PR 인수인계

2026-11-18 기준. 이 문서는 **저장소에 올릴 코드**와 **저장소 밖에서 별도로 연결할 데이터**를 구분한다. D 화면은 저장된 C/B/M07 결과를 조회하며, 조건을 바꿀 때 시뮬레이션을 새로 실행하지 않는다.

## 실행 범위

| 연결 자료 | 화면에서 확인 가능한 것 | 연결되지 않은 것 |
| --- | --- | --- |
| 저장소 코드만 | 합성 DEMO, PR #41 보고서의 구로·동작·강서·강동 5일 평균 KPI·비교 그래프 | 실제 A GTFS, 정류장별 실험 수요, C 노선·차량, 날짜별 M05/M07 |
| A 배포 ZIP + `seoul_transit_data.Zip` | 실제 A 정류장·역과 정차 순서, 구 경계·교통망, 네 구의 시나리오·날짜·시간별 **셔틀 투입 전 잔여 수요 원** | 차량·운행안에 따른 셔틀 운행 후 정류장별 미충족 변화 |
| 위 자료 + C/B/M07 실행 원본 | 해당 조건의 C 후보·계획·차량 배분, 날짜별 M05 지표와 정류장별 미충족 원, M07 검사 상태 | 원본이 없는 다른 조건의 공간 결과, 검증된 도로·철도 주행 선형 |

구로 `affected` 2026-10-26의 11·13대 × ours/sp **네 건**은 제공된 입력과 현재 C/B/M07 코드로 로컬에서 새로 계산했다. 네 건 모두 M05 성공, M07 `pass=true`이며 `fully_verified=false`, V12·V15·V16 미검증이다. Windows 기본 `cp949`에서 M07이 UTF-8 파일을 읽지 못해 같은 M05 출력을 `py -3 -X utf8 -m busstrike.evaluation.verify`로 다시 검증했다. 이 실행은 **PR #41의 원본 640건 중 복사본이 아니다.** 결과 파일은 OneDrive 밖 임시 폴더에만 있으며 PR에 포함되지 않는다. 임시 폴더가 정리되면 이 네 건의 날짜별 시각화도 사라진다.

## 로컬 실행

저장소 루트에서 `py -3 web/operator/serve.py`를 실행하고 `http://127.0.0.1:8766/simulation`을 연다. 다른 PC에서는 아래 경로를 실제 로컬 경로로 바꾼다.

```powershell
py -3 web/operator/serve.py `
  --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" `
  --transit-data "C:\path\to\seoul_transit_data.Zip" `
  --simulation-results "C:\path\to\routegen_v4" `
  --simulation-inputs "C:\path\to\region_bundles_v4"
```

`--transit-data`는 `TRANSIT_DATA_ROOT` 환경변수로도 지정할 수 있다. 미지정 시 사용자 Downloads의 `seoul_transit_data` 폴더 또는 ZIP을 탐색한다. `--simulation-results` 아래에는 `<구>/manifest.json`, `candidates/*.json`, `plans/*.json`, `runs/<variant>/runs.json`, `runs_test/<variant>/runs.json`, 각 실행의 `run/metrics.json`, `run/passenger_outcomes.csv`, `verifier_report.json`, `plan_m05.json`이 필요하다. `--simulation-inputs` 아래에는 `<구>_0610_v4/network/stops.csv` 및 `network/travel_times.json`이 필요하다. 원본 배치는 [시뮬레이션 계약](d-simulation-contract.md)에 적었다.

## 화면 해석과 참고사항

- 수요 입력의 시간은 **06:00~10:00**이다. `bin_start_s=21600,25200,28800,32400`, 각 `bin_width_s=3600`이며 24시간 자료가 아니다.
- 네 변형 `affected`, `affected_maeul`, `affected_gg`, `affected_maeul_gg`는 C의 `maeul_reach`·`gg_reach` 흡수 가능 수요 제외 조건이다. 지하철 중단 변형은 없다. 지도 표시 옵션에서 지하철을 숨겨도 수요·충족률은 재계산되지 않는다.
- **관측 승차(k5)**, **셔틀 투입 전 파업 잔여 수요**, **M05 셔틀 투입 후 미충족 수요**는 서로 다르다. 시뮬레이션 기본 빨간 원은 입력 번들의 잔여 수요다. 해당 날짜의 `passenger_outcomes.csv`가 연결되면 출발 정류장별 M05 미충족 원으로 바뀐다. 총 충족률을 정류장별로 임의 배분하지 않는다.
- 보고서 충족률은 `M1 served / M1 total`이며 분모는 선택한 실행의 잔여 수요다. 구·시나리오·기간마다 분모가 달라 직접 비교할 때 조건을 같이 본다. 5일 평균 보고서 값과 개별 날짜 M05 원본은 화면에서 출처를 나눈다.
- 지도 표시 옵션의 `잔여·미충족 수요 원`으로 원을 끄고 켠다. 조건 변경 시 원 반지름을 약 0.5초 보간한다. M05 공간 결과가 양쪽 조건에 있으면 달라진 정류장을 굵은 테두리로 강조한다.
- A GTFS에는 `shapes.txt`가 없다. A 버스·철도 점선은 정차 관계 개략도다. C 노선에는 정류장과 차량 수를 표시하지만 검증된 도로 선형이 없으면 실제 운행 경로 선을 그리지 않는다. A/C 정류장 ID도 자동으로 동일시하지 않는다.
- M07의 `pass=true`는 `fully_verified=true`와 다르다. 선택 실행의 실패·미실행·미검증 검사와 입력 버전을 함께 확인한다. 화면의 운행안 `selected`는 실제 운행 승인이나 시민 안내 확정이 아니다.

## PR에 포함할 것과 외부 인수물

운영자 화면과 API 코드, `web/operator/vendor/`의 로컬 MapLibre 파일 및 라이선스, 작은 자치구 경계 GeoJSON, D 테스트·문서를 함께 포함한다. 현재 새 파일 중 일부는 Git **미추적**이므로 PR 작성자가 파일 목록을 확인해야 한다. `seoul_transit_data.Zip`, A 배포 ZIP, 원본 카드 자료, `.keys`, 재계산 실행 파일과 개인 식별자는 PR에 넣지 않는다. 외부 자료는 읽기 전용 경로로 연결한다.

네 구 전체의 차량·운행안별 빨간 원을 보려면 C/B팀에서 PR #41 실행 결과 루트 전체를 받아야 한다. 특히 구별 `manifest.json`·계획·후보, 실험/테스트 `runs.json`, 각 실행의 `metrics.json`·`passenger_outcomes.csv`·`verifier_report.json`·`plan_m05.json`, 일치하는 `network_snapshot_id`의 정류장 좌표·이동시간 자료가 필요하다. `passenger_outcomes.csv`는 로컬에서만 읽어 출발 정류장별로 집계하며 승객 ID를 API로 반환하지 않는다. 공용 서비스로 배포하려면 별도의 비식별 집계본과 접근 통제가 필요하다.

## 확인

- D API 단위·HTTP 테스트 37개 및 시민 경로 테스트 30개 통과, `git diff --check` 통과.
- 로컬 재계산 구로 네 건의 M05·M07 API 상태 `passed`, 정류장별 공간 결과 `available` 확인. 차량 11→13대와 ours↔sp 전환에 따라 실제 원본 집계가 다른 정류장을 확인.
- 4개 구 × 4개 변형 × 실험/테스트 32조건에서 입력 `demand_bins.csv` 합계와 PR #41 보고서 잔여 수요 분모가 일치함을 확인.
- JavaScript 문법, 원 표시 토글, 반지름 보간, 실행 결과가 있는 날짜 자동 선택을 스크립트로 확인. Chrome 자동 브라우저 검사는 빈 출력을 반환했으므로 화면 렌더링 검증으로 간주하지 않는다.

수동 확인: `/simulation`에서 구로 `affected` 실험을 선택한다. 날짜별 원본 경로가 연결된 환경이면 2026-10-26을 선택하고 11↔13대, ours↔sp를 바꿔 미충족 원·노선별 차량 수·검증 상태를 본다. 다른 지역과 테스트 기간은 원본 부재 상태가 명확히 표시되는지 확인한다. 지도 표시 옵션의 원 체크를 끄고 켠 뒤 기존 DEMO/A 화면으로 돌아가 기능을 확인한다.

## PR 본문 참고

## 이번 GIS 선형 추가와 팀 공유 자료

`--rail-gis-live` 또는 `--rail-gis-file`을 지정하면 A GTFS 1·2·3·4·5·6·8·9호선을 Esri Korea의 실제 물리 철도 선형으로 표시한다. 7호선은 GTFS 역 한 곳이 GIS 선형에서 약 1.1km 떨어져 자동 검증에 실패하므로 기존 점선 개략도를 유지한다. 버스는 `web/operator/build_bus_roads.py`의 일회성 OSRM 전처리에서 **마을 3·시내 4개**가 검증을 통과했고 `--bus-geometry-file`로 연결된다. 버스 공식 선형 0개, OSM relation 검증 0개이며 구로01은 도로를 따르는 `osrm_estimate`다. 공식 운행 경로라고 표시하지 않는다. 결과 GeoJSON은 OS 임시 폴더에 있고 PR에는 포함되지 않는다. [자료 분석과 검증 조건](d-transit-data-integration.md)을 참고한다.

팀원이 최종 운영자 화면을 재현하려면 PR 코드 외에 별도 로컬 공유가 필요하다: (1) A GTFS 배포 ZIP, (2) `seoul_transit_data.Zip` 및 문서상 허용된 접근 권한, (3) 4개 구의 C/B/M07 원본 결과 전체 트리와 동일 버전의 `region_bundles_v4` 입력, (4) 오프라인 철도 GIS를 쓰려면 공식 레이어에서 받은 GeoJSON과 재사용 조건, (5) 버스 실선이 필요하면 OA-15264 또는 T-DATA 1056/1058 인증키를 각자 환경변수로 발급받고 노선 ID·정류장 순서 매핑 표를 확보해야 한다. `.keys`, 원시 카드 자료, 대용량 ZIP 및 실행 결과 원본은 PR에 올리지 않는다. `passenger_outcomes.csv`가 없는 실행은 정류장별 빨간 미충족 수요 원을 정확하게 만들 수 없다.

## 2026-11-19 worktree 검증 보충

- PR #46 기능은 현재 worktree에 이미 존재했다. `origin/main`과 비교한 PR #47 핵심 추적 파일(`d_simulation.py`, `simulation.js`, 시뮬레이션 테스트)은 통합 후 `origin/main`과 동일한 내용이다. Git merge/commit은 하지 않았다.
- PR #48의 25개 OSM 경계는 `districts_all.geojson`으로 런타임에 연결했고, 기존 4개 구 전용 `districts.geojson`은 보존했다. MapLibre 로컬 JS·worker·CSS·스타일 파일은 `/vendor/`에서 HTTP 200으로 제공되는 것을 확인했다. 파일은 현재 worktree에서 미추적이므로 PR 작성자가 라이선스와 포함 대상을 검토해야 한다.
- 실제 A GTFS와 `--rail-gis-live` API 확인에서 GIS 8개(1·2·3·4·5·6·8·9호선)가 실제 `MultiLineString`으로 반환됐다. 7호선은 12개 역 중 11개만 250m 이내여서 실선 채택을 거부했다. 네 지원 구의 `extent=inside` 경계·선형 응답도 확인했다. 실제 브라우저 시각 검증은 수행하지 않았다.
- 시뮬레이션 결과 자료는 현재 GitHub·확인한 로컬 폴더에서 찾지 못했다. 실행 결과가 없을 때는 PR #41 보고서 fallback만 가능하며, 테스트 fixture는 실제 구별 결과로 사용하지 않는다.
- 이번 보충 검증: D 시뮬레이션·네트워크·GIS·HTTP 테스트 22개 통과. 전체 unittest는 360개 중 실패 26개·건너뜀 6개였고, 기존 평가/M07 fixture의 체크섬·검증 기대값 실패가 남아 있다. `PYTHONUTF8=1` 및 `PYTHONPATH=src`로 실행했다. 전체 테스트를 통과했다고 표시하지 않는다.

현재 브랜치 `feat/45-d-simulation-viz`는 저장소의 `<type>/<이슈번호>-<설명>` 형식에 맞는다. PR을 직접 올리기 전 `git status --short`로 미추적 파일까지 확인하고 **D 코드·문서·필요한 공개 경계 및 라이선스 파일만** 선택해 스테이징한다. 제목은 `feat(d): 운영자 지도와 시뮬레이션 결과 연동 (#이슈번호)`, 본문은 `## 무엇을` / `## 확인` / `## 연결` 형식을 따른다. 원본 실행 결과를 PR에 포함하지 않았다는 점과 브라우저 시각 검증 여부를 정확히 쓴다.

저장소의 `AGENTS.md` 형식에 맞춰 실제 제출 시 아래 세 칸을 사용한다. 실행 결과 파일을 PR에 포함했다는 표현은 쓰지 않는다.

```markdown
## 무엇을
- D 운영자 MapLibre 화면에 지역 교통망, C/B 결과 조회, 시나리오별 잔여 수요 및 실행별 미충족 수요 원을 연결
- 보고서 평균·입력 번들·M05 실행 파일의 출처를 화면에서 구분

## 확인
- D API 테스트 37개와 실제 입력 32조건의 잔여 수요 합계 대조
- 로컬 재계산 구로 한 날짜 네 실행의 M05·M07 상태 확인(실행 파일은 PR 밖)
- 브라우저 자동 렌더링은 미확인; 이 문서의 수동 확인 절차 제공

## 연결
- 선행 PR #41 보고서와 저장소 밖 A GTFS·seoul_transit_data·C/B/M07 결과를 경로로 조회
- 4개 구 전체의 날짜별 공간 결과는 실행 원본 인수 후 활성화
```
