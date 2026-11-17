# D팀 운영자 대시보드·경로 서비스 개발 계획

> 현재 구현의 검토 단위·남은 데이터 게이트·직접 PR 제출 준비는 [실행 계획](d-next-steps-and-pr-plan.md)에 정리했다.

> 상태: **최신 `main` 기반 worktree로 D 운영자 화면·로컬 API·합성 경로 미리보기 이관. 팀 공용 ADR 및 A/B/C 연동 대기**. 본 문서는 D 전용 구현 제안이며 M00, M08 공개 API, 파업 정책을 확정하지 않는다. 실제 운행 승인 절차를 설계한 문서도 아니다.

> 2026-11-16 Draft PR 검증: D API 18개·routing 28개 통과. Git 원본 LF를 보존한 임시 환경에서 `main` 228개와 D 포함 274개 전체 테스트가 각각 통과했다(건너뜀 6개). Windows `core.autocrlf=true` checkout에서는 기존 평가 fixture의 SHA-256이 달라지므로 로컬 전체 테스트가 실패한다. 현재 브라우저 지도 상호작용은 제어 도구의 URL 확인 실패로 수락하지 못했다. 상세 근거는 [인수인계](d-operator-dashboard-handoff.md)에 둔다.

## 1. 목적과 범위

D는 M06 경로 검색, M08 API, M10 운영자·시민 화면을 맡는다. 첫 구현은 합성 입력으로 **서버 API와 실제 지도에서 동작**해야 한다. 다음으로 A의 비식별·집계 수요와 정상 GTFS, B의 셔틀 시간표·시뮬레이션, C의 후보안을 동일한 화면에 연결한다. 어느 단계에서도 가상 수치, 근사 시간표, 실제 관측값, 시뮬레이션 평가값을 같은 출처인 것처럼 표시하지 않는다.

운영자 화면은 노선·정류장·수요·차량·후보안·검증 결과를 조회하고, **검증된 시뮬레이션 안**을 비교·선택한다. 시민 화면은 고정된 `(plan_id, revision)`에 대한 출발지·목적지·출발 시각 경로를 검색한다. 미연결 자료는 `null`과 사유로 표시한다. 실제 배차 확정·탑승 보장·현장 안전성 보증은 범위에 없다.

현재 단계의 완성 범위는 합성 데이터에서 전체 화면·API·경로·결정론적 시나리오 결과가 동작하는 것과, 유효한 실제 산출물을 **프런트엔드 수정 없이** 교체할 수 있는 어댑터·검증·새로고침이다. A/B/C 자료가 아직 없거나 계약이 미정인 영역은 `unavailable`로 남긴다.

## 2. 확인한 저장소와 기존 D 산출물

| 위치·상태 | 이미 있는 것 | 재사용·주의 |
| --- | --- | --- |
| 현재 `main` 기반 `feat/d-dashboard-mvp` | `src/busstrike/{routing,api}`와 `web`은 README 중심, `planning`·`evaluation`에 코드, `pyproject.toml`은 Python 패키지 골격 | D 코드는 새 구조에 미커밋으로 이관. Git 이력을 옮겼거나 GitHub에 반영했다고 주장하지 않음 |
| 원본 로컬 `D.Part`, Draft PR #9/#19/#21 | GTFS 헤더 검사, 합성 정상·셔틀 GTFS 조회, 같은 정류장 최대 1회 환승, 개발용 HTTP·시민 화면, 정상 교통 조회 예제와 28개 테스트 | 핵심 경로 검색·정상 도달 가능 코드를 `src/busstrike/routing/`으로 옮기고 fixture·테스트를 `tests/`로 이관. `/__demo__/`는 정식 M08이 아님. 원본 폴더는 수정하지 않음 |
| 별도 로컬 실 GTFS 작업 | 실제 GTFS parser·버스 경로 후보 기능과 추가 테스트 | 이번 이관 범위에 포함하지 않음. 로컬 원본 자료·ZIP·별도 worktree를 복사하지 않았으며 A/B 입력 계약 전 실제 경로 통합으로 간주하지 않음 |
| A의 로컬 2026-11-11 배포 ZIP | 정상 버스·철도 통합 GTFS, 서비스일 2026-11-02~06, 버스 시간표는 관측 구간시간 기반 **근사치** | 실제 노선·정류장 자료와 실제 운행 실측을 구분. 철도 trip 3개의 시간 역전과 생성 보고서의 0건 주장은 A와 정정 필요 |
| 원격 C `planning` | 후보 `Plan`, `Assessment`, `checked_assessment`, `rerank`; `to_service_plan()`은 `draft`, `pending_b`, `trips=[]` | C 추천은 운행 시간표·독립 검증·선택 완료가 아님. `plan_id`·`revision`·`input_bundle_id`·체크섬을 맞댐 |
| 원격 B Draft PR #34/#36 | #34의 셔틀 GTFS 내보내기와 #36의 C→B 합성 브리지 | 미병합 코드에 필수 import 의존을 두지 않음. #34 `stops.txt`는 좌표가 있을 때만 생성될 수 있음 |
| 원격 M07 현재 | 독립 검증 보고서의 `pass`와 `fully_verified`가 별도이며 후자는 현재 `false` | `pass=true`만으로 완전 독립 검증 또는 선택 가능이라고 표시하지 않음 |

