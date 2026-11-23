# D 운영자 지도 개발 화면

## 통합 운영자 노선 실험실

합성 DEMO·A GTFS·저장된 C/B 시뮬레이션 결과는 **같은 MapLibre 지도 중심 화면과 접이식 좌측 패널**을 공유한다. 상단 합성 DEMO/A GTFS 선택은 지도 데이터 모드를 바꾸고, **시뮬레이션 결과 표시**는 현재 교통망을 유지한 채 공식 실행 결과를 덧붙인다. `/simulation`도 같은 화면을 연다. A GTFS는 서버 시작 때 `--a-gtfs` 경로를 지정해야 하며 자동 탐색하지 않는다. GIS 철도 선형은 추가로 `--rail-gis-live` 또는 `--rail-gis-file`이 필요하다. A는 정상 교통망 참고 레이어이며 파업 대상·B 수송 성과와 자동으로 연결하지 않는다. 구·차량·시나리오·운행안·실험/테스트·날짜·시간을 고르면 KPI, ours/sp 비교표, 4개 구·차량·기간 그래프와 검증 상태가 갱신된다. `--transit-data`를 연결하면 C/B 입력 번들의 **셔틀 투입 전 잔여 수요**를 C 지역망 출발 정류장에 빨간 원으로 표시한다. 날짜별 `passenger_outcomes.csv`가 연결된 실행에서는 선택 차량·운행안의 **셔틀 투입 후 미충족 수요**로 원을 바꾼다. 보고서의 구 전체 수치를 정류장에 임의 배분하지 않는다.

서버에 A GTFS가 연결되어 있으면 첫 화면은 A GTFS와 서울 25개 구 전체 지도 범위를 선택한다. `/simulation`도 서울 전체 지도에서 시작하고, 실행된 구의 집계만 지표로 표시한다. 자치구를 선택하면 해당 구의 상세 시뮬레이션을 조회한다. 서울 25개 구 전체의 수요·충족률로 집계를 해석하지 않는다.

`experiments/reports/routegen_v4.md` §3에 실린 4개 구 × 2개 차량 조건 × 4개 변형 × 2개 안 × 실험/테스트의 5일 평균을 조회한다. 충족률은 보고서의 `M1 served / M1 total`이다. §6의 미충족 사유는 `affected` 실험 평균에만 적용한다. 실제 노선 정차점·노선별 차량·일별 M05 지표와 M07 검증은 원본 결과가 있을 때만 나온다. 입력 수요의 시간칸은 **06:00~10:00**(06·07·08·09시 시작, 각 1시간)뿐이다. 보고서의 노선 수를 지도 형상이나 노선별 차량 대수로 추정하지 않는다.

