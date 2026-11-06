# M00 공통 데이터 계약 v0.1

- 상태: **팀 검토 대기 · 구현 미착수**
- 권장 저장소 경로: `docs/contracts/M00_DATA_CONTRACT.md`
- 적용 범위: M00–M10, API, worker, 웹, 테스트, 실험 산출물
- 원본 설계: [M00 · 공통 계약·전체 구조](https://app.notion.com/p/40868053b697819b9075d34a03a1e039)

> 이 문서는 모듈 간 데이터 교환의 기준 초안이다. 팀이 채택하기 전에는 코드·DB·API가 이미 존재하거나, 미결정 정책이 확정되었다는 뜻이 아니다. 변경은 Issue와 PR을 거치며, 계약·테스트·소비 모듈을 함께 갱신한다.

## 1. 목적과 적용 원칙

M00은 수집(M01), 교통망(M02), 수요(M03), 운행안(M04), 시뮬레이션(M05), 경로(M06), 평가(M07), API(M08), 저장소(M09), 화면(M10)이 같은 의미의 데이터를 주고받게 하는 공통 계약이다.

- 모듈 간 입출력은 이 문서의 DTO 또는 버전이 붙은 산출물 참조로 전달한다.
- 코어 로직은 FastAPI 요청 객체나 DB ORM 객체에 의존하지 않는다.
- 모듈 간 DB 직접 접근으로 계약을 우회하지 않는다.
- 공용 계약 변경은 구현 전에 Issue에 영향과 호환성 계획을 알리고, 영향받는 모듈의 리뷰를 받는다.
- `MUST`는 반드시 지킬 규칙, `SHOULD`는 특별한 사유가 없으면 지킬 규칙이다.

## 2. 공통 표현 규칙

### 2.1 ID와 버전

- 모든 `*_id`는 비어 있지 않은 문자열이다.
- 원천 ID는 원본 문자열을 보존하고 **source namespace**와 함께 취급한다. 노선번호, 정류장 이름, ARS 번호만으로 전역 고유키를 만들지 않는다.
- 정류장 방문은 `route_id + direction + sequence`로 구분한다. 순환노선에서 같은 정류장을 다시 방문하면 다른 `occurrence_id`다.
- 데이터셋, 네트워크, 수요, 시나리오, 입력 묶음, 운행안, 실행 결과는 각각 버전 또는 불변 참조를 통해 추적 가능해야 한다.

### 2.2 시간과 단위

| 대상 | 규칙 |
| --- | --- |
| API 시각 | UTC offset을 포함한 ISO 8601 문자열 |
| 내부 계산 시각 | `Asia/Seoul` service date의 자정부터 센 정수 초 `time_s` |
| 자정 이후 운행 | `time_s`가 86,400초를 넘어도 허용 |
| 거리 | m |
| 시간·지속시간 | s |
| 승객 수·차량 정원 | 정수 |
| 좌표 | 검증 후 CRS와 축 순서를 함께 저장 |

- `PSNG_NUM` 같은 가중 인원은 행 수로 대체하지 않는다.
- 승차 태그 시각을 정류장 도착 시각으로 간주하지 않는다.
- 미확인 구간을 0초나 정류장 간 직선 거리로 임의 보완하지 않는다.

### 2.3 URI와 해시

- `*_uri`는 해당 산출물·보고서·로그의 위치를 가리키는 참조다. 실제 저장 방식은 M09가 정하되, 소비자는 URI를 통해 동일 산출물을 찾을 수 있어야 한다.
- 체크섬과 설정 해시는 동일 입력·설정·산출물을 재현하고 비교하기 위한 값이다. 해시 알고리즘은 저장소 초기화 PR에서 고정한다.

## 3. 공용 DTO

아래 필드는 최소 계약이다. 각 모듈의 추가 필드는 해당 모듈 문서가 소유하며, 공통 의미를 바꾸는 경우 이 문서를 함께 수정한다.

### 3.1 데이터·망·수요

#### `DatasetVersion` — M01 생성, M02·M03 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `dataset_version_id` | string | 데이터셋 버전 식별자 |
| `observation_date` | date string | 실제 관측 기준일 |
| `source_checksum` | string | 원천 파일 또는 입력의 무결성 참조 |
| `schema_version` | string | 원천/정규화 스키마 버전 |
| `quality_report_uri` | URI string | 결측, 조인 실패, 제외 정책을 담은 품질 보고서 |

#### `NetworkSnapshot` — M02 생성, M04·M05·M06 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `network_snapshot_id` | string | 방향별 교통망 스냅샷 식별자 |
| `dataset_version_id` | string | 생성에 사용한 데이터셋 버전 |
| `artifact_uri` | URI string | 검증된 망 산출물 참조 |
| `validation_report_uri` | URI string | 방향·회전·회차·좌표 검증 보고서 |

#### `DemandProfile` — M03 생성, M04·M05·M07 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `demand_profile_id` | string | 수요 프로필 식별자 |
| `dataset_version_id` | string | 수요의 관측 데이터 버전 |
| `scope_mode` | enum | `single_bus_leg` 또는 `full_journey` |
| `window_start_s` | integer | service date 기준 수요 창 시작 초 |
| `window_end_s` | integer | service date 기준 수요 창 종료 초 |
| `rows` | `DemandRow[]` | OD·시간대별 가중 수요 |

#### `DemandRow` — M03 소유

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `origin_stop_id` | string | 출발 정류장 ID |
| `destination_stop_id` | string | 도착 정류장 ID |
| `time_bin_s` | integer | service date 기준 시간 구간 |
| `passenger_count` | integer | 해당 OD·시간 구간의 가중 인원 |

`scope_mode=full_journey`는 환승 연결과 중복 집계 검증을 마친 뒤에만 사용한다. 두 scope의 결과를 같은 충족률 그래프에 섞지 않는다.

### 3.2 시나리오와 고정 입력

#### `Scenario` — API 생성, 각 엔진 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `scenario_id` | string | 시나리오 식별자 |
| `service_date` | date string | 서비스 기준일 |
| `timezone` | string | 기본값은 `Asia/Seoul` |
| `network_snapshot_id` | string | 사용할 네트워크 스냅샷 |
| `demand_profile_id` | string | 사용할 수요 프로필 |
| `strike_route_ids` | string[] | 영향 운행 노선 ID |
| `fleet` | `Fleet` | 실제 가용 차량 집합 |
| `operating_window` | object | 운행 가능 시작·종료 시간 |
| `policy` | `Policy` | 평가·운행·환승 정책 |

`fleet`에는 최소한 차량 ID, 정원, 차급, 가용 시작·종료 시각, 초기 위치, 종료 조건이 포함된다. 인가대수와 실제 가용 대수를 구분한다.

`policy`에는 최대 누적 대기, 최대 총 이동시간, 허용 환승, 승객 발생 분포, 정차·회차 가정, 미확인 연결 처리 규칙이 포함된다. 값은 Scenario 생성 시 반드시 검토한다.

#### `RunInputs` — 입력 준비 단계 생성, M05·M07 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `input_bundle_id` | string | 고정 실행 입력 묶음 식별자 |
| `scenario_id` | string | 기반 시나리오 |
| `seed` | string 또는 integer | 입력 생성에 사용한 seed |
| `arrival_realization_uri` | URI string | 사전 생성된 승객 발생 입력 |
| `travel_time_realization_uri` | URI string | 사전 생성된 교통·이동시간 입력 |
| `checksum` | string | 입력 묶음 무결성 참조 |

비교 실험은 같은 `RunInputs`를 재사용한다. 동일 seed라도 사건 순서에 따른 난수 호출이 달라질 수 있으므로, seed만으로 동일 입력을 주장하지 않는다.

### 3.3 운행안·실행 결과·시민 경로

#### `ServicePlan` — M04 생성, M05·M06·M07 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `plan_id` | string | 논리적 운행안 식별자 |
| `scenario_id` | string | 기반 시나리오 |
| `revision` | integer | 운행안 불변 버전 |
| `status` | enum | `draft`, `validated`, `selected` |
| `routes` | module-owned value | 노선 정의 |
| `vehicle_duties` | module-owned value | 차량별 임무·일정 |
| `trips` | module-owned value | 실행 운행편 |

- `(plan_id, revision)`의 내용은 불변이다. 수정은 같은 `plan_id`의 증가한 `revision`으로 저장한다.
- 별도의 대안을 분기하면 새 `plan_id`와 원본 `parent_plan_ref`를 기록한다.
- `draft → validated → selected` 순서로 전환한다. 제약 검증에 실패한 안은 `selected`가 될 수 없다.
- `selected`는 화면·실험에서 선택한 시뮬레이션 안일 뿐, 실제 운행 승인이나 배차가 아니다.
- 선택 포인터, 실행·경로·지표 캐시는 모두 `(plan_id, revision)`을 고정해 참조한다.

#### `SimulationResult` — M05 생성, M07·API 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `run_id` | string | 실행 식별자 |
| `plan_id` | string | 실행한 논리적 운행안 |
| `plan_revision` | integer | 실행한 불변 운행안 버전 |
| `input_bundle_id` | string | 사용한 RunInputs |
| `metrics_uri` | URI string | 요약 지표 산출물 |
| `event_log_uri` | URI string | 사건 로그 산출물 |
| `config_hash` | string | 실행 설정 해시 |

#### `JourneyRequest` — API 생성, M06 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `plan_id` | string | 조회 대상 논리적 운행안 |
| `origin` | string | 출발지 또는 정류장 참조 |
| `destination` | string | 목적지 또는 정류장 참조 |
| `departure_at` | ISO 8601 string | UTC offset을 포함한 출발 시각 |

경로 계산 시 선택된 `plan_id`의 정확한 `revision`을 함께 고정해야 한다. API가 외부에 노출하는 revision 필드명은 M08의 API 계약에서 이 문서와 일치하게 정한다.

#### `JourneyResult` — M06 생성, API·웹 소비

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `legs` | array | 승차·하차·보행·환승 단위의 안내 |
| `arrival_at` | ISO 8601 string | 예상 도착 시각 |
| `plan_mode` | enum | 현 단계에서는 `simulation` |
| `warnings` | string[] 또는 structured warnings | 데이터·범위·시뮬레이션 제한 안내 |

시민 경로가 존재해도 좌석 확보를 보장하지 않는다. 실제 운행 데이터·승인 절차가 마련되기 전에는 `plan_mode=operational`로 표시하지 않는다.

## 4. 오류 계약

모든 공통 오류는 아래 구조를 사용한다. HTTP 상태 코드는 M08이 이 오류 코드에 매핑한다.

| 필드 | 의미 |
| --- | --- |
| `code` | 기계가 해석 가능한 오류 코드 |
| `message` | 사람이 읽을 수 있는 요약 |
| `details` | 검증 실패 필드, 누락 데이터, 원인 등 구조화된 추가 정보 |
| `request_id` | 요청·작업·로그를 연결하는 추적 ID |

| 코드 | 의미 |
| --- | --- |
| `INPUT_INVALID` | 시간·단위·필수 필드가 잘못됨 |
| `DATA_VERSION_MISMATCH` | 노선·정류장·수요의 관측 기준이 맞지 않음 |
| `DATA_INCOMPLETE` | 선택한 범위의 수요 또는 시간표가 부족함 |
| `PLAN_INFEASIBLE` | 차량 중복, 정원·시간·회전 제약 위반 |
| `NO_PATH` | 검증된 입력과 지원 범위 안에서 경로가 없음 |
| `OUT_OF_SCOPE` | 지원 지역·시간·수단 밖 요청 |
| `JOB_FAILED` | 계산 실패. 부분 로그는 실패 표시와 함께 보존 |

`DATA_INCOMPLETE`, `NO_PATH`, `OUT_OF_SCOPE`는 서로 다른 상태다. 알 수 없는 구간을 경로 없음으로 축소하거나, 반대로 경로 없음에 데이터 부족을 숨기지 않는다.

## 5. 데이터 흐름과 소유권

```text
M01 DatasetVersion
 ├─ M02 NetworkSnapshot
 └─ M03 DemandProfile ─┐
                          ├─ Scenario → RunInputs → M04 ServicePlan
                          │                           ↓
                          │                    M05 SimulationResult → M07 독립 검증
                          └──────────────────────────────────────────┐
                                                             M06 JourneyResult
```

1. M01은 원천을 검사하고 `DatasetVersion`을 만든다.
2. M02는 `NetworkSnapshot`, M03은 `DemandProfile`과 공통 승객 발생 입력을 만든다.
3. API는 데이터 버전, 파업 범위, 가용 차량, 정책을 고정한 `Scenario`를 만든다.
4. M04는 후보 운행안을 만들고 M05 평가 포트를 호출한다. M05는 M04를 역호출하지 않는다.
5. M05는 동일한 `RunInputs`로 사건 로그와 요약을 만들고, M07은 이를 독립적으로 재계산한다.
6. `validated` 운행안을 선택한 뒤 M06은 고정된 운행안 revision과 정상 운행편으로 시민 경로를 계산한다.

## 6. 검증과 재현성 기준

- 같은 데이터 버전, `RunInputs`, 설정, 알고리즘 버전에서는 같은 결과를 재현할 수 있어야 한다.
- M07은 인원 보존, 차량 일정, 차량 용량을 독립 검사기로 재계산한다.
- contracts 변경은 DTO 직렬화·역직렬화 테스트와 API contract test를 모두 갱신한다.
- 합성 fixture는 손으로 검산 가능한 작은 망·수요를 포함한다.
- 공개 저장소에는 합성 fixture, 재현 스크립트, 이용 조건을 충족한 집계만 둔다. 원시 교통카드 식별자, 인증키, 개인 이동 내역은 코드·로그·응답에 넣지 않는다.

## 7. 아직 확정되지 않은 결정

다음 항목은 이 계약의 사용 방식에 영향을 주지만, 현재 v0.1에서 확정된 값이 아니다. 담당자·기한과 함께 ADR로 결정한다.

| ID | 미결정 항목 | 영향 |
| --- | --- | --- |
| D01 | 첫 지역·시간·관측일 | M01–M07 데이터 범위 |
| D02 | 평가 단위 (`single_bus_leg` 또는 `full_journey`) | M03·M05·M07 지표 |
| D03 | 가용 차량·초기 위치 | M04·M05 제약 |
| D04 | 운행 가능 연결 검증 | M02·M04 후보 생성 |
| D05 | 탐색·배분 알고리즘 | M04 전략 인터페이스 |
| D06 | 환승·도보·정상 수단 범위 | M05·M06 경로 정책 |
| D07 | 최대 대기·총 이동시간 | M03–M07 평가 정책 |
| D08 | 저장소·UI 기술 | M08–M10 구현 방식 |
| D09 | 인증·배포 범위 | M08–M10 접근 제어 |

## 8. 변경 절차

1. 변경 이유, 영향받는 DTO·모듈·API·데이터·테스트를 Issue에 적는다.
2. 호환성이 깨질 경우 이전·이후 계약, 마이그레이션, 롤백 방법을 제안한다.
3. 영향받는 모듈 담당자의 리뷰와 contract test 갱신을 받는다.
4. `M00_DATA_CONTRACT.md`, 관련 모듈 문서, DTO/API 구현을 같은 PR 또는 명시적으로 연결된 PR에서 갱신한다.
5. 병합 후 버전과 변경 이력을 기록한다.