로컬 기존 D 코드 테스트 28개, 별도 worktree 추가 8개는 이전 작업에서 통과했다. 이는 새 구조의 통합 테스트 결과가 아니다. 이전 Draft PR의 옛 경로는 PR #27 이후 자동 병합 대상이 아니므로 해당 PR을 그대로 병합하는 방식은 피한다.

## 3. 새 구조로 옮기는 원칙과 데이터 흐름

```text
A GTFS·집계 OD ── ADataAdapter ──┐
B 셔틀 GTFS·평가 ─ BDataAdapter ──┼─ 검증·정규화 ─ 불변 Snapshot ─ M08 API ─ M10 웹
C 후보·Scenario ─ CDataAdapter ──┤                      │
합성 fixture ── DemoDataAdapter ──┘                      └─ M06 정상·파업·셔틀 경로 조회
```

파서는 `routing` 또는 D 전용 어댑터에, HTTP 처리·선택 포인터는 `api`에, 시각화는 `web`에 둔다. 코어 함수는 HTTP 객체를 받지 않는다. A/B/C 모듈 DB나 내부 mutable 상태에 직접 접근하지 않고 공개 DTO 또는 **버전이 붙은 산출물**을 소비한다. `src/busstrike/contracts`, `planning`, `sim`, `evaluation`의 의미를 D에서 임의로 변경하지 않는다. 이번 이관에서는 원본 `D.Part`를 수정하지 않고, 테스트한 합성 경로 기능만 `src/busstrike/routing`, `web/citizen`, `tests/routing`으로 복사했다.

## 4. 운영자 화면 구성

| 영역 | 표시·동작 | 데이터가 없을 때 |
| --- | --- | --- |
| 상단 요약 | 시나리오·서비스일·파업/정상 노선 수·가용/배정 차량·총 수요·미충족 수요/평가 상태·스냅샷 버전 | 숫자 `0` 대신 `미연결`/`미계산`, 출처·기준일 표시 |
| 왼쪽 탐색 | 실제 `route_id`로 선택되는 노선 목록, 별도 `route_short_name` 검색, 회사·수단·운행 상태 필터 | 해당 수단과 필터 결과의 부재를 구분 |
| 중앙 지도 | 노선·셔틀·정류장/역·수요 버블·중복 구간, 클릭 선택·줌/팬·툴팁 | 좌표/형상 부재 시 해당 레이어 비활성화와 이유 표시 |
| 오른쪽 상세 | 노선과 출처, 차량 정원·가용 시간·배정, 시간표/검증 상태, 레이어 설정 | 미확인 값은 `null`과 상태 표시 |
| 하단 | 시간대 수요 그래프, 같은 입력 묶음의 운행안 비교, 데이터 소스 상태·갱신 이력 | 비교 불가 이유와 필요한 입력 표시 |

데스크톱을 우선하되 좁은 창에서는 좌우 패널을 접고 키보드로 노선·시간대·레이어를 선택할 수 있게 한다. 색상은 파업 빨강, 정상 서울 시내버스 파랑, 경기버스 주황, 마을버스 초록, 철도 보라, 셔틀 청록을 **범례와 텍스트 상태**로 함께 전달한다. `route_type=3`만으로 서울/경기/마을/파업을 판정하지 않는다. 분류가 불명확한 노선은 `unknown`으로 표시한다.

## 5. 사용자 조작과 상태 관리

웹 상태는 `data_mode`, `snapshot_id`, `scenario_id`, `selected_plan_ref`, `selected_route_id`, `filters`, `visible_layers`, `demand_mode`, `time_bin_s`, `request_state`로 나눈다. `selected_plan_ref`는 항상 `(plan_id, revision)` 쌍이며 새 스냅샷에 그 쌍이 없으면 선택을 해제하고 알린다. API 응답마다 스냅샷 ID를 받아 오래된 응답이 최신 화면에 덮어쓰지 못하게 한다.

**레이어 토글**은 표시만 변경하고 평가 지표·수요 합계를 재계산하지 않는다. **운행 조건 변경**은 새 시나리오/입력 묶음 또는 이미 계산된 결과를 요청한다. 합성 모드에서는 고정된 합성 승객·운행시간·차량 입력으로 결정론적 계산을 실행하고, 같은 입력의 baseline/대안만 비교한다. 실제 모드에서 B/M07 결과가 없으면 충족률을 만들지 않고 `pending_evaluation`으로 표시한다. 후보안 클릭과 `selected` 상태 전환은 별도 동작이며, 선정 권한·상태 전환 API는 팀 승인 전 읽기 전용으로 둔다.

시민 화면은 날짜·출발지·목적지·출발 시각과 선택된 운행안 참조를 보여주고, 정상/파업/셔틀 다리를 구분한다. 실제 운행 정보가 아닐 때 `시뮬레이션·근사 시간표` 경고를 상단과 결과에 표시한다.

