# D 파트 1~2일 차 — GTFS 입력 및 M06 연계 요구사항 (M00 정렬 초안)

> 상태: **설계 초안 / 팀 검토 필요** | C PR #4 및 `tests/opt` 코드 검토 반영: 2026-11-08 | 담당: D (M06 경로, M08 API, M10 화면)
>
> 공통 기준: 팀 `main`의 [`docs/contracts/M00_DATA_CONTRACT.md`](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md) v0.1. M00 자체도 팀 검토 대기 중이다. 본 문서는 공통 계약을 변경하지 않으며, 미결정 항목을 사실처럼 확정하지 않는다.

## 1. 목적과 책임 경계

시민 요청에 대해 **선택된 시뮬레이션 운행안의 특정 버전**과 정상 교통망을 기준으로 실제 시간표상 가능한 경로를 검색하는 데 필요한 입력을 정의한다. 1~2일 차 산출물은 **요구사항 문서**이며, 실제 GTFS 제작·경로 엔진 구현·실제 운행 승인에 해당하지 않는다.

| 제공 주체 | 필요한 입력 또는 참조 | D에서 사용하는 방식 | 현재 확보 여부 |
| --- | --- | --- | --- |
| A (M01·M02 등) | 정상 버스·도시철도 GTFS 또는 이를 만들 수 있는 시간표, `NetworkSnapshot` 및 원천 ID 매핑 | 운행 가능한 정상 수단과 정류장·환승망 로드 | 실제 시간표·ID 매핑 미확인 |
| 팀의 시나리오 생성 단계 | M00 `Scenario` (`scenario_id`, `service_date`, `timezone`, `network_snapshot_id`, `strike_route_ids`, `policy` 등) | 파업 대상·적용 날짜·탐색 정책 확인 | 공통 DTO는 문서 초안, 인스턴스 미확인 |
| C (M04) | `ServicePlan`의 `plan_id`, 불변 `revision`, `status`, 노선·배분 정보 | **선택된 운행안 참조**. C의 draft만으로는 경로 시간표가 완성되지 않음 | 합성 프로토타입 있음, B 연계 미완료 |
| B (M05·M07) | 차량별 일정·실제 운행편·정차시각, 검증 상태·평가 결과 (GTFS 변환 형식은 협의) | 검색 가능한 셔틀 운행편 추가; 검증되지 않은 시간표를 실제 운행으로 표시하지 않음 | `main`에는 미확인. Draft PR #6에 내부 합성 일정 생성기가 있으나 병합·독립 검증 전 |

C의 `opt/` 프로토타입은 `ServicePlan`의 `vehicle_duties`와 `trips`를 비운 **draft**를 제공한다. 이를 바로 시민에게 안내할 수 있는 운행편으로 간주하지 않는다. `selected`는 시뮬레이션상의 선택이며 실제 셔틀 운행 승인이라는 뜻이 아니다.

**B Draft PR #6 확인 범위(2026-11-08):** B의 병합 전 PR은 C 초안에서 차량별 내부 `Trip`을 컴파일하고 `schedule_status=compiled`를 만들 수 있다. 그러나 이 `Trip`은 `stops`, 도착·출발 초, 차량 ID 등을 가진 B 내부 DTO이며 GTFS의 `routes.txt`·`trips.txt`·`stop_times.txt`·운행일 파일 묶음이 아니다. 같은 PR의 `Assessment.independently_verified`는 항상 `false`다. 따라서 D는 이 Draft 산출물을 개발 검토 근거로만 읽으며, 병합·공개 산출물·M07 독립 검증·시간표 전달 계약이 갖춰지기 전에는 시민 경로 입력으로 사용하지 않는다.

### C 코드·테스트를 확인한 후 추가한 구체적 입력 게이트

C의 [`opt/models.py`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/models.py) `Plan.to_service_plan()`이 현재 출력하는 값의 핵심은 다음과 같다(다음 예시의 ID는 설명용):

```json
{
  "plan_id": "DEMO_PLAN_C",
  "scenario_id": "synthetic-v1",
  "revision": 1,
  "status": "draft",
  "plan_mode": "simulation",
  "input_bundle_id": "synthetic-development-v1",
  "schedule_status": "pending_b",
  "routes": [],
  "vehicle_duties": [],
  "trips": []
}
```

