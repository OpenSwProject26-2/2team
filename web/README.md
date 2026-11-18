# D 운영자 지도 개발 화면

## C/B 시뮬레이션 결과 모드

`http://127.0.0.1:8766/`의 기존 D 운영자 화면 상단 **시뮬레이션 결과**를 누르면 같은 MapLibre 지도 안에서 C/B 결과 모드로 전환한다. `/simulation`도 같은 화면을 이 모드로 연다. A GTFS 배포본을 `--a-gtfs`로 연결했다면 A의 정류장·교통망 레이어를 함께 표시하고, 나갈 때 이전 DEMO/A 모드로 돌아간다. A는 정상 교통망 참고 레이어이며 파업 대상·B 수송 성과와 자동으로 연결하지 않는다. 구·차량·시나리오·운행안·실험/테스트·날짜·시간을 고르면 KPI, ours/sp 비교표, 4개 구·차량·기간 그래프와 검증 상태가 갱신된다. `--transit-data`를 연결하면 C/B 입력 번들의 **셔틀 투입 전 잔여 수요**를 C 지역망 출발 정류장에 빨간 원으로 표시한다. 날짜별 `passenger_outcomes.csv`가 연결된 실행에서는 선택 차량·운행안의 **셔틀 투입 후 미충족 수요**로 원을 바꾼다. 보고서의 구 전체 수치를 정류장에 임의 배분하지 않는다.

`experiments/reports/routegen_v4.md` §3에 실린 4개 구 × 2개 차량 조건 × 4개 변형 × 2개 안 × 실험/테스트의 5일 평균을 조회한다. 충족률은 보고서의 `M1 served / M1 total`이다. §6의 미충족 사유는 `affected` 실험 평균에만 적용한다. 실제 노선 정차점·노선별 차량·일별 M05 지표와 M07 검증은 원본 결과가 있을 때만 나온다. 입력 수요의 시간칸은 **06:00~10:00**(06·07·08·09시 시작, 각 1시간)뿐이다. 보고서의 노선 수를 지도 형상이나 노선별 차량 대수로 추정하지 않는다.