## 6. 지도·수요·중복 구간

**로컬 1차 구현 선택:** MapLibre GL JS 6.18.0으로 노선·마커·수요 GeoJSON을 렌더링하고, OpenFreeMap 공개 인스턴스의 `liberty` 벡터 스타일을 지도 배경으로 사용한다. 두 구성요소는 각각 지도 렌더러와 타일/스타일 공급자다. 합성 예제의 버스 6개 노선은 OSRM 공개 데모의 OSM 자동차 도로망 경로를 일회성 조회해 fixture에 저장한다. 이 선택은 **화면 시연**에 한정하며 버스 통행 가능성, 실제 노선, 실제 운행거리 또는 배포용 경로 엔진 채택을 뜻하지 않는다. 전역 D08 기술 스택 채택과 배포·인증은 별도 팀 ADR이 필요하다. 카카오맵은 한국어 기반 지도 품질 대조용, Leaflet은 WebGL이 불가능하거나 저밀도 지도에 필요한 대안으로 남긴다. 상세 비교·라이선스는 [지도 기술 검토](d-opensource-map-stack.md)와 [실사용 근거](d-opensource-usage.md)에 기록한다.

| 평가 항목 | MapLibre + OpenFreeMap **선택** | Leaflet + 별도 타일 | Kakao Maps |
| --- | --- | --- | --- |
| 오픈소스·라이선스 | MapLibre BSD-3, OpenFreeMap 코드 MIT, OSM 데이터 ODbL·스타일 별도 | Leaflet BSD-2, 타일·데이터 계약 별도 | Kakao 웹 API는 자체 이용 조건 |
| 무료·키 | 공개 OpenFreeMap은 현재 무료·키 없음. 운영 보장은 별도 | 라이브러리 무료, 타일 제공자에 좌우 | 앱/JavaScript 키·등록 도메인 필요 |
| 한국 표시 품질 | OSM 기반, 서울 실화면 평가 필요 | 선택한 타일에 좌우 | 국내 지도 대조 후보 |
| 버스·철도/GeoJSON·GTFS | GeoJSON line/circle 레이어, GTFS 변환은 D API | GeoJSON/Polyline 지원, GTFS 변환은 D API | Overlay 가능, GTFS 변환은 D API |
| 수백 노선·수요 원 | WebGL 일괄 렌더링이 유리할 것으로 예상; 실측 필요 | 적은 객체에 단순, 많은 객체는 Canvas·클러스터 검토 | 성능·쿼터 별도 실측 필요 |
| 레이어/마커/툴팁 | source·layer 표시 전환, circle layer·popup | layer group·marker·tooltip | SDK overlay·marker |
| 셔틀 중복 구간 | 검증된 공통 segment의 별도 GeoJSON line layer | Polyline 별도 layer | Polyline overlay |
| 로컬·배포 | 정적 JS + D Python API, 외부 CDN/타일 네트워크 필요 | 정적 JS + D API, 타일 별도 | 키·도메인 등록 필요 |
| 타일 제한·비용 | 공개 무료 정책은 변경 가능; 공급자 변경/자체 호스팅 경로 유지 | 타일 제공자별 별도 확인 | 현재 정책·쿼터를 운영 전 확인 |
| D API 연동·실데이터 확장 | D GeoJSON 응답을 source `setData`로 교체, 지도 재작성 불필요 | GeoJSON layer 교체 가능 | 변환 계층 필요 |

이 비교는 두 렌더러의 성능을 서울 전체 GTFS로 측정한 결과가 아니다. 배포 환경의 비용·서비스 수준을 보증하지 않는다. `tile.openstreetmap.org` 공식 래스터 서버는 사용하지 않는다.

좌표는 A의 CRS·축 순서를 확인한 뒤 검증된 WGS84 표시 좌표로 변환한다. 노선 shape/검증된 segment geometry가 있으면 polyline으로 그린다. 합성 예제의 OSRM 자동차 도로 추정은 별도 출처로 표시한다. 정류장 좌표만 연결한 선은 점선 `선형 미확인`으로 표시하며 실제 도로·운행 구간이라고 주장하지 않는다. 노선번호 검색은 ID 매칭 결과를 사용한다. 같은 번호의 여러 노선은 운영자에게 선택을 요구한다. 정류장·역 아이콘의 호버 확대·툴팁은 구현했으며 키보드 포커스는 후속 접근성 작업이다.

수요 버블은 A의 비식별 집계 `DemandRow`를 정류장 또는 합의된 공간 격자·시간 bin으로 묶는다. 반경은 해당 bin에서 관측한 **인원값의 단조 함수**로 계산하고 범례에 원값·면적/반경 스케일을 명시한다. 한 출발 수요를 여러 근처 정류장에 임의로 복제하지 않는다. 도착·출발·전체 승하차는 각각 무엇을 합산했는지 표시하고, `전체 승하차`를 고유 승객 수라고 부르지 않는다. 파업 영향 수요는 A의 명시적 노선/OD 매핑이 있을 때만, 미충족 수요는 동일한 `RunInputs`의 B/M07 결과와 위치 귀속 정보가 있을 때만 제공한다. 중복 집계 방지 키·공간 할당 방식은 A와 합의한다. 자료가 거친 지역 단위라면 그 단위만 그린다.

