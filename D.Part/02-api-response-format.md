# D 파트 1~2일 차 — API 요청·응답 형식 (M00 정렬 초안)

> 상태: **D 제안 / M08 API 상세 계약 및 팀 리뷰 대기** | C 구현·22개 테스트 검토 반영: 2026-11-08 | 담당: D (M06·M08·M10)
>
> 기준: 팀 `main`의 [`docs/contracts/M00_DATA_CONTRACT.md`](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md) v0.1. **M00의 공통 DTO 정의와 본 문서의 HTTP API 응답 래퍼를 구분한다.** API 경로·필드 추가·HTTP 매핑은 확정이 아니다.

## 1. 목적·책임

- **M06 경로 엔진(D)**: 선택된 `ServicePlan(plan_id, revision)`과 정상 교통 시간표에 대해 이동 경로(`JourneyResult`) 계산.
- **M08 API(D)**: 시민의 요청을 검증해 M06에 전달하고, 화면용 JSON 및 구조화된 오류 반환.
- **M10 웹(D)**: 경로/평가 정보를 표시. 실제 운행 확정 또는 좌석 예약처럼 표현하지 않음.
- **B(M05·M07), C(M04)**: 운행안 평가·검증과 계획 생성. D는 지표를 재계산하거나 C의 근사 평가를 B의 검증 결과로 표시하지 않음.

**실제 C 연동 상태:** [`opt/__main__.py`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/__main__.py)의 합성 CLI 출력에는 `b_integration: "not_connected"`가 표시된다. C의 `Assessment`와 `EvaluationPort`는 B를 붙이기 위한 인터페이스이고, `tests/opt`의 `assessment()`는 가짜 B 테스트 대역이다. 웹 화면은 이 단계의 점수를 `verified`로 표시하지 않는다.

**B Draft PR #6 상태:** 병합 전 B PR은 C 배분안에서 내부 `Trip` 일정과 `schedule_status=compiled`를 만들 수 있지만, 반환 `Assessment.independently_verified`는 `false`다. 이는 M07 독립 검증 결과나 M00 `SimulationResult`의 공개 산출물이 아니다. D API는 이 Draft 형식을 정식 셔틀 GTFS나 시민 경로용 성공 응답으로 변환하지 않으며, B가 병합 후 전달 계약과 검증 근거를 공개할 때까지 별도 개발 검토 상태로만 다룬다.

## 2. API 엔드포인트 후보 (모두 미구현)

| 메서드 | 제안 경로 | 목적 | 계약 상태 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | 서버 준비 상태 | D 제안 |
| `GET` | `/api/v1/scenarios` | 시나리오 탐색 | D 제안 |
| `POST` | `/api/v1/journeys/search` | 시민 이동 경로 검색 | **M00 `JourneyRequest`에 맞춘 D 제안** |
| `GET` | `/api/v1/evaluations/{plan_id}?revision=1` | 특정 운행안 버전의 평가 결과 | **M00 `SimulationResult`/M07 확인 후 확정** |

기존 `/api/v1/evaluations/{scenario_id}`는 한 시나리오에 여러 운행안/버전이 있을 수 있어 비교 결과를 혼동시킬 위험이 있었다. 위 경로는 **제안 변경안**이며 최종 결정은 M08 API 계약에서 한다.

## 3. 시민 경로 검색 요청

### 3.1 M00 `JourneyRequest`의 최소 계약

| M00 필드 | 형식 | 의미 |
| --- | --- | --- |
| `plan_id` | string | 조회할 논리적 운행안 |
| `origin` | string | 출발지 또는 정류장 참조 |
| `destination` | string | 도착지 또는 정류장 참조 |
| `departure_at` | offset 포함 ISO 8601 | 사용자 출발 희망 시각 |

**M00 추가 조건:** 검색은 해당 운행안의 정확한 **불변 `revision`**을 고정해야 한다. 외부 API에서 사용할 revision 필드명은 M08이 확정하기 전까지 미정이다.

### 3.2 D의 HTTP 요청 JSON 예시 — 추가 필드명은 미확정