원본을 저장소 밖에 보관하고 경로만 지정한다. `--simulation-results` 아래에는 구별 `manifest.json`과 실행 결과가 있는 `guro/`, `dongjak/`, `gangseo/`, `gangdong/` 폴더를 둔다. `--simulation-inputs` 아래의 `<구>_0610_v4/network/stops.csv`는 C팀 **지역 교통망 정류장** 좌표다. A GTFS ID와 자동으로 합치지 않는다. 결과가 없는 구는 보고서 평균과 입력 잔여 수요까지만 보여 준다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --transit-data "C:\path\to\seoul_transit_data.Zip" --simulation-results "C:\path\to\routegen_v4" --simulation-inputs "C:\path\to\region_bundles_v4"
```

구별 요청 파일: `manifest.json`, `candidates/affected*.json`(4개 변형 각각), `plans/*.json`, `runs/<variant>/runs.json`, `runs_test/<variant>/runs.json`, 각 실행의 `run/metrics.json`, `run/passenger_outcomes.csv`, `verifier_report.json`, `plan_m05.json`. 지역 입력의 `network/stops.csv`와 `network/travel_times.json`도 함께 받는다. 후자의 `network_snapshot_id`가 manifest와 일치할 때만 실행 결과의 정류장 좌표를 쓴다. D는 `passenger_outcomes.csv`를 로컬에서 출발 정류장별로 집계하고 승객 ID를 API로 내보내지 않는다. 공개 배포에는 별도의 비식별 집계본과 접근 정책이 필요하다. 선형을 그리려면 정류장 순서와 일치하는 검증된 방향별 도로 geometry가 필요하다. 현재는 실제 운행 경로 선 대신 정류장 점만 표시한다.

API: `GET /api/d/simulation/options`, `/result`, `/residual`, `/runs`, `/comparison`, `/table`, `/seoul`, `/selection`, `/candidates`, `/routes`, `/allocations`, `/map`, `/metrics`, `/verification`; `POST /api/d/simulation/selection`. 조회에는 `region`, `fleet`, `variant`, `plan`, `period=exp|test`, `date=mean|YYYY-MM-DD`를 쓴다. `/residual`은 `--transit-data`의 지역 입력 번들을 정류장별로 집계하고 5명 미만 정류장·시간 칸을 숨긴다. 원본 파일을 읽기만 하며 필터 변경 시 재실행하지 않는다. 선택은 개별 날짜의 M05 성공과 M07 통과 원본, 일치하는 `plan_id`, `revision`, `network_snapshot_id`가 있어야 한다. V12/V15/V16 등 미검증 항목은 그대로 기록한다. 선택 이력은 저장소 내 Git 제외 `data/d_selection.json`에 보관하며, `selected`는 실제 운행 승인과 다르다.

수동 화면 검사: 결과 파일이 없으면 구로 11대 `affected` 우리 안 실험 평균 12.58%와 강동 70대 `affected` 피더안 테스트 평균 23.52%가 **보고서 출처**로 표시된다. `--transit-data`가 있으면 시나리오·날짜·06~09시 시작 시간칸에 따라 출발 정류장별 잔여 수요 원이 바뀐다. 개별 M05/M07 파일이 있으면 같은 날짜에서 차량·운행안을 바꿔 정류장별 미충족 원, 노선·배분, M07 상태를 확인한다. 입력이 없을 때는 해당 기능이 미연결로 표시된다. 지도 표시 옵션의 `잔여·미충족 수요 원`으로 원을 끄고 켜며, 지하철 표시 체크는 수요를 재계산하지 않는다. 기존 DEMO/A 화면으로 되돌아가 기능을 확인한다. 지도 타일은 인터넷 연결이 필요하다.

PR에 포함할 코드·외부 인수물·로컬 재계산 결과의 범위와 수동 검증 순서는 [D 대시보드 PR 인수인계](../docs/design/d-dashboard-pr-handoff.md)에 정리했다.

실행 화면은 `web/operator/index.html`이다. 상단 카드·좌측 노선 목록·중앙 지도·우측 상세/레이어·하단 그래프 배치를 유지하고 가상 SVG 지도를 **MapLibre GL JS + OpenFreeMap의 실제 서울 지도 배경**으로 교체했다. 데이터 조회는 로컬 Python API를 사용한다. 원본 UI 참고 파일은 이 worktree에 포함하지 않았다.

## 실행

저장소 루트에서:

```powershell
py -3 web/operator/serve.py
```

브라우저에서 `http://127.0.0.1:8766/` 또는 `http://127.0.0.1:8766/simulation`을 연다. MapLibre 6.18.0 JS·CSS와 Liberty 스타일 정의는 `web/operator/vendor/`에서 제공한다. 실제 배경 타일·글꼴·스프라이트는 OpenFreeMap 연결이 필요하지만, 연결이 불안정해도 D의 지도 데이터 레이어는 초기화한다. **합성 버스 선형은 미리 저장한 `road_shapes.json`을 읽으므로 화면 조회 시 OSRM API를 호출하지 않는다.** `file://`로 HTML을 직접 열면 API가 없어서 실행되지 않는다.

A의 로컬 배포 ZIP을 지도 자료로 추가하려면, 원본 ZIP을 저장소에 복사하지 않고 다음처럼 경로만 전달한다:

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --service-date 2026-11-02
```

상단의 `합성 DEMO`/`A GTFS` 버튼으로 출처를 바꾼다. A GTFS 모드에는 실제 정류장·역 좌표와 노선 목록을 보여 준다. 해당 배포본에 `shapes.txt`는 없지만 `stop_times.txt`의 정차 순서는 있다. 지하철 9개 노선은 전체 활성 운행편의 인접 역을 점선으로 연결해 분기까지 개략적으로 보여 주고, 버스는 선택한 노선의 대표 운행편 정류장을 점선으로 연결한다. **이 점선은 정차 관계 개략도이며 실제 도로·철로 선형이나 거리 자료가 아니다.** 목록의 **시내버스·마을버스·지하철·미분류** 탭은 A의 `routes.txt` `route_desc`와 `route_type`을 기준으로 분류한다. 오른쪽 **대표 운행편 정차점**에는 `stop_times.txt` 순서대로 이름·ID가 보인다. **전체 활성 운행편 정차점**에는 상·하행/변형 운행편의 정차점을 중복 제거해 보여 준다. 전체 목록은 ID 정렬이며 운행 순서가 아니다. 항목을 누르면 지도 위치로 이동한다. 선택 노선의 전체 정차점을 노란 점으로 강조한다. A 자료를 갱신하고 `초기화` 버튼을 누르면 서버가 전체 ZIP을 검증한 뒤 새 스냅샷으로 교체한다. 검증 실패 시 이전 정상본과 오류 상태를 유지한다. 로컬 전용 서버이며 공용 M08 API나 배포 서버가 아니다.

2026-11-11 A 배포본을 서비스일 2026-11-02로 읽으면 활성 노선 707개(서울 시내 계열 374, 서울 마을 242, 도시철도 9, 분류 미확인 버스 82), 좌표가 있는 정류장·역 13,151개(버스 12,693, 철도 458)다. 707개 노선 모두 대표 순서와 전체 정차점 집합이 있고 해당 ID는 `stops.txt`에서 확인된다. 예컨대 `종로13`은 대표 16곳·전체 33곳, `1호선`은 대표 21곳·전체 102곳이다. **경기버스로 분류된 노선은 이 배포본에 0개**다. `route_desc`는 A 자료의 분류이며 D가 노선번호만 보고 추측한 값이 아니다.

합성 DEMO는 **가상 거점 10곳**만 넣은 작은 예제라 노선별 정차점이 3~5곳(가상 철도 4곳)뿐이다. A 정보를 3곳으로 줄이거나 실제 역을 임의로 대체한 것이 아니다. A GTFS 모드에서 노선을 고르면 해당 노선의 전체 정차점이 지도에 노란 점으로 강조된다. 축소 지도에서는 아이콘 충돌 방지로 일반 정류장 아이콘 일부가 숨으므로 확대하거나 오른쪽 정차점 목록을 사용한다. 이 A 배포본에는 `shapes.txt`가 없어서 정차점 사이의 실제 주행 선형은 알 수 없다.

## 조작과 의미

- 지도 확대·축소·드래그, `전체 보기`, `선택 노선 보기`, 노선 클릭/검색, 정류장·역 아이콘·팝업·호버 이름, 레이어 표시 전환을 제공한다. 합성 DEMO에서는 가상 정류장 순서를 OSRM/OSM **자동차 도로망**에 맞춘 버스 6개 노선 추정 선형을 실선으로 그린다. 합성 지하철 1개와 새로 만든 셔틀의 미확인 선형은 점선이다. A GTFS에서는 지하철 전체 정차 관계와 선택 버스의 대표 정차 관계를 점선으로, 선택 노선의 **전체 활성 운행편 정차점**을 노란 점으로 표시한다.
- A GTFS 정류장 점은 현재 지도 화면 범위 안의 좌표만 렌더링하고 이동·확대 후 갱신한다. 화면을 넓혀 지도를 보면 정류장과 역을 구별하기 쉽다.
- 시간대를 바꾸면 합성 수요 버블·합계·목록·그래프가 같은 시간대 자료를 사용한다. 계산 입력은 80개 가상 OD 셀이고 지도에서는 이를 **10개 거점별 원으로 합산**한다. 원 반경은 해당 거점 인원이 많을수록 커진다. `전체`에서는 연한 바깥 원이 전체 수요, 진한 안쪽 원이 미충족 수요다. `출발`·`도착`은 각각 그 수요만, `미충족`은 진한 원만 표시한다. 출발·도착과 충족·미충족은 B 평가나 실제 OD가 아니다.
- 오른쪽에서 셔틀을 제외·다시 추가하거나 정차점·차량 수를 넣어 새 가상 셔틀을 만들면 같은 시간대의 가상 배분을 다시 계산한다. 차량 8대 상한을 검사한다. 새 셔틀은 서버에 저장되지 않고 브라우저 세션에서만 유지된다.
- **합성 DEMO의 운행 조건**에서 시내·경기·마을·지하철을 끄면 해당 가상 정상 노선이 운행하지 않는 것으로 가정해 미충족 원과 충족률을 다시 계산한다. 예제 fixture에 노선별 정차 순서·가정 시간당 정원(시내 160명, 경기 100명, 마을 80명, 지하철 200명)을 명시했다. 이는 관측 정원·실제 운행편 수가 아니다. **지도 표시 옵션**의 모든 체크는 계산에 영향을 주지 않는다. A GTFS 모드의 체크 역시 표시만 바꾼다.
- DEMO 배분 순서는 정상 노선 `route_id` 정렬 후 셔틀 `route_id` 정렬이며, 이미 충족된 합성 OD 셀을 중복 계산하지 않는다. 순서별 노선 기여량은 가상 배분 결과일 뿐 최적 배차나 실제 승객 선택을 뜻하지 않는다.
- 합성 DEMO에서는 화면에 표시 중인 정상 노선과 **좌표가 정확히 같은 선분**에서 셔틀 선만 투명하게 숨긴다. 정상 노선을 체크 해제하면 해당 선분이 다시 나타난다. 다른 정상 노선도 같은 선분에 겹치면 그 노선까지 꺼야 나타난다. 이는 **지도 표시 효과**이며 실제 노선 중복 분석이 아니다. 현재 합성 철도는 검증된 궤도 선형이 없어 도로와 겹친다고 판정하지 않는다. A GTFS 모드에는 `shapes.txt`가 없어 이 효과를 적용하지 않는다.
- OSRM 결과는 자동차 도로를 따르는 **추정 표시 선형**이다. 가상 정류장 위치에 따른 큰 우회와 최대 약 80m의 스냅 오차가 있으며, 버스 통행 규제·회차·정차 가능성·실제 운행거리·탑승 가능성을 검증하지 않았다.

## 나중에 선형 자료 교체

합성 기본 노선은 `tests/fixtures/d_dashboard/demo.json`의 `routes[]`에 `route_id`, 순서대로 나열한 `[경도, 위도]` 정차점 좌표, 셔틀의 `stop_ids`를 둔다. 도로 선형은 별도 `tests/fixtures/d_dashboard/road_shapes.json`의 동일 `route_id` 항목에 `profile: "driving"`, **원본과 정확히 같은** `source_waypoints`, 도로를 따른 `coordinates`(GeoJSON `[경도, 위도]` 배열), 각 정차점의 `snap_distances_m`, 양수 `distance_m`를 둔다. 항목을 추가하면 최상위 `route_count`도 맞춘다. 시작 시 ID·좌표·스냅 거리 120m 상한과 스키마 버전을 검증하며 불일치하면 `DATA_VERSION_MISMATCH`를 낸다. 두 파일 내용은 `snapshot_id`에 반영된다.

가상 신규 셔틀을 영구 예제로 만들려면 위 두 파일에 노선을 넣고, `demo.json`에는 `kind: "shuttle"`, `stop_ids`, `assigned_vehicles`도 넣는다. 도로 선형이 없으면 `geometry_status: "synthetic"`와 정차점 연결선을 사용하되 화면에서 **점선·미확인**으로 표시한다. 실제 자료로 전환할 때는 A GTFS `shapes.txt`나 B가 제공할 검증된 방향별 선형·정류장 ID 계약을 우선 사용해야 한다. DEMO 도로 선형을 실제 피드에 섞지 않는다.

선형을 다시 **일괄 생성**하려면 `py -3 web/operator/build_demo_roads.py`를 수동 실행한다. 공개 OSRM 데모 서버의 비상업·초당 1회 제한에 맞춰 1.1초 간격으로 요청하고 기존 `road_shapes.json` 전체를 교체하므로, 수동으로 보정한 좌표가 있다면 먼저 보존해야 한다. 실제 배포용 외부 경로 서비스 채택은 별도 합의가 필요하다.

## 개발 API

`GET /api/d/dashboard`, `/api/d/routes`, `/api/d/map`, `/api/d/demand`, `/api/d/sources`는 `mode=demo|real`을 받는다. `/api/d/map`과 `/api/d/demand`는 GeoJSON `FeatureCollection`을 반환한다. `GET /api/d/routes?mode=real&kind=village`처럼 A 분류를 조회할 수 있으며 각 노선의 순서가 있는 `representative_stop_ids`와 순서 없는 `all_stop_ids`를 `/api/d/map`의 정류장 이름·좌표와 연결한다. `POST /api/d/demo/scenario/evaluate`는 합성 시나리오의 `hour`, 셔틀 `routes`, 체크된 `active_normal_route_ids`를 받아 정원 배분을 재계산한다. 이 값이 빠지면 정상 노선 운행을 가정하지 않는다. `POST /api/d/sources/refresh`는 로컬 A GTFS를 다시 읽는다. 필드와 HTTP 상태는 **D 로컬 제안**이며 M08 공용 계약으로 확정되지 않았다.

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

A GTFS ZIP을 연결하고 공식 GIS 선로를 서버 시작 시 조회하려면 `--rail-gis-live`를 더한다. 외부 GIS 접속이 제한된 PC에서는 별도로 받은 같은 레이어의 GeoJSON을 저장소 밖에 두고 `--rail-gis-file "C:\path\to\rail.geojson"`을 쓴다. 응답이 없거나 A 역 좌표와 맞지 않는 노선은 기존 점선 개략도를 유지한다.

```powershell
py -3 web/operator/serve.py --a-gtfs "C:\path\to\busstrike_data_release_20261111.zip" --rail-gis-live
```

운영자 화면 `http://127.0.0.1:8766/`에서 **A GTFS**를 선택하고 구로·강동 등 구를 고르면 구 내부 철도 선형과 정류장이 표시된다. 노선을 선택하면 실선의 GIS 출처와 역 좌표 검증 수를 확인한다. `/api/d/sources`의 `rail_gis.matched_routes`로 연결 수를 확인할 수 있다. GIS 선형은 물리 선로이며 운행편별 분기·방향 경로의 인증 자료는 아니다. 서울 버스 공식 GPS 선형은 별도의 API 인증키 및 A GTFS와의 노선 ID·정류장 순서 검증이 아직 필요하다.

검증된 버스 선형 GeoJSON을 별도로 받았다면 `--bus-geometry-file "C:\path\to\bus-verified.geojson"`을 추가한다. 필수 매핑 필드와 채택 조건은 [교통 데이터 통합 문서](../docs/design/d-transit-data-integration.md)에 있다. 파일이 없으면 버스 정차점 연결은 점선으로 남는다.

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