현재 합성 DEMO는 80개 가상 OD 셀을 지도에서 10개 거점 원으로 합산하고 인원에 따라 반경을 키운다. 이 표시 규칙은 A 실제 OD의 공간 귀속 기준을 확정하지 않는다.

셔틀/정상 노선의 중복은 동일한 **검증된 방향별 도로 segment ID 또는 geometry와 운행 범위**가 있을 때만 판정한다. 단지 정류장 이름·위치 또는 양 끝점이 같아도 중복으로 세지 않는다. OSRM 자동차 경로는 실제 버스가 같은 방향·차로를 운행한다는 증거가 아니므로 현재 화면은 `판정 불가`로 둔다. 검증 선형이 확보된 뒤에만 중복 구간 시각화를 구현한다. 중복을 이유로 셔틀 운행을 자동 삭제하지 않는다.

현재 합성 DEMO의 **화면 효과**는 좌표가 정확히 같은 노선 선분에서 셔틀 선만 투명 처리하고, 정상 노선 레이어를 끄면 다시 그린다. 이 효과는 `판정 불가` 상태를 바꾸지 않으며 실제 중복률·길이·방향별 도로 공유 값으로 사용하지 않는다.

공간 분석 2차 단계에는 Shapely의 LineString·buffer·intersection을 **미터 단위 투영 CRS**에서 쓰는 방안을 우선 검토한다. 허용 오차는 설정값으로 두고, 가까운 평행 도로/반대 방향/교차만 하는 선을 실제 도로 공유로 확정하지 않는다. 검증된 방향별 공통 segment를 우선하고, 중복 구간을 union한 뒤 길이를 한 번만 세어 `중복률=공통 길이/셔틀 전체 길이`를 계산한다. 현재 A 배포 GTFS에는 `shapes.txt`가 없고 B 셔틀 실선형도 없어 실제 중복 거리·비율은 계산하지 않는다. 오래된 합성 fixture의 가상 공통선 필드는 판정 근거로 사용하지 않는다. A가 검증 망을 제공하지 않을 경우에만 OSMnx의 보행/도로망을 검토한다. Valhalla의 도로 경로·지도 매칭, OpenTripPlanner의 GTFS+OSM 복합 경로는 별도 D06 의사결정과 설치·운영 비용 검토 후 확장 후보로 둔다.

## 7. A/B/C 입출력 계약과 필수 검증

아래는 D의 **요청·소비 제안**이다. 팀이 아직 승인하지 않은 필드는 M00 확정 계약으로 취급하지 않는다.

| 공급자 → D | 필요한 산출물/필드 | D 검증과 출력 | 미합의·막힘 |
| --- | --- | --- | --- |
| A M01/M02 | GTFS `agency, routes, trips, stop_times, stops, calendar/calendar_dates, transfers`, `feed_version`, `DatasetVersion`, `NetworkSnapshot`, 좌표/CRS, route·stop namespace, 명시적 파업 `route_id`, 도로 shape/segment | ID 참조·서비스일·시간 역전·좌표·버전 검사 → 정상 노선/정류장/경로·지도 | 배포 ZIP 내부 철도 시각 역전 3건, 근사 버스 시간표, 노선 분류/파업 범위, 도로 형상 |
| A M03 | `DemandProfile`의 `scope_mode`, OD `origin_stop_id,destination_stop_id,time_bin_s,passenger_count`, 정류장 대응표·품질/누락 보고 | 중복 키·음수·서비스일·GTFS ID·분모 검사 → 수요 버블/차트 | `single_bus_leg` 대 `full_journey`, 실제 OD 위치 귀속, 영향/미충족 산식 |
| B M05 | `(plan_id,plan_revision)`, service date, 셔틀 GTFS trip/stop/time/`block_id`, 일정 검증, `SimulationResult`, `RunInputs` 참조·지표·실패 사유 | C 안 및 A 정류장 ID와 조인 → 셔틀/차량·평가·경로 | PR #34/#36 미병합, `stops.txt` 선택적, 승인된 피드/검증 출처·실제 수요 연결 |
| C M04 | `Scenario`, 후보 `ServicePlan`의 `plan_id,revision,status,routes,vehicle_duties,trips`, `Assessment`, 입력 묶음/체크섬 | 초안·추천·B 시뮬레이션·M07 결과 분리 → 후보 비교 | C `Plan`의 `trips=[]`와 `pending_b`, 선택 상태 소유자·revision 변경 이벤트 |
| M07 | 보고서 URI·체크섬·`pass`·`fully_verified`·위반과 재계산 지표 | 동일 plan/revision/RunInputs 결합 → 검증 배지와 선택 게이트 | 현재 `fully_verified=false`; 어떤 검증 수준이 선택 조건인지 합의 필요 |