**주의:** 실제 C 출력의 `routes`에는 배정된 노선·차량 수가 채워질 수 있다. 위는 `trips=[]`, `vehicle_duties=[]`, `schedule_status=pending_b`의 의미를 보여 주는 축약 사례이며 C의 원본 JSON 전체가 아니다.

D는 다음 조건을 만족하는 **B가 생성·검증한 산출물**이 나오기 전에는 C의 draft를 완성된 셔틀 GTFS로 변환하거나 경로 안내에 포함하지 않는다.

1. 정확한 `(plan_id, revision)`과 `scenario_id`가 일치하고, 시간표·정류장 ID가 망 버전에 대응한다.
2. B가 해당 버전의 `trip_id`별 정류장 방문 시각과 서비스 날짜를 제공하며, 공통 검증 절차에서 정상 처리된다.
3. B의 일정 실현 가능성 및 독립 검증 결과를 구분해 확인한다. `pending_b`, 검증 실패, 데이터 부족인 경우 이를 성공 경로로 표시하지 않는다.
4. C 예제의 `Segment.travel_s`, `Route.outbound`는 **C의 근사 평가용 모델**이다. GTFS의 `stop_times.txt` 또는 실제 `trips.txt`를 대신하지 않는다.
5. C 첫 버전은 `scope_mode=single_bus_leg`, 동일 차고지·동일 제원 차량, 수송 방향의 정류장 재방문 미지원이다. M00의 `occurrence_id` 전체 지원이나 다중 환승 검색을 C가 완료했다고 해석하지 않는다.
6. B 내부 `Trip`의 `schedule_status=compiled`만으로는 위 조건을 충족하지 않는다. D가 소비할 정식 시간표 산출물의 전달 형식, 운행일, 정류장·노선 ID namespace, 버전·검증 참조는 B·D 계약에서 별도로 합의한다.