원본을 저장소 밖에 보관하고 경로만 지정한다. `--simulation-results` 아래에는 구별 `manifest.json`과 실행 결과가 있는 `guro/`, `dongjak/`, `gangseo/`, `gangdong/` 폴더를 둔다. `--simulation-inputs` 아래의 `<구>_0610_v4/network/stops.csv`는 C팀 **지역 교통망 정류장** 좌표다. A GTFS ID와 자동으로 합치지 않는다. 결과가 없는 구는 보고서 평균과 입력 잔여 수요까지만 보여 준다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --transit-data "C:\path\to\seoul_transit_data.Zip" --simulation-results "C:\path\to\routegen_v4" --simulation-inputs "C:\path\to\region_bundles_v4"
```

구별 요청 파일: `manifest.json`, `candidates/affected*.json`(4개 변형 각각), `plans/*.json`, `runs/<variant>/runs.json`, `runs_test/<variant>/runs.json`, 각 실행의 `run/metrics.json`, `run/passenger_outcomes.csv`, `verifier_report.json`, `plan_m05.json`. 지역 입력의 `network/stops.csv`와 `network/travel_times.json`도 함께 받는다. 후자의 `network_snapshot_id`가 manifest와 일치할 때만 실행 결과의 정류장 좌표를 쓴다. D는 `passenger_outcomes.csv`를 로컬에서 출발 정류장별로 집계하고 승객 ID를 API로 내보내지 않는다. 공개 배포에는 별도의 비식별 집계본과 접근 정책이 필요하다. 선형을 그리려면 정류장 순서와 일치하는 검증된 방향별 도로 geometry가 필요하다. 현재는 실제 운행 경로 선 대신 정류장 점만 표시한다.

API: `GET /api/d/simulation/options`, `/result`, `/residual`, `/runs`, `/comparison`, `/table`, `/seoul`, `/selection`, `/candidates`, `/routes`, `/allocations`, `/map`, `/metrics`, `/verification`; `POST /api/d/simulation/selection`. 조회에는 `region`, `fleet`, `variant`, `plan`, `period=exp|test`, `date=mean|YYYY-MM-DD`를 쓴다. `/residual`은 `--transit-data`의 지역 입력 번들을 정류장별로 집계하고 5명 미만 정류장·시간 칸을 숨긴다. 원본 파일을 읽기만 하며 필터 변경 시 재실행하지 않는다. 선택은 개별 날짜의 M05 성공과 M07 통과 원본, 일치하는 `plan_id`, `revision`, `network_snapshot_id`가 있어야 한다. V12/V15/V16 등 미검증 항목은 그대로 기록한다. 선택 이력은 저장소 내 Git 제외 `data/d_selection.json`에 보관하며, `selected`는 실제 운행 승인과 다르다.

수동 화면 검사: 결과 파일이 없으면 구로 11대 `affected_maeul_gg` 우리 안 실험 평균 12.00%와 강동 70대 같은 조건 피더안 테스트 평균 14.09%가 **보고서 출처**로 표시된다. `--transit-data`가 있으면 시나리오·날짜·06~09시 시작 시간칸에 따라 출발 정류장별 잔여 수요 원이 바뀐다. 마을·경기버스 표시 체크는 저장된 수요 변형을 선택하며, 같은 날짜·시간의 정류장별 입력이 있으면 기본 조건과 인원 차이를 원·팝업에 표시한다. 개별 M05/M07 파일이 있으면 같은 날짜에서 차량·운행안을 바꿔 정류장별 미충족 원, 노선·배분, M07 상태를 확인한다. **보고서 평균만 있는 차량 조건에서는 합계 지표만 바뀌고 지도 원은 셔틀 투입 전 잔여 수요를 유지한다.** 정류장별 결과가 없다는 안내를 지도 상단에 표시하며, 보고서의 총 충족 인원을 정류장에 임의 배분하지 않는다. 지도 표시 옵션의 `잔여·미충족 수요 원`은 원을 끄고 켠다. 저장된 C/B 결과에는 시내버스·지하철 제외 실행이 없으므로 두 수단의 체크는 표시만 바꾸고 기본 수요 원을 참고값으로 유지하며 미계산 이유를 안내한다. 합성 DEMO에서는 시내·마을·경기·지하철 체크와 셔틀 차량 수가 가상 정원 배분을 다시 계산한다. 시뮬레이션 결과 표시를 끄면 현재 DEMO/A GTFS 교통망과 같은 노선 실험실 화면에 남는다. 지도 타일은 인터넷 연결이 필요하다.

## 통합 노선 실험실 화면

운영자 대시보드는 별도의 실험실 화면으로 전환하지 않고, 합성 DEMO·A GTFS·시뮬레이션 결과가 같은 MapLibre 지도 중심 레이아웃을 공유한다. 왼쪽 접이식 패널에서 자치구, 교통수단별 노선 목록, 레이어, 운행 조건을 조작한다. 상단의 합성 DEMO/A GTFS 선택은 기존 데이터 모드를 전환하며, **시뮬레이션 결과 표시**는 현재 모드를 바꾸지 않고 저장 결과 패널을 지도에 덧붙인다.

시뮬레이션 결과는 공식 `/api/d/simulation/*` 계약의 저장 결과를 조회한다. 개인 실험실의 `/api/lab/*` 후보 생성·M05/M07 재실행 API는 호출하지 않는다. 직접 생성은 미연결 상태로 안내하고, 개인 실험실의 정원·혼잡 가정값을 공식 수치에 섞지 않는다. 계획에 검증된 도로 선형이 없으면 정류장 순서의 실선 개략선을 보여 주며 실제 도로 경로라고 표시하지 않는다. A GTFS를 연결하지 않아도 선택 구 경계는 `/data/districts_all.geojson` 정적 자료에서 표시한다.

PR에 포함할 코드·외부 인수물·로컬 재계산 결과의 범위와 수동 검증 순서는 [D 대시보드 PR 인수인계](../docs/design/d-dashboard-pr-handoff.md)에 정리했다.

실행 화면은 `web/operator/index.html`이다. 합성 DEMO·A GTFS·시뮬레이션 결과가 같은 화면을 사용한다. 접이식 좌측 패널에 구·노선 목록·상세·운행 조건·레이어를 두고, 중앙은 **MapLibre GL JS + OpenFreeMap의 서울 지도**로 넓게 표시한다. 시뮬레이션 비교·검증 정보는 지도 아래에 보인다. 데이터 조회는 로컬 Python API를 사용한다. 원본 UI 참고 파일은 이 worktree에 포함하지 않았다.

자치구 선택은 상단 헤더에 두며 시뮬레이션 조건과 선택을 공유한다. 시뮬레이션에 후보 셔틀 정차점이 연결되면 버스 표식과 정차 순번을 함께 표시한다. 도로 geometry가 없는 후보 노선은 선을 실선으로 표시하더라도 정류장 순서 연결 개략선이며 실제 도로 경로가 아니다. 현재 C/B 입력 API에는 셔틀안 기준 도보 400m 초과 판정에 필요한 보행거리 속성이 없고, 2026년 1월 지자체 셔틀의 노선·정차점 공간자료도 연결되지 않았다. 해당 레이어 선택 항목은 미연결 상태로 비활성화한다.

## 실행

저장소 루트에서:

```powershell
py -3 web/operator/serve.py
```

브라우저에서 `http://127.0.0.1:8766/` 또는 `http://127.0.0.1:8766/simulation`을 연다. MapLibre 6.18.0 JS·CSS와 Liberty 스타일 정의는 `web/operator/vendor/`에서 제공한다. 실제 배경 타일·글꼴·스프라이트는 OpenFreeMap 연결이 필요하지만, 연결이 불안정해도 D의 지도 데이터 레이어는 초기화한다. **합성 버스 선형은 미리 저장한 `road_shapes.json`을 읽으므로 화면 조회 시 OSRM API를 호출하지 않는다.** `file://`로 HTML을 직접 열면 API가 없어서 실행되지 않는다.

A의 로컬 배포 ZIP을 지도 자료로 추가하려면, 원본 ZIP을 저장소에 복사하지 않고 다음처럼 경로만 전달한다:

서버는 A GTFS를 자동 탐색하지 않는다. `--a-gtfs` 경로가 없거나 피드 검증에 실패하면 상단 A GTFS 버튼은 비활성화되고, 합성 DEMO는 계속 사용할 수 있다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --service-date 2026-11-02
```

상단의 `합성 DEMO`/`A GTFS` 버튼으로 출처를 바꾼다. A GTFS 모드에는 실제 정류장·역 좌표와 노선 목록을 보여 준다. 해당 배포본에 `shapes.txt`는 없지만 `stop_times.txt`의 정차 순서는 있다. 지하철 9개 노선은 전체 활성 운행편의 인접 역을 실선 개략선으로 연결해 분기까지 보여 주고, 버스는 선택한 노선의 대표 운행편 정류장을 연결한다. **실선이어도 정차 관계 개략도는 실제 도로·철로 선형이나 거리 자료가 아니다.** 목록의 **시내버스·마을버스·지하철·미분류** 탭은 A의 `routes.txt` `route_desc`와 `route_type`을 기준으로 분류한다. 오른쪽 **대표 운행편 정차점**에는 `stop_times.txt` 순서대로 이름·ID가 보인다. **전체 활성 운행편 정차점**에는 상·하행/변형 운행편의 정차점을 중복 제거해 보여 준다. 전체 목록은 ID 정렬이며 운행 순서가 아니다. 항목을 누르면 지도 위치로 이동한다. 선택 노선의 전체 정차점을 노란 점으로 강조한다. A 자료를 갱신하고 `초기화` 버튼을 누르면 서버가 전체 ZIP을 검증한 뒤 새 스냅샷으로 교체한다. 검증 실패 시 이전 정상본과 오류 상태를 유지한다. 로컬 전용 서버이며 공용 M08 API나 배포 서버가 아니다.

2026-11-11 A 배포본을 서비스일 2026-11-02로 읽으면 활성 노선 707개(서울 시내 계열 374, 서울 마을 242, 도시철도 9, 분류 미확인 버스 82), 좌표가 있는 정류장·역 13,151개(버스 12,693, 철도 458)다. 707개 노선 모두 대표 순서와 전체 정차점 집합이 있고 해당 ID는 `stops.txt`에서 확인된다. 예컨대 `종로13`은 대표 16곳·전체 33곳, `1호선`은 대표 21곳·전체 102곳이다. **경기버스로 분류된 노선은 이 배포본에 0개**다. `route_desc`는 A 자료의 분류이며 D가 노선번호만 보고 추측한 값이 아니다.

합성 DEMO는 **가상 거점 10곳**만 넣은 작은 예제라 노선별 정차점이 3~5곳(가상 철도 4곳)뿐이다. A 정보를 3곳으로 줄이거나 실제 역을 임의로 대체한 것이 아니다. A GTFS 모드에서 노선을 고르면 해당 노선의 전체 정차점이 지도에 노란 점으로 강조된다. 축소 지도에서는 아이콘 충돌 방지로 일반 정류장 아이콘 일부가 숨으므로 확대하거나 오른쪽 정차점 목록을 사용한다. 이 A 배포본에는 `shapes.txt`가 없어서 정차점 사이의 실제 주행 선형은 알 수 없다.

## 조작과 의미

- 지도 확대·축소·드래그, `전체 보기`, `선택 노선 보기`, 노선 클릭/검색, 정류장·역 아이콘·팝업·호버 이름, 레이어 표시 전환을 제공한다. 합성 DEMO에서는 가상 정류장 순서를 OSRM/OSM **자동차 도로망**에 맞춘 버스 6개 노선 추정 선형을 실선으로 그린다. 합성 지하철 1개와 새로 만든 셔틀의 미확인 선형도 실선으로 표시하되 도로 경로가 아닌 개략선으로 고지한다. A GTFS에서는 지하철 전체 정차 관계와 선택 버스의 대표 정차 관계를 실선 개략도로, 선택 노선의 **전체 활성 운행편 정차점**을 노란 점으로 표시한다.
- 서울 전체에서는 25개 구 경계와 저장된 충족률이 있는 4개 구의 비율만 표시한다. 자치구 선택 후 A GTFS 정류장 점은 현재 지도 화면 범위 안의 좌표만 렌더링하고 이동·확대 후 갱신한다.
- 자치구를 고르면 지하철역을 보라색 열차 아이콘과 역 이름으로 표시한다(줌 9 이상). `지하철` 체크를 끄면 역 아이콘과 이름도 함께 숨긴다. 버스 정류장 표식은 연결된 노선의 정차점 ID를 기준으로 시내 파란 원·경기 주황 마름모·마을 초록 원·복수 유형 회색 `+` 아이콘으로 구분한다. 유형을 확인할 수 없는 정류장은 회색으로 남긴다. 선택 노선의 정차점 이름·방문 순서는 따로 강조한다.
- 빨간 잔여·미충족 수요 원은 정류장·역 아이콘과 역 이름 아래에 그린다. 같은 위치를 클릭하면 정류장·역 정보를 우선 표시한다.
- 시간대를 바꾸면 합성 수요 버블·합계·목록·그래프가 같은 시간대 자료를 사용한다. 계산 입력은 80개 가상 OD 셀이고 지도에서는 이를 **10개 거점별 원으로 합산**한다. 원 반경은 해당 거점 인원이 많을수록 커진다. `전체`에서는 연한 바깥 원이 전체 수요, 진한 안쪽 원이 미충족 수요다. `출발`·`도착`은 각각 그 수요만, `미충족`은 진한 원만 표시한다. 출발·도착과 충족·미충족은 B 평가나 실제 OD가 아니다.
- 합성 DEMO의 운행 조건과 가상 셔틀 편집은 노선 실험실 좌측 설정 패널의 상세 영역에서 제공한다. 두 가상 파업 노선을 개별 해제·복구할 수 있다. 기존 셔틀의 차량 수와 쉼표로 구분한 정차 ID 순서를 편집하거나, 셔틀을 제외·추가하면 같은 시간대의 가상 배분을 다시 계산한다. 차량 8대 상한을 검사한다. 정차 순서를 편집한 노선의 선형은 정차점 연결 개략선으로 바뀌며 실제 도로 경로를 뜻하지 않는다. 변경은 서버에 저장되지 않고 브라우저 세션에서만 유지된다.
- **합성 DEMO**에서는 지도 표시 옵션의 시내·경기·마을·지하철·셔틀안 체크가 운행 가정과 함께 바뀐다. 파업 노선 체크를 해제하면 전체 OD는 유지하고 복구된 노선(700·701 각각 가정 시간당 200·140명)이 자신의 정차 순서에 맞는 수요를 먼저 흡수한다. 이어 정상 노선과 셔틀이 남은 수요를 가정 시간당 정원까지 흡수한다. 원의 크기·분포, 합계, 충족률, 노선별 가상 흡수 인원을 새 평가 응답으로 갱신한다. 예제 fixture의 시간당 정원(시내 160명, 경기 100명, 마을 80명, 지하철 200명)은 관측값이 아니다. 정류장·수요 원 체크는 표시만 바꾼다. **A GTFS 단독**에서는 교통수단 체크가 지도 표시만 바꾸며 실제 수요·충족률은 미계산이다.
- **저장된 C/B 결과 표시 중**에는 시나리오 선택 UI를 제공하지 않고 상단 지표를 기본 `affected_maeul_gg` 조건으로 고정한다. 마을·경기 체크는 저장된 네 접근 조건의 셔틀 투입 전 잔여 수요 원을 바꾼다. 정확히 일치하는 별도 수단 제외 결과가 있으면 이를 우선한다. 시내·지하철 제외 결과가 없으면 기본 원을 참고값으로 유지하고 효과 미반영을 표시한다. 파일 형식과 생성 조건은 [교통수단 제외 수요 계약](../docs/design/d-transport-demand-contract.md)을 따른다.
- DEMO 배분 순서는 복구된 700·701 노선, 정상 노선 `route_id` 정렬, 셔틀 `route_id` 정렬이며 이미 충족된 합성 OD 셀을 중복 계산하지 않는다. 파업 노선이 복구되면 해당 노선에 연결한 가상 OD는 전체 수요에 남고, 복구 노선의 정차 순서·가정 시간당 정원으로 먼저 흡수한다. 이는 합성 fixture의 소속 가정이며 실제 노선별 승객·수단 전환 자료가 아니다. 순서별 노선 기여량은 가상 배분 결과일 뿐 최적 배차나 실제 승객 선택을 뜻하지 않는다. 가상 날짜 선택은 `date_profiles`의 거점별 입력만 사용한다. 재현 명령과 공개 자료 조사 결과는 [D 시나리오 재현 문서](../docs/design/d-scenario-reproducibility.md)를 본다.
- 합성 DEMO에서는 화면에 표시 중인 정상 노선과 **좌표가 정확히 같은 선분**에서 셔틀 선만 투명하게 숨긴다. 정상 노선을 체크 해제하면 해당 선분이 다시 나타난다. 다른 정상 노선도 같은 선분에 겹치면 그 노선까지 꺼야 나타난다. 이는 **지도 표시 효과**이며 실제 노선 중복 분석이 아니다. 현재 합성 철도는 검증된 궤도 선형이 없어 도로와 겹친다고 판정하지 않는다. A GTFS 모드에는 `shapes.txt`가 없어 이 효과를 적용하지 않는다.
- OSRM 결과는 자동차 도로를 따르는 **추정 표시 선형**이다. 가상 정류장 위치에 따른 큰 우회와 최대 약 80m의 스냅 오차가 있으며, 버스 통행 규제·회차·정차 가능성·실제 운행거리·탑승 가능성을 검증하지 않았다.

## 나중에 선형 자료 교체

합성 기본 노선은 `tests/fixtures/d_dashboard/demo.json`의 `routes[]`에 `route_id`, 순서대로 나열한 `[경도, 위도]` 정차점 좌표, 셔틀의 `stop_ids`를 둔다. 도로 선형은 별도 `tests/fixtures/d_dashboard/road_shapes.json`의 동일 `route_id` 항목에 `profile: "driving"`, **원본과 정확히 같은** `source_waypoints`, 도로를 따른 `coordinates`(GeoJSON `[경도, 위도]` 배열), 각 정차점의 `snap_distances_m`, 양수 `distance_m`를 둔다. 항목을 추가하면 최상위 `route_count`도 맞춘다. 시작 시 ID·좌표·스냅 거리 120m 상한과 스키마 버전을 검증하며 불일치하면 `DATA_VERSION_MISMATCH`를 낸다. 두 파일 내용은 `snapshot_id`에 반영된다.

가상 신규 셔틀을 영구 예제로 만들려면 위 두 파일에 노선을 넣고, `demo.json`에는 `kind: "shuttle"`, `stop_ids`, `assigned_vehicles`도 넣는다. 도로 선형이 없으면 `geometry_status: "synthetic"`와 정차점 연결선을 사용하며, 화면에서는 **실선 개략도·미확인**으로 표시한다. 실제 자료로 전환할 때는 A GTFS `shapes.txt`나 B가 제공할 검증된 방향별 선형·정류장 ID 계약을 우선 사용해야 한다. DEMO 도로 선형을 실제 피드에 섞지 않는다.

선형을 다시 **일괄 생성**하려면 `py -3 web/operator/build_demo_roads.py`를 수동 실행한다. 공개 OSRM 데모 서버의 비상업·초당 1회 제한에 맞춰 1.1초 간격으로 요청하고 기존 `road_shapes.json` 전체를 교체하므로, 수동으로 보정한 좌표가 있다면 먼저 보존해야 한다. 실제 배포용 외부 경로 서비스 채택은 별도 합의가 필요하다.

## 개발 API

`GET /api/d/dashboard`, `/api/d/routes`, `/api/d/map`, `/api/d/demand`, `/api/d/sources`는 `mode=demo|real`을 받는다. `/api/d/map`과 `/api/d/demand`는 GeoJSON `FeatureCollection`을 반환한다. 합성 `/api/d/demand`는 지원하는 `date`·`hour`를 받는다. `GET /api/d/routes?mode=real&kind=village`처럼 A 분류를 조회할 수 있으며 각 노선의 순서가 있는 `representative_stop_ids`와 순서 없는 `all_stop_ids`를 `/api/d/map`의 정류장 이름·좌표와 연결한다. `POST /api/d/demo/scenario/evaluate`는 합성 시나리오의 `date`, `hour`, `strike_route_ids`, 셔틀 `routes`(정차 ID 순서·차량 수), 체크된 `active_normal_route_ids`를 받아 OD 셀별 정원 배분을 재계산한다. `date` 생략 시 fixture 기준일, `strike_route_ids` 생략 시 합성 파업 노선 전체를 가정하며, `active_normal_route_ids` 생략 시 정상 노선 운행을 가정하지 않는다. `POST /api/d/sources/refresh`는 로컬 A GTFS를 다시 읽는다. 필드와 HTTP 상태는 **D 로컬 제안**이며 M08 공용 계약으로 확정되지 않았다.

## 검사

```powershell
py -3 -B -m unittest discover -s tests/api -p test_*.py -v
py -3 -m ruff check src/busstrike/api web/operator/serve.py web/operator/build_demo_roads.py tests/api
py -3 -m ruff format --check src/busstrike/api web/operator/serve.py web/operator/build_demo_roads.py tests/api
```

한국어 Windows의 기본 `cp949`에서 전체 테스트의 UTF-8 fixture를 읽으려면 PowerShell에서 `$env:PYTHONUTF8 = '1'`을 설정한 뒤 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`를 실행한다. 단, 현재 저장소는 `core.autocrlf=true`로 checkout하면 평가 fixture의 LF가 CRLF로 바뀌어 SHA-256 검사가 실패한다. `PYTHONUTF8`은 이 줄바꿈 문제를 해결하지 않는다. Git 원본 LF를 보존한 임시 환경에서는 `main`과 D 포함 전체 테스트가 통과했으며 재현 근거와 공용 `.gitattributes` 개선안은 [D 인수인계](../docs/design/d-operator-dashboard-handoff.md)에 기록했다. 평가 fixture와 기대 체크섬은 변경하지 않았다.

오픈소스와 출처 표기: [실제 사용 근거](../docs/design/d-opensource-usage.md), [지도 기술 검토](../docs/design/d-opensource-map-stack.md). MapLibre GL JS 6.18.0과 Liberty 스타일 정의는 `web/operator/vendor/`에서 로컬로 제공한다. JS·스타일 정의 로딩은 CDN에 의존하지 않지만 실제 배경 타일·글꼴·스프라이트는 OpenFreeMap 공개 서버에 의존한다. 배경 타일 연결이 불안정해도 D의 노선·정류장·수요 레이어는 초기화한다. 라이선스 파일은 같은 `vendor/` 폴더에 보관한다. OSM/OpenMapTiles/OpenFreeMap/OSRM 출처는 지도 화면 안에 표시한다.

## 시민 경로 미리보기

경로 검색 프로토타입은 합성 GTFS를 이용해 **평상시 / 파업 / 파업+합성 셔틀**의 경로 차이를 보여 준다. **파업+운영자 선택 셔틀**은 `data/d_selection.json`의 정확한 `plan_id`와 `revision`을 확인하고, 해당 실제 시간표와 A/C 정류장 ID 매핑이 없으면 `DATA_INCOMPLETE`를 돌려준다. 경로 없음(`NO_PATH`)과 데이터 부족을 구분한다. 합성 경로 검색은 M05의 시간 내 수송 성공이나 좌석 가능성을 뜻하지 않는다. 저장소 루트에서 별도의 localhost 화면을 실행한다:

```powershell
py -3 web/citizen/serve.py
```

브라우저에서 `http://127.0.0.1:8765/`를 연다. 합성 입력은 `tests/fixtures/routing/gtfs_preview/`에 있다. 정상 버스 전용 도달 가능 조회는 다음처럼 실행할 수 있다:

```powershell
py -3 -m pip install -e .
py -3 -m busstrike.routing.normal_reachability synthetic-demand-1 A C 2026-11-07T00:00:00+09:00
```

`/__demo__/` 응답은 개발용 미리보기일 뿐 M08 공개 API 계약이 아니다. 경로 검색과 API 테스트:

```powershell
py -3 -B -m unittest discover -s tests/routing -p "test_*.py" -v
py -3 -B -m unittest discover -s tests/api -p "test_*.py" -v
```

## 실제 지하철 선로 표시

A GTFS ZIP을 연결하고 공식 GIS 선로를 서버 시작 시 조회하려면 `--rail-gis-live`를 더한다. 외부 GIS 접속이 제한된 PC에서는 별도로 받은 같은 레이어의 GeoJSON을 저장소 밖에 두고 `--rail-gis-file "C:\path\to\rail.geojson"`을 쓴다. 응답이 없거나 A 역 좌표와 맞지 않는 노선은 실선 개략도를 유지한다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --rail-gis-live
```

운영자 화면 `http://127.0.0.1:8766/`에서 **A GTFS**를 선택하고 구로·강동 등 구를 고르면 구 내부 철도 선형과 정류장이 표시된다. 노선을 선택하면 실선의 GIS 출처와 역 좌표 검증 수를 확인한다. `/api/d/sources`의 `rail_gis.matched_routes`로 연결 수를 확인할 수 있다. GIS 선형은 물리 선로이며 운행편별 분기·방향 경로의 인증 자료는 아니다. 서울 버스 공식 GPS 선형은 별도의 API 인증키 및 A GTFS와의 노선 ID·정류장 순서 검증이 아직 필요하다.

기본 서울 전체 지도에는 25개 자치구 경계와 결과가 있는 4개 구의 저장된 충족률만 표시한다. 나머지 21개 구의 비율은 계산하지 않는다. 자치구를 선택하면 A GTFS의 시내·마을버스 및 지하철을 표시한다. 선형 좌표가 없는 버스는 정차점 간 직선 개략선으로 표시한다. 현재 A GTFS에는 경기버스 노선과 실제 버스 회사명이 없으므로 경기버스 선은 나타나지 않고 회사 단위 자동 파업 지정도 불가능하다. 노선 상세 또는 노선번호 검색 결과에서 수동으로 파업을 지정하면 그 노선은 기존 분류에서 빠져 빨간 파업 노선으로 표시된다. 이 지정은 브라우저 세션의 가정이며 서버에 저장되지 않는다.

임의로 노선·정류장 접점당 승객 수를 가정해 보여 주던 가상 추가 수요 원은 제거했다. A GTFS의 노선·정류장 관계만으로 실제 추가 수요 인원을 계산할 수 없다. 시내·지하철을 제외한 실제 수요 변화를 표시하려면 노선별·시간별 수요, 수단 전환 입력 및 해당 조건의 M05 실행 결과가 필요하다. 집계된 결과 파일은 저장소 밖에 두고 `--transport-demand`로 연결한다.

자치구 경계는 `web/operator/data/districts_all.geojson`의 서울 25개 OSM 행정경계를 사용하고, 운영자 선택지는 현재 4개 구로 제한한다. 출처·ODbL 및 재생성 방법은 [교통 데이터 통합 문서](../docs/design/d-transit-data-integration.md)를 따른다. 기존 4개 구 전용 `districts.geojson`은 런타임에서 읽지 않는다.

검증된 버스 선형 GeoJSON을 별도로 받았다면 `--bus-geometry-file "C:\path\to\bus-verified.geojson"`을 추가한다. 필수 매핑 필드와 채택 조건은 [교통 데이터 통합 문서](../docs/design/d-transit-data-integration.md)에 있다. 파일이 없으면 버스 정차점 연결은 실선 개략도로 남으며 실제 도로 경로가 아니다.

### 버스 도로 경로를 로컬에서 생성

공식 서울 버스 API 키가 없는 경우 다음 전처리 명령으로 **선택한 노선만** OSRM 자동차 도로망에 맞춘다. 결과 파일은 저장소 밖에 둔다. 공개 데모 서버에 다수의 노선을 한꺼번에 요청하지 말고, 대량 처리에는 직접 운영하는 OSRM 서버를 `--osrm-url`로 지정한다. 화면의 구·운행안 선택은 이 경로를 다시 계산하지 않는다.

```powershell
py -3 web/operator/build_bus_roads.py `
  --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" `
  --route-id 116900013 `
  --out "C:\path\outside-repo\bus-roads.geojson"

py -3 web/operator/serve.py `
  --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" `
  --rail-gis-live `
  --bus-geometry-file "C:\path\outside-repo\bus-roads.geojson"
```

`116900013`은 A GTFS의 구로01이다. 이번 확인에서는 구로01을 포함해 4개 구 대표 노선 7개(마을 3, 시내 4)가 정류장 스냅·방문 순서·우회율 검사를 통과했다. 구로01의 최대 스냅은 14.3m, 도로 경로는 4.49km, 좌표 160개다. 결과는 `osrm_estimate` 출처와 옅은 연속 실선으로 표시한다. 버스 통행 허용이나 공식 운행 경로는 검증되지 않았다. `/api/d/sources`의 `bus_geometry`와 `/api/d/network?mode=real&district=guro`에서 연결 여부를 확인한다.

공식 경로는 [공공데이터포털 서울특별시 노선정보조회 서비스](https://www.data.go.kr/data/15000193/openapi.do)에서 활용 신청 후 발급한 키를 `SEOUL_BUS_API_KEY` 환경변수에 설정하고 같은 전처리기에 `--source official`을 준다. 키를 파일·코드·PR에 넣지 않는다. 예를 들어 현재 PowerShell 세션에서 `$env:SEOUL_BUS_API_KEY = (Read-Host '서울 버스 API 키')`로 입력한다. 공식 API 응답의 노선 ID·이름·정류장 순서와 A GTFS 정류장 좌표가 맞아야 채택되며, 현재 키가 없어 실제 공식 응답 검증은 완료되지 않았다.

시뮬레이션 결과 조회는 [결과 데이터 계약](../docs/design/d-simulation-contract.md)의 PR #47 `routegen-results/1` manifest를 지원한다. 결과 루트가 지정되지 않거나 manifest가 없으면 PR #41 보고서 평균 fallback만 제공하고 실제 후보 셔틀 노선·배정·날짜별 M05/M07은 표시하지 않는다.