B GTFS의 `block_id`는 차량 식별 의미가 해당 산출물에서 보장되는지 확인한다. `stops.txt`가 없으면 A의 **동일 namespace의 검증된 stop_id**에 정확히 조인하고, 누락·중복·좌표 불일치는 해당 셔틀 스냅샷을 거부한다. A 정상 피드와 B 셔틀 피드의 `service_date`가 같아야 경로에 합친다. B `feed_version`만으로 `(plan_id,revision)`을 유추하지 않고 별도 manifest를 요구한다. C 근사 `Assessment` 수치와 B 실험 지표는 원천·분모·입력 묶음별로 별도 표시한다.

## 8. D 내부 모델과 어댑터 경계

`ADataAdapter.load(source, manifest) -> ABundle`, `BDataAdapter.load(source, manifest, a_bundle) -> BBundle`, `CDataAdapter.load(source, manifest) -> CBundle`, `DemoDataAdapter.load() -> SnapshotCandidate`를 **제안 인터페이스**로 둔다. 모든 `load`는 원본을 수정하지 않고 오류 목록/품질 보고를 반환하거나 구조화된 예외를 낸다. `build_snapshot(a,b,c,verification) -> DashboardSnapshot`이 교차 참조를 확인하고 불변 스냅샷을 만든다. 프런트엔드는 D API만 읽는다.

| 내부 모델(제안) | 최소 필드·불변 조건 |
| --- | --- |
| `SourceRef` | `kind=demo/observed/derived/simulated/approximate`, 공급자, artifact URI, schema version, service date, checksum, 생성/관측 기준일. 없는 체크섬은 `null` |
| `RouteView`, `StopView`, `SegmentView` | namespace+원본 ID, 표시명, 수단·운행 상태, WGS84 좌표/검증 geometry, 출처. 표시용 번호는 키가 아님 |
| `DemandCell` | scope, OD/공간 집계키, time bin, mode, passenger_count, source, 단위·중복 정책. 미계산 값은 `null` |
| `FleetView` | 확보/가용/배정 차량 집합과 ID·정원·가용 시간. 미연결 집합은 빈 집합이 아닌 `null` |
| `PlanRef`, `PlanView` | `(plan_id,revision)`, scenario, status, timetable/검증 참조, 후보/시뮬레이션/선택 상태 분리 |
| `MetricView` | 값, 단위, 분모, `RunInputs` ID+checksum, 계산 출처·검증 수준. 비교 시 동일 입력 묶음 필수 |
| `DashboardSnapshot` | snapshot ID, service date, 각 source/version, routes/stops/demand/fleet/plans/metrics, warnings, 완전성·data mode. 한 번 게시하면 불변 |

`auto` 모드는 화면 영역별 출처를 명시하고, 동일 카드·그래프의 분자/분모를 demo와 real에서 섞지 않는다. `real` 모드에서 필요한 도메인이 없으면 그 영역만 `unavailable`; 핵심 참조가 불일치하면 스냅샷 게시 자체를 거부한다. `demo` 모드에는 큰 `합성 예제` 표시를 고정한다.

## 9. M08 API 제안

경로와 JSON 필드명은 **D 전용 초안**이며 D08/D09 및 M08 ADR 승인 전에 대외 계약으로 공개하지 않는다. 모두 현재 `snapshot_id`·`source`·`service_date`를 공통 envelope에 포함한다. API 시각은 offset 있는 ISO 8601, 내부 시간은 Asia/Seoul service date 기준 정수 초다.

| 제안 경로 | 요청 | 응답 요지 |
| --- | --- | --- |
| `GET /api/d/dashboard` | `mode`, `scenario_id` | 요약·가용 데이터·스냅샷/출처·경고 |
| `GET /api/d/sources` | 없음 | A/B/C/M07 도메인별 연결·검증·버전·마지막 성공/오류 |
| `GET /api/d/routes` | `scenario_id`, `kind`, `operator`, `mode`, `q` | 실제 route_id와 표시명·종류·상태, 페이지/범위 |
| `GET /api/d/map` | `scenario_id`, `bounds`, `layers`, `plan_id`, `revision` | 정류장/역·검증 geometry·개략 여부·중복 판정 상태 |
| `GET /api/d/demand` | `scenario_id`, `time_bin_s`, `mode`, `bounds` | 공간 셀·값·단위·집계 범위·총합·출처 |
| `GET /api/d/fleet` | `scenario_id`, `plan_id`, `revision` | 가용/배정 집합, 정원·시간 및 미연결 상태 |
| `GET /api/d/plans` | `scenario_id`, `input_bundle_id` | 후보, `(plan_id,revision)`, 검증 배지·비교 가능 여부 |
| `GET /api/d/plans/{plan_id}/{revision}` | 없음 | 해당 revision만의 노선·셔틀 trip·지표·출처·위반 |
| `POST /api/d/journeys/search` | origin/destination, offset 포함 `departure_at`, `plan_id`, `revision` | 다리·예상 도착·`plan_mode=simulation`·범위·경고 |
| `POST /api/d/sources/refresh` | 개발용 수동 새로고침 요청 | 검증 결과·새 snapshot ID 또는 기존 ID 유지/오류. 인증·배포 정책 확정 전 외부 노출 금지 |