C의 22개 [`tests/opt/test_planning.py`](https://github.com/OpenSwProject26-2/2team/blob/main/tests/opt/test_planning.py)는 이 범위의 **C 내부 테스트**이며 D의 정상 GTFS 수신·셔틀 변환·경로 검색을 대신 검증하지 않는다.


## 2. GTFS Schedule 입력 요구사항

공식 규격: https://gtfs.org/documentation/schedule/reference/

| 파일 | D가 활용할 주요 필드 | 요구 방식 |
| --- | --- | --- |
| `agency.txt` | `agency_name`, `agency_url`, `agency_timezone`; 필요 시 `agency_id` | 기관 및 시간대 확인 |
| `stops.txt` | `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, 선택 `stop_code`, `parent_station` | 정류장 위치·식별, 역/플랫폼 연결 |
| `routes.txt` | `route_id`, `route_type`, `route_short_name` 또는 `route_long_name` | 운송수단 및 노선 식별 |
| `trips.txt` | `route_id`, `service_id`, `trip_id`, 선택 `direction_id`, `shape_id` | 날짜별 개별 운행편과 방향 연결 |
| `stop_times.txt` | `trip_id`, `stop_id`, `stop_sequence`, `arrival_time`, `departure_time` | 정류장 방문 순서 및 시간 기반 검색. 개별 시각의 생략 가능 여부는 공식 GTFS 규칙 준수 |
| `calendar.txt` / `calendar_dates.txt` | `service_id`와 적용일 | 운행일 확인. 두 파일의 조건부 필요 관계 확인 |
| `frequencies.txt` | `trip_id`, `start_time`, `end_time`, `headway_secs`, `exact_times` | **선택**. 배차간격 기반 서비스는 별도 해석 로직 필요 |
| `transfers.txt`, `pathways.txt` | 환승 가능성·최소 환승 시간·역 내부 경로 | **선택**. 환승 검색을 시작할 때 범위 결정 |
| `shapes.txt` | `shape_id`, 위경도, 순번 | **선택**. 지도에서 실제 노선 형상 표현 |

GTFS 규격의 일반적 필수 여부와 **우리 서비스에서 반드시 받아야 하는 값**은 구분한다. 서울 정류장 엑셀에 운행편별 도착시각이 없다면 GTFS 전체가 완성된 것이 아니다. `frequencies.txt`가 있다고 해서 정류장별 정확한 시각이 항상 관측된 것도 아니다.

## 3. ID·시간·데이터 품질 계약 (M00 v0.1 기준)

1. 모든 공통 `*_id`는 빈 문자열이 아닌 **문자열**이다. 원천 ID는 `source namespace`를 포함해 보존한다. `ARS_ID`의 앞자리 0을 없애지 않는다.
2. 서울시 엑셀 `NODE_ID`와 GTFS `stop_id`가 자동으로 같다고 가정하지 않는다. 매핑 출처·버전·미일치 수량을 보고한다.
3. 노선번호·정류장 이름만으로 유일한 ID를 만들지 않는다. 방향·정류장 순번을 보존하며, **같은 정류장 재방문은 별개의 방문(`occurrence_id`)**으로 취급한다.
4. 외부 API 시각은 `+09:00`과 같은 UTC offset을 포함한 ISO 8601, 내부 계산은 `Asia/Seoul`의 **service date 자정부터 정수 초(`time_s`)**다. 24시간을 넘는 GTFS 시각도 유효할 수 있다.
5. 좌표의 CRS·축 순서를 검사한다. 위치정보 XLSX의 `X좌표`는 경도, `Y좌표`는 위도이나, 원시 교통카드 DH의 이름이 유사한 필드와 혼동하지 않는다.
6. 확인되지 않은 구간 이동시간을 **0초 또는 직선거리 기반 추정값으로 조용히 보완하지 않는다**. 관측·추정·합성 여부와 누락 범위를 따로 기록한다.
7. 입력 데이터는 데이터셋/망/수요/운행안 **버전 및 참조**로 추적한다. 경로 검색은 `plan_id`와 정확한 `revision`을 고정한다.
8. 시뮬레이션 운행안은 `plan_mode=simulation`으로 표시하며 **실제 차량 운행·좌석 확보를 보장하지 않는다**.

## 4. 시나리오와 경로 입력 연결

M00의 공통 `Scenario`는 다음과 같은 필드들을 포함한다. 이 표는 **공통 DTO의 일부**이며 D가 새로 만든 최종 API가 아니다.

| 필드 | 역할 |
| --- | --- |
| `scenario_id`, `service_date`, `timezone` | 어떤 날짜와 상황으로 계산하는지 |
| `network_snapshot_id`, `demand_profile_id` | 같은 교통망·수요 버전을 고정 |
| `strike_route_ids` | 파업 영향을 받는 노선 식별자 (전체 중단과 영향 범위의 해석은 추가 합의) |
| `fleet`, `operating_window`, `policy` | 가용 차량·운영시간·최대 대기·환승·도보 등 설정 |

기존 D 문서의 `suspended_route_ids`는 M00의 `strike_route_ids`와 이름 및 잠재적 의미가 달랐다. 공통 데이터에서는 **`strike_route_ids`를 기준으로 참조**하되, 이 목록이 정확히 어떤 노선을 제거하는지(전면 중단/영향 노선)는 팀이 결정해야 한다. 기존 `shuttle_feed_id`는 M00의 확정 필드가 아니므로, B의 시간표 산출물 참조 방식이 정해질 때까지 **제안 사항**으로 남긴다.

경로 조회 시에는 `Scenario`의 조건과 **C의 `ServicePlan(plan_id, revision)`**, B가 해당 버전에 실제로 생성한 셔틀 운행편을 일치시켜야 한다. `draft` 또는 검증 실패 운행안에서 시간표가 만들어졌다고 간주하지 않는다.

## 5. GTFS를 받았을 때 검증할 항목

- [ ] GTFS 파일·필수 열이 존재하고 읽기 가능한 형식인지 확인 (현재 `gtfs_header_check.py`의 **기본 범위**).
- [ ] `stop_times.trip_id` → `trips.trip_id`, `trips.route_id` → `routes.route_id`, `trips.service_id` → 운행일 정보 간 참조가 유효한지 확인.
- [ ] 중복 운행편 ID, 중복 정류장 방문/재방문, `direction_id`, `stop_sequence`를 구분하여 검사.
- [ ] 정류장 좌표, CRS, 축 순서, 원천 ID ↔ GTFS ID 연결률을 검증.
- [ ] `calendar` 예외일 및 24시 이후 GTFS 시각이 실제 검색 날짜에 올바르게 적용되는지 확인.
- [ ] 시간표가 **실측·공식 계획·추정·합성** 중 어느 출처인지 명시.
- [ ] 정상 운행 노선과 파업 영향 노선의 구분, B 셔틀 운행편과의 ID 충돌 여부 확인.
- [ ] 참조한 `network_snapshot_id`, `plan_id`, `revision`, `input_bundle_id`, 시간표 버전을 재현할 수 있는지 확인.
- [ ] C draft의 `schedule_status=pending_b`와 B의 완성 운행편을 구분하고, `vehicle_duties`·`trips`가 비어 있으면 셔틀 GTFS가 준비된 것으로 간주하지 않기.
- [ ] C의 B 연동 포트(`opt/integration.py`)가 **실제 B 구현으로 연결됐는지** 확인. C의 테스트 대역(mock) 결과를 실제 독립 검증이라고 표시하지 않기.
- [ ] 데이터가 부족하면 `DATA_INCOMPLETE`로 보고. 검증된 범위 내 경로 없음인 `NO_PATH`, 지원 범위 밖인 `OUT_OF_SCOPE`와 혼동하지 않기.
- [ ] 원본 교통카드 식별자·개인 이동 기록·인증키·허가되지 않은 원본 ZIP/XLSX를 커밋하지 않기.

**현재 검증 현황:** D의 로컬 GTFS 기본 검사기는 이전 대화에서 9개 단위 테스트 및 Google 규격 샘플 헤더 검사를 통과했다. **참조 무결성, 운행시간 정확성, 실제 경로 탐색은 그 검사 결과로 입증되지 않는다.**

## 6. A·B·C와 확인할 사항

| 상대 | D가 확인할 질문 | 필요한 결과 |
| --- | --- | --- |
| A | 정상 교통 GTFS 또는 정류장별 운행시각은 어떤 자료·관측일·파일 구조로 제공하는가? 정류장 ID 매핑은? | 입력 버전, ID 대응표, 출처·품질·누락 보고 |
| A | 정상 버스·지하철과 도보·환승 연결은 어느 범위까지 지원하는가? | D06 정책·지도망 범위 |
| B | `ServicePlan`에 맞는 셔틀 운행편과 정차시각은 언제, 어떤 산출물로 나오는가? | `plan_id`, `revision`에 묶인 시간표·검증 정보 |
| C·B | `opt/integration.py`의 `Assessment` 또는 M00 `SimulationResult`를 서비스에서 어떻게 소비하는가? | 버전 및 `input_bundle_id`가 일치하는 결과 참조 |
| 전원 | 지역·평가 단위·정책·운영 시간·시간표 버전·공통 합성 예제는 무엇인가? | Issue/ADR 합의 결과 |

## 7. 다음 단계와 완료 기준

**1~2일 차 완료 범위:** GTFS 입력 파일·소유권·ID/시간/버전 규칙·검증 체크리스트·미합의 사항을 문서화한다. 이 단계에서는 OTP/r5py/FastAPI/React 구현을 완료한 것으로 표시하지 않는다.

**3~5일 차 이후:** 작은 **팀 공통 합성 GTFS**로 정상 교통 경로, 파업 영향 노선 처리, B의 셔틀 시간표 포함 여부를 테스트한다. `single_bus_leg` 데이터에서 전체 여정 수요 충족을 입증했다고 주장하지 않는다.

## 참고

- 팀 공통 계약: [`M00_DATA_CONTRACT.md`](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md)
- 팀 공용 규칙: [`AGENTS.md`](https://github.com/OpenSwProject26-2/2team/blob/main/AGENTS.md)
- C 합성 프로토타입: [`opt/README.md`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/README.md)
- GTFS Schedule: https://gtfs.org/documentation/schedule/reference/