```json
{
  "plan_id": "DEMO_PLAN_1",
  "plan_revision": 1,
  "origin": "DEMO_STOP_A",
  "destination": "DEMO_STOP_C",
  "departure_at": "2026-11-09T07:50:00+09:00"
}
```

- `plan_revision`은 **D가 제안한 외부 API 필드명**이다. M00에는 정확한 버전 고정 원칙은 있으나 HTTP 필드명은 미결정이다.
- 기존 D 초안의 `scenario_id`, `origin_stop_id`, `destination_stop_id`는 API에서 제거할지, 별도의 선택/해석 계층으로 유지할지 **팀 합의 필요**. `plan_id`가 어떤 `scenario_id`를 참조하는지는 `ServicePlan`에서 검증한다.
- 시민 UI에서 정류장만 선택하더라도 M00의 `origin`/`destination`에 대응시킬 수 있다. 실제 일반 위치·좌표 검색 지원 여부는 미확정이다.

## 4. 시민 경로 검색 응답

### 4.1 M00 `JourneyResult`의 최소 의미

| 필드 | 의미 |
| --- | --- |
| `legs` | 보행·탑승·하차·환승 등 연결된 이동 단계 |
| `arrival_at` | UTC offset 포함 ISO 8601 예상 도착 시각 |
| `plan_mode` | 현 단계에서는 `simulation` |
| `warnings` | 미지원 범위·입력 품질·시뮬레이션/비보장 안내 |

### 4.2 D 웹용 응답 예시 — 모든 데이터는 가상, 구현 미완료

```json
{
  "plan_id": "DEMO_PLAN_1",
  "plan_revision": 1,
  "journeys": [
    {
      "journey_id": "DEMO_JOURNEY_1",
      "arrival_at": "2026-11-09T08:20:00+09:00",
      "plan_mode": "simulation",
      "warnings": ["합성 셔틀 시간표이며 실제 운행·좌석 확보를 보장하지 않습니다."],
      "total_duration_sec": 1800,
      "transfers": 0,
      "uses_shuttle": true,
      "legs": [
        {
          "mode": "shuttle",
          "route_id": "DEMO_SHUTTLE_1",
          "trip_id": "DEMO_TRIP_1",
          "from_stop_id": "DEMO_STOP_A",
          "to_stop_id": "DEMO_STOP_C",
          "departure_at": "2026-11-09T08:00:00+09:00",
          "arrival_at": "2026-11-09T08:20:00+09:00",
          "duration_sec": 1200
        }
      ]
    }
  ]
}
```

- M00 최소 필드(`legs`, `arrival_at`, `plan_mode`, `warnings`)를 빠뜨리지 않는다.
- `journeys`, `journey_id`, `total_duration_sec`, `transfers`, `uses_shuttle`, `mode`, `trip_id` 등은 **M08/M10용 D 확장 제안**이다. `total_duration_sec`는 출발 희망 시각부터 도착까지(예제는 07:50→08:20, 1800초)로 계산했고, 탑승 구간의 `duration_sec`는 1200초다.
- `plan_mode=simulation`은 실제 운행 여부와 구분하기 위해 사용한다. 기존 `data_status=confirmed`와 같은 상태를 근거 없이 사용하지 않는다.
- `[]`가 단순히 반환되었다는 사실만으로 진짜 `NO_PATH`라고 단정하지 않는다. 시간표 미확보 시 `DATA_INCOMPLETE`, 조회 범위 밖이면 `OUT_OF_SCOPE`다.
- 동일 출발/도착·운행안이더라도 `plan_revision`이 달라지면 경로 결과를 구분하고 캐시도 분리해야 한다.

## 5. 운영자 평가 응답

M00의 `SimulationResult`는 `run_id`, `plan_id`, `plan_revision`, `input_bundle_id`, `metrics_uri`, `event_log_uri`, `config_hash`로 **실행 결과 및 입력 버전을 추적**한다. 평가 값의 단일 공식 JSON 형식은 현재 M00에서 확정하지 않았다.

C의 현재 `opt/integration.py` 내부 `Assessment`는 별도 프로토타입이며 다음을 요구한다.