오류 본문은 M00의 `{code,message,details,request_id}`를 따른다. `INPUT_INVALID`, `DATA_VERSION_MISMATCH`, `DATA_INCOMPLETE`, `NO_PATH`, `OUT_OF_SCOPE`, `PLAN_INFEASIBLE`, `JOB_FAILED`를 구분한다. HTTP 상태 매핑은 M08 합의 사항으로 남기고 계약 테스트를 작성한다. `NO_PATH`는 **검증된 입력과 명시한 검색 범위**에서만 반환한다. 버스 전용·1회 환승 검색의 무경로를 서울 전체 여정 불가로 번역하지 않는다. 미선택/미검증 셔틀을 시민 경로에 넣지 않는다.

## 10. 합성 fixture와 동작 기준

`tests/fixtures/d_dashboard/`에 한 서비스일의 손검산 가능한 정상 버스·철도역·정류장 좌표·셔틀 trip·OD·차량·2개 운행안·동일 `RunInputs`·검증/실패 예제를 둔다. JSON manifest는 schema version, service date, 각 파일의 체크섬, `plan_id`와 revision, 출처=`synthetic`, 생성 규칙을 적는다. 노선번호가 같은 다른 route_id, 자정 이후 운행, 좌표 없는 정류장, 다른 날짜/버전, 실패한 검증을 오류 fixture로 둔다. 합성 입력은 개인 교통카드 행이 아니라 정수 가중 OD다.

결정론적 비교 계산은 고정 승객 발생·구간시간·차량/정원·정차시각을 입력으로 받아 충족/미충족·대기·도보·환승·탑승 실패 원인·차량거리 중 **실제 계산 가능한 항목만** 낸다. 보행망이 없는 예제에서는 `walking_time_s=null`; 실패 원인을 모르면 `unknown`, 운행거리 형상이 없으면 `null`이다. A/B의 진짜 평가를 대신하는 수치라고 표시하지 않는다.

## 11. 실제 자료 수신·검증·교체

개발 환경 설정에 A/B/C/M07 산출물 경로와 `mode`를 지정한다. **경로 자체는 설정 값**이고 프런트엔드나 모듈 import 경로에 박지 않는다. 최초에는 수동 새로고침을 제공하고, 안정적인 manifest/파일 완료 신호가 합의되면 변경 감지를 추가한다. 여러 파일이 하나의 GTFS/평가 묶음을 구성하므로 파일별 부분 게시를 하지 않는다.

`discover → stage → schema/참조/서비스일/체크섬 검증 → cross-source 검증 → SnapshotCandidate → 원자적 게시 → API 갱신 알림/재조회` 순서로 처리한다. 내용 지문으로 같은 산출물의 중복 로딩을 피한다. 갱신 실패 시 이전 정상 snapshot을 유지하고 `stale`·마지막 성공 시각·오류를 보여준다. 데이터 스키마가 바뀌면 암묵적 보정 대신 명시적 버전 어댑터 또는 `DATA_INCOMPLETE`/`DATA_VERSION_MISMATCH`를 낸다. `real` 모드에서 데모 fallback을 조용히 끼워 넣지 않는다. 스냅샷 전환 직전/직후 동시 요청은 각각 하나의 완전한 버전만 본다.

원본 교통카드·개인 식별자, API 키, ZIP 원본은 Git에 넣지 않는다. 공개 가능한 집계라도 이용 조건·노출 범위를 확인한다. 실제 파일 변경 테스트는 합성 파일 두 버전으로 수행하여 화면 코드를 바꾸지 않고 API와 지도·지표의 버전이 교체되는지 증명한다.

## 12. 버전, 비교, 검증, 선택

모든 실행·조회·캐시는 `(scenario_id, service_date, plan_id, revision, input_bundle_id, checksum, snapshot_id)`의 해당 조합으로 묶는다. 동일 `plan_id`의 다른 revision은 다른 운행안이다. `feed_version`, 파일 SHA-256, M00 `checksum`은 출처와 알고리즘이 합의되기 전 동의어가 아니다. 비교는 같은 `RunInputs`와 분모·scope·정책·서비스일일 때만 허용한다. seed만 같다는 이유로 비교 가능으로 표시하지 않는다.

화면 배지는 `draft`, `B 일정 가능`, `B 시뮬레이션 완료`, `M07 검사 통과`, `M07 완전 검증`, `실험 선택`을 다른 상태로 보여준다. 현재 M07의 `pass=true, fully_verified=false`는 **완전 검증으로 표시하지 않는다**. 팀이 M07 선택 기준과 상태 전환 주체를 합의하기 전에는 쓰기/선택 API를 만들지 않고 후보 비교·근거 표시까지만 진행한다. 선택이 승인되더라도 이는 실험용 시뮬레이션 선택이다.

## 13. 파일별 책임과 현재 이관 상태