| 키 | 왜 필요한가 |
| --- | --- |
| `plan_id`, `plan_revision` | 정확히 평가한 운행안 식별 |
| `input_bundle_id`, `input_checksum` | 같은 실험 입력인지 검증 |
| `schedule_feasible`, `independently_verified`, `violations` | B의 일정 가능·독립 검증 여부 |
| `served`, `total` | 충족 수요와 고정 평가 분모 |
| `p90_wait_s`, `distance_m` | 대기·운행자원 지표 |

### C CLI의 현재 출력과 D API를 연결하는 기준

C의 실제 합성 CLI (`python -m opt --input opt/examples/synthetic.json`)가 내보내는 최상위 키는 `mode`, `b_integration`, `input_bundle_id`, `input_checksum`, `comparison`, `evaluations`, `rejected_candidates`, `top_plans`, `exact_comparison`이다. `comparison[]`은 `plan`과 `estimate`를 포함하며, **예상치**인 `estimate.estimated=true`가 B의 검증 완료를 뜻하지 않는다.

| C 출력 | D에서의 취급 |
| --- | --- |
| `comparison[].plan.plan_id`, `revision`, `status=draft` | **운행안 선택 후보**로만 표시; 확정 일정이 아님 |
| `comparison[].plan.schedule_status=pending_b`, `trips=[]` | 시민 경로 검색용 셔틀 시간표 **미준비** |
| `comparison[].estimate.served`, `total`, `fulfillment_rate`, `mean_wait_s`, `distance_m` | 추정 결과 영역에만 표시(`estimated=true`); 실측·독립 검증 지표와 분리 |
| `b_integration=not_connected` | B 연동 및 독립 검증 **미완료** 상태 표시 |
| `top_plans` (최대 3개) | B가 `EvaluationPort`로 재평가할 후보; `selected`와 구분 |
| `exact_comparison` | **고정 후보·동일 근사 평가기 내부**의 소규모 전수 비교로만 표시. 실제 최적해 주장 금지 |

### D 운영자 API 응답 형태 제안 — 실제 B 계산 결과가 아님

```json
{
  "plan_id": "DEMO_PLAN_1",
  "plan_revision": 1,
  "run_id": "DEMO_RUN_1",
  "input_bundle_id": "DEMO_INPUTS_1",
  "plan_mode": "simulation",
  "verification": {
    "schedule_feasible": true,
    "independently_verified": true,
    "violations": []
  },
  "metrics": {
    "evaluation_demand": 100,
    "on_time_arrival_demand": 60,
    "demand_satisfaction_rate": 0.6,
    "p90_wait_s": 600,
    "distance_m": 3000
  }
}
```

이 예제의 수치는 **설명용 가상값**이고 현재 B가 생성한 검증 결과가 아니다. 실제 응답에서는 `metrics_uri`에서 읽은 **같은 run/버전**의 결과만 사용한다. 아직 `metrics_uri`를 제공하는 B 결과는 GitHub `main`에서 확인되지 않았다. C의 `estimated=true` 결과를 위 `independently_verified=true`로 변환하면 안 된다. B 결과가 없을 때는 `pending` 또는 `DATA_INCOMPLETE` 처리 규칙을 M08과 합의한다.

평가 분모는 모든 비교 운행안에서 동일한 `RunInputs`를 기준으로 고정하며, `passenger_count`를 합산한다. 실제 평가 지표명과 0명일 때의 비율, P90 계산 범위 및 포함률은 B·M07에서 최종 정의한다. 기존 D 초안의 `included_demand`와 `demand_inclusion_rate`도 **제안 지표**로 남겨두며 B 결과와 일치시켜야 한다.

## 6. 오류 응답과 HTTP 코드

M00 공통 오류 구조는 다음처럼 **최상위에 네 필드**를 둔다.

```json
{
  "code": "DATA_INCOMPLETE",
  "message": "선택한 날짜의 정상 교통 시간표가 부족합니다.",
  "details": {"service_date": "2026-11-09"},
  "request_id": "DEMO_REQUEST_1"
}
```