| 경로 | 책임 | 현재 상태 |
| --- | --- | --- |
| `src/busstrike/routing/gtfs_preview.py`, `gtfs_header_check.py` | GTFS 기본 검사, 합성 서비스일·정차시각 로딩, 직행/1회 환승 | 이관 |
| `src/busstrike/routing/normal_reachability.py` | 합성 정상 버스 도달 가능 조회 | 이관; A 실제 OD·GTFS 매핑 미연결 |
| `src/busstrike/routing/preview_server.py`, `web/citizen/` | localhost 합성 미리보기 API·시민 화면 | 이관 |
| `src/busstrike/api/d_dashboard.py`, `d_geo.py`, `d_scenario.py` | 운영자 로컬 API, A GTFS 지도 어댑터, 합성 배분 | 이관; 공용 M08 계약 아님 |
| `src/busstrike/api/d_models.py`, `d_adapters/`, `d_snapshot.py` | 통합 DTO·A/B/C 어댑터·원자적 공용 snapshot | 미구현 |
| `src/busstrike/routing/gtfs_input.py`, `journey.py` | 실제 A/B 서비스 입력 및 선택 revision의 경로 통합 | 미구현; 현재 검색기는 합성 버스만 지원 |
| `web/operator/` | 운영자 대시보드·지도·필터·가상 수요 시나리오 | 이관; 최신 브라우저 육안 수락 미완료 |
| `web/reference/` | 원본 UI 참고 자산 | 이번 이관에서 제외; 원본 작업 폴더는 변경하지 않음 |
| `tests/fixtures/d_dashboard/`, `tests/fixtures/routing/` | 합성 입력·도로선형·GTFS fixture | 이관; 실제 원본 자료 없음 |
| `tests/routing/`, `tests/api/` | GTFS 파서·합성 경로·API 검사 | 이관 |
| `tests/web/` | 브라우저 상호작용 자동화 | 미구현 |
| `docs/design/d-operator-dashboard-handoff.md`, `d-opensource-map-stack.md` | 구현 현황·출처·라이선스·한계 기록 | 이관 |

현재 구현은 계획의 합성 개발 단계까지만 포함한다. D08 기술 합의·실제 A/B/C 산출물·M07 검증·브라우저 수락이 필요한 항목은 미완료이며, `pyproject.toml`에 외부 의존성은 추가하지 않았다.

## 14. 구현 순서와 작은 검토 단위

| 순서 | 작업 단위 | 완료 조건 |
| --- | --- | --- |
| 0 | 이 계획·D08/D09·M08·A/B/C 입력/검증 게이트 합의 | 미결정 필드와 소유자·검토 기한을 Issue/ADR에 기록. 사용자 계획 승인 |
| 1 | 새 패키지에 기존 D 파서·정상 경로 기능 이관 | 옛 정상/오류/경계 테스트 동일 의미로 통과, 옛 폴더·worktree 보존 |
| 2 | D 내부 모델·합성 fixture·어댑터 | demo API에서 출처/버전 노출, 잘못된 ID·날짜·revision 거부 |
| 3 | 대시보드 API·시나리오 결정론 계산 | 합성 조회·필터·비교·버전·오류 응답과 동일 입력 비교 검증 |
| 4 | 실제 지도와 운영자·시민 화면 | 지도 조작·검색·수요 모드·레이어/시나리오 분리·경고·선택 상태 브라우저 검증 |
| 5 | A 실제 GTFS/집계 OD 어댑터 | 유효 자료 교체·다중 agency/노선, 철도 품질·ID 오류 게이트; 없는 OD는 미연결 |
| 6 | B/C/M07 산출물 어댑터 | 미병합 PR 직접 import 없이 파일/DTO로 연결, revision/RunInputs/검증 불일치 거부 |
| 7 | 원자적 refresh·회귀·인수인계 | 실패 시 이전 정상본, 프런트 수정 없는 교체, 실제 실행 명령·한계 기록 |

각 단위는 한 Issue·검토 가능한 PR로 나누고 PRD ID와 M06/M08/M10을 연결한다. 이 문서 작성 단계에는 Issue/PR을 생성하지 않는다. 현재 브랜치는 최신 `main`과 같은 HEAD에서 시작했고 D 변경은 미커밋 상태다. GitHub 반영 범위는 사용자가 직접 검토한다.

## 15. 테스트와 수락 조건

| 검증 영역 | 필수 사례와 기대 결과 |
| --- | --- |
| 서버/API | 로컬 서버와 두 화면 접속, 출처/스냅샷 조회, 검색·필터·페이지/범위, M00 오류 구조, 지원 밖 시간/지역과 자료 부족/무경로 구분 |
| 지도·UI | 노선번호→실제 ID 선택·강조·자동 이동; 회사/수단/파업 필터; 레이어 토글에 지표 불변; 마커 호버·툴팁·키보드; 셔틀/중복/판정 불가 구분 |
| 수요·차량 | 시간대·5개 수요 모드와 합계/범례, 중복 방지, 실제 값 크기 대응; 가용/배정/미배정과 미연결 구분 |
| 시나리오·평가 | 동일 `RunInputs` 기반 재계산, 다른 입력 묶음 비교 거부, C 근사/B 평가/M07 검증 배지 분리, 미검증 계획 경로 제외 |
| A/B/C 어댑터 | A ZIP의 서비스일·버전·좌표/ID 검사; B GTFS의 stop 조인과 trip/시간; C revision 변경; 날짜·체크섬·namespace 불일치 거부 |
| refresh | 두 합성 버전 전환 시 프런트 코드 변경 없음, 실패/부분 파일 시 이전 정상본 유지 및 상태 표시, 동시 요청은 단일 스냅샷 |
| 회귀·현실성 | 기존 D 합성/실제 버스 검색 테스트 이관·유지; 실제 철도 3 trip 오류 진단; 실자료 기능은 실제 실행한 범위만 보고 |

브라우저 상호작용은 자동화 또는 동등한 재현 절차와 캡처로 검증한다. 테스트 명령은 채택된 스택과 실제 파일이 생긴 뒤 적는다. 아직 존재하지 않는 린트/빌드나 실제 자료 테스트를 통과했다고 보고하지 않는다. `main` 병합 전 최신 HEAD에서 CI·교차 리뷰가 필요하다.

## 16. 기술적 한계와 위험

1. A 피드의 버스 시간은 관측 구간시간 기반 근사값이다. 철도 시간 역전 3건, 단절/누락 구간이 있어 실제 전체 여정 가능 판단에 바로 쓰지 않는다.
2. 현재 D 버스 검색은 같은 정류장 최대 1회 환승이며 보행·철도·좌석/최소 환승시간을 계산하지 않는다. 확대 정책 D06이 합의되기 전 시민 결과에는 범위와 경고를 표시한다.
3. 수요 공간 해상도는 A의 비식별 집계 수준을 넘을 수 없다. 세밀한 버블을 위해 개인 동선을 생성하거나 임의 분산하지 않는다.
4. 합성 예제의 자동차 도로망 추정 선형은 확보했지만 실제 버스 방향별 운행 선형은 없다. 따라서 실제 노선 중복은 판정할 수 없고 신규 셔틀 정류장 연결선은 점선의 미확인 표시다.
5. PR #34/#36은 Draft이며 현재 `main`의 B 구현으로 가정할 수 없다. M07 완전 검증도 아직 성립하지 않는다.
6. D08 기술 스택, D09 인증·배포, 지도 키/타일 약관이 미정이다. 개발용 refresh를 공개 운영 기능으로 노출하지 않는다.
7. 이전 Draft PR의 옛 파일 경로와 현재 D 이관 파일은 별도 검토가 필요하다. 현재 브랜치의 D 파일은 미커밋이므로 PR에 포함할 파일을 명시적으로 확인해야 한다.

## 17. 다른 팀과 합의할 질문·게이트

| 대상 | 합의할 내용 | 결정 전 D 동작 |
| --- | --- | --- |
| A | 철도 시간 역전 3건 정정/품질 보고; namespace↔GTFS ID 대응표; 파업 route_id; agency·노선 유형; shape/segment; OD 집계 단위·관측일·미매칭률 | 철도/영향·미충족 수요/중복 판정은 미연결 또는 판정 불가 |
| B | PR #34/#36 병합·산출물 manifest; `stops.txt` 처리; service date, `block_id` 의미; `(plan_id,revision)`/RunInputs·검증 상태 연결; 실패 사유·거리 지표 | B 셔틀·실제 충족률 미표시 |
| C | 후보 `Plan`/`ServicePlan` 출력 버전, 차량·노선 ID, `Assessment` 분모, 추천 vs 선택 상태 소유자, revision 교체 규칙 | 후보와 근사 평가만 읽기 전용 표시 |
| M07·전원 | `pass`와 `fully_verified`의 선택 게이트; `validated→selected` 권한·저장소; 동일 RunInputs 비교; D06 보행·환승/수단 범위 | 실제 선택 API 비활성, 시민 경로는 선택된 검증 안만 대상으로 제한 |
| 팀/ADR | D08 백엔드·프런트·지도 기술과 타일/키; D09 접근 제어·배포; M08 URL·HTTP 상태·응답 버전 | API는 D 전용 제안으로만 구현·표시 |

공용 M00 변경이 필요하면 D에서 먼저 코드로 굳히지 않고 영향·호환성·fixture를 Issue/ADR에 기록해 소비 팀 리뷰를 받는다. 위 게이트를 충족하지 못하는 영역은 오류와 필요한 입력을 보여주며 나머지 합성 기능 개발은 지속한다.

## 18. 구현 후 인수인계

구현 완료 시 `docs/design/d-operator-dashboard-handoff.md`에 실제 생성·수정 파일, 실행 명령, API/화면 사용법, demo/real/auto 출처, A/B/C 파일 배치와 manifest, 테스트 **실행 명령과 결과**, 미실행 이유, 현재 미연결/제한, 계획 대비 변경을 기록한다. 화면 캡처와 합성/실제 예제 응답을 첨부하고, 사용자에게 Git 작업이 필요하면 별도 명령·이유를 안내한다. 이 계획 단계에서는 Git 이력과 원격 GitHub를 변경하지 않는다.