| M00 오류 코드 | 의미 | HTTP 상태(제안, M08 확정 전) |
| --- | --- | --- |
| `INPUT_INVALID` | 필드·시간·단위 검증 실패 | 422 |
| `DATA_VERSION_MISMATCH` | 버전·입력 참조 불일치 | 409 |
| `DATA_INCOMPLETE` | 시간표 또는 수요 등 핵심 데이터 누락 | 503 |
| `PLAN_INFEASIBLE` | 차량·운행 일정 제약 위반 | 422 |
| `NO_PATH` | **검증된 범위 안**에서 경로 없음 | 200 + 빈 목록 또는 구조화 오류 중 결정 필요 |
| `OUT_OF_SCOPE` | 지원 대상 지역·시간·수단 밖 | 422 |
| `JOB_FAILED` | 계산 실패 | 500 |

기존 `{"error":{"code":...}}` 래퍼와 `STOP_NOT_FOUND` 코드는 M00 공통 오류 명세와 달라 본문 기본 예시에서 제외했다. 신규 세부 오류 코드 추가가 필요하면 M00 계약 변경 Issue를 먼저 제안한다. 실제 HTTP 매핑 및 200 빈 목록 정책은 **M08에서 미정**이다.

## 7. 웹 화면과 연결할 필드

| UI | 계약/제안 필드 |
| --- | --- |
| 출발·도착·시각 | `origin`, `destination`, `departure_at` |
| 운행안 선택 | `plan_id`, 제안 `plan_revision` |
| 예상 도착 | `journeys[].arrival_at` (M00) |
| 이동 단계 | `journeys[].legs[]` (M00) |
| 시뮬레이션 경고 | `journeys[].plan_mode`, `journeys[].warnings` (M00) |
| 총 이동시간·환승 | `journeys[].total_duration_sec`, `journeys[].transfers` (D 확장 제안) |
| 셔틀 이용 여부 | `journeys[].uses_shuttle` (D 확장 제안) |
| 운영자 비교 | `plan_id`, `plan_revision`, `input_bundle_id`, `metrics`, `verification` (B·C와 합의) |

## 8. 팀 합의가 필요한 결정과 검증 기준

- [ ] M08 HTTP 요청의 `plan_revision` 필드명 및 `scenario_id`와의 연결 결정.
- [ ] C의 `draft` (`trips=[]`, `schedule_status=pending_b`) → B 일정·독립 검증 → 시뮬레이션 `selected` 흐름 확인.
- [ ] B Draft PR #6의 내부 `Trip`/`schedule_status=compiled`와 D가 소비할 정식 시간표 산출물을 구분하고, 병합·M07 검증·전달 형식의 수락 조건을 정함.
- [ ] C CLI의 `estimated=true`/`b_integration=not_connected`를 운영자 API에서 검증 결과와 혼동하지 않기.
- [ ] `tests/opt`의 B 테스트 대역 성공이 실제 B 연동이 아님을 확인하고, 실제 B 데이터가 오면 별도 contract test 작성.
- [ ] `JourneyResult`와 웹용 `journeys[]` 래퍼, 추가 지표 필드 승인.
- [ ] `NO_PATH` vs `DATA_INCOMPLETE` vs `OUT_OF_SCOPE` 및 HTTP 매핑 승인.
- [ ] 운영자 수요 지표를 B/M07 결과와 일치시키고 고정 `RunInputs` 검증.
- [ ] 선택한 `plan_id`, `revision`의 GTFS·정상 교통 데이터 버전 검증.
- [ ] 합성 JSON 계약 테스트를 작성하되 실제 구현/통합 테스트가 완료된 것으로 표시하지 않기.

**1~2일 차 완료 기준:** 공통 최소 DTO, D의 제안 API 입출력, 오류 의미, 추가 필드와 미결정 항목을 문서화해 팀 리뷰를 요청한다. 실제 FastAPI/웹 구현은 이후 단계다.

## 참고

- [M00 공통 계약](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md)
- [C 개발 포트](https://github.com/OpenSwProject26-2/2team/blob/main/opt/integration.py) / [C 설계·실행 안내](https://github.com/OpenSwProject26-2/2team/blob/main/opt/README.md)
- https://fastapi.tiangolo.com/tutorial/response-model/
