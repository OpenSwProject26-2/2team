# D 파트 1~2일 차: API 요청·응답 형식

> 상태: **구현을 위한 API 설계 초안** | 담당: D (`api/`, `web/`) | 기준: 2026-11-06
>
> 시민 경로 조회 및 운영자 평가 화면이 사용할 API의 **입력, 출력, 오류, 화면 연계**를 정의한다. 아래 경로·필드명은 D의 제안 형식이며, 공통 `contracts/`가 생기면 기존 계약을 우선한다.

## 1. API의 역할

시민 웹 화면은 출발·도착·시각을 서버에 보내고, 서버는 경로 탐색기의 결과를 JSON으로 돌려준다. 운영자 화면은 B의 검증기 및 C의 운행안 비교 결과를 요약된 JSON으로 표시한다. 백엔드 프레임워크는 팀 계획대로 **FastAPI**를 사용한다.

## 2. 우선 정의할 API 목록

| 메서드 | 경로 (제안) | 입력 | 출력 | 화면 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/v1/health` | 없음 | 서비스 동작 상태 | 개발 확인 |
| `GET` | `/api/v1/scenarios` | 없음 | 선택 가능한 시나리오 ID·이름·상태 | 시민·운영자 공통 |
| `POST` | `/api/v1/journeys/search` | 출발/도착 정류장, 출발 시각, 시나리오 | 경로 목록 및 이동시간·환승 수 | 시민 |
| `GET` | `/api/v1/evaluations/{scenario_id}` | 시나리오 ID | 수요 충족률 등 평가 지표 | 운영자 |

이번 1~2일 차는 **API 명세 작성**이 목표이고, 서버의 실제 구현은 이후 개발 단계에 진행한다.

## 3. 시민 경로 검색 API

### 요청: `POST /api/v1/journeys/search`

```json
{
  "origin_stop_id": "DEMO_STOP_A",
  "destination_stop_id": "DEMO_STOP_B",
  "departure_at": "2026-11-09T08:00:00+09:00",
  "scenario_id": "DEMO_STRIKE_SHUTTLE"
}
```

| 필드 | 타입 | 의미 | 필수 |
| --- | --- | --- | --- |
| `origin_stop_id` | string | 출발 정류장 식별자 | 예 |
| `destination_stop_id` | string | 도착 정류장 식별자 | 예 |
| `departure_at` | string (ISO 8601) | 사용자가 출발하려는 날짜·시각 | 예 |
| `scenario_id` | string | 적용할 교통·파업·셔틀 조건 | 예 |

### 성공 응답: `200 OK` (모든 값은 합성 예시)

```json
{
  "scenario_id": "DEMO_STRIKE_SHUTTLE",
  "data_status": "simulated",
  "origin_stop_id": "DEMO_STOP_A",
  "destination_stop_id": "DEMO_STOP_B",
  "journeys": [
    {
      "journey_id": "DEMO_JOURNEY_1",
      "total_duration_sec": 900,
      "transfers": 0,
      "uses_shuttle": true,
      "legs": [
        {
          "mode": "shuttle",
          "route_id": "DEMO_SHUTTLE_1",
          "from_stop_id": "DEMO_STOP_A",
          "to_stop_id": "DEMO_STOP_B",
          "duration_sec": 900
        }
      ]
    }
  ]
}
```

| 응답 필드 | 타입 | 화면 표시 또는 의미 |
| --- | --- | --- |
| `data_status` | string | `simulated`: 시뮬레이션, `confirmed`: 실제 확인된 자료. 실제 확인 여부는 증거가 있어야 한다. |
| `journeys` | array | 검색된 이동 경로 목록, 경로 없음은 빈 배열 `[]` 제안 |
| `total_duration_sec` | integer | 이동 총 소요시간(초): 900 → 15분 |
| `transfers` | integer | 환승 횟수 |
| `uses_shuttle` | boolean | 임시 셔틀 포함 여부 |
| `legs` | array | 이동 구간의 순서(도보, 버스, 도시철도, 셔틀 등) |
| `mode` | string | `walk` / `bus` / `subway` / `shuttle` 후보 (최종 값 미확정) |

**주의:** `total_duration_sec`는 내부 초 단위 규칙을 따르며, GTFS 시각 문자열은 입력 경계에서 처리한다. API 결과가 실제 운행으로 확인된 경우가 아니면 `confirmed`라고 표시하지 않는다.

## 4. 운영자 평가 결과 API

### 요청: `GET /api/v1/evaluations/DEMO_PROPOSAL`

### 성공 응답: `200 OK` (합성 예시)

```json
{
  "scenario_id": "DEMO_PROPOSAL",
  "data_status": "simulated",
  "metrics": {
    "evaluation_demand": 100,
    "included_demand": 90,
    "on_time_arrival_demand": 60,
    "demand_satisfaction_rate": 0.6,
    "demand_inclusion_rate": 0.9
  }
}
```

| 필드 | 의미 | 계산 규칙 |
| --- | --- | --- |
| `evaluation_demand` | 평가 대상 전체 승객 수 | **모든 비교 운행안에서 동일한 분모** |
| `included_demand` | 실제 평가에 포함한 수요 | 제외 사유·인원 별도 집계 |
| `on_time_arrival_demand` | 시간 조건 내 도착한 수요 | 원천 데이터의 `PSNG_NUM` 가중 합 기준 |
| `demand_satisfaction_rate` | 수요 충족률 | `on_time_arrival_demand / evaluation_demand` |
| `demand_inclusion_rate` | 입력 수요 포함률 | `included_demand / evaluation_demand` (제안 지표) |

**100명, 90명, 60명은 설명용 값이며 실제 실험 결과가 아니다.** 평가 분모가 0일 때 비율의 표현 방식은 정해야 한다. 프로젝트의 평가 지표는 B 검증기·공통 모델과 일치시킨다.

## 5. 오류·빈 결과 처리 제안

| 상황 | HTTP 코드 제안 | 응답 방식 |
| --- | --- | --- |
| 필드 형식 누락·검증 오류 | `422` | FastAPI 기본 검증 오류 형식 사용 후보 |
| 존재하지 않는 정류장/시나리오 | `404` | 구조화된 오류 JSON |
| 정상적으로 검색했지만 경로 없음 | `200` | `journeys: []` |
| 경로 GTFS/엔진 아직 준비되지 않음 | `503` | 데이터 미준비임을 알리는 오류 |

오류 응답의 사용자 정의 형태(제안):

```json
{
  "error": {
    "code": "STOP_NOT_FOUND",
    "message": "정류장 ID를 찾을 수 없습니다."
  }
}
```

`422` 응답을 자체 형식으로 변경할지는 후속 결정이 필요하다. 현재는 **일관된 JSON 처리 정책을 정했다는 점**에 의미가 있다.

## 6. 웹 화면과 API 연결

| UI 요소 | API 입력/응답 필드 |
| --- | --- |
| 출발 정류장 | `origin_stop_id` |
| 도착 정류장 | `destination_stop_id` |
| 출발 시각 | `departure_at` |
| 교통 상황 선택 | `scenario_id` |
| 소요시간 | `journeys[].total_duration_sec` (분 단위 표시) |
| 셔틀 탑승 여부 | `journeys[].uses_shuttle` |
| 이동 단계 | `journeys[].legs[]` |
| 운영자 수요 충족률 | `metrics.demand_satisfaction_rate` (백분율 표시) |
| 운영자 평가 대상·포함률 | `metrics.evaluation_demand`, `metrics.demand_inclusion_rate` |

## 7. 다음 개발 단계의 테스트 기준

- [ ] 정상 요청에 JSON 경로 목록이 반환된다(합성 예제로 시작).
- [ ] 경로 없음과 API 오류를 구분한다.
- [ ] 소요시간 필드는 초 단위 정수이며 프론트에서 분 단위로 정확히 표시한다.
- [ ] 파업 중단 노선은 경로 구간에 포함되지 않는다.
- [ ] 셔틀 경로는 `uses_shuttle: true`로 표시된다.
- [ ] `evaluation_demand`가 시나리오 비교 중 임의로 바뀌지 않는다.
- [ ] 응답에 `simulated`와 `confirmed`를 구분한다.

### 이번 단계 완료 기준

시민 검색 요청 JSON, 검색 결과 JSON, 운영자 평가 JSON, API URL·메서드, 필드 의미, 오류 처리, UI와 연결되는 항목을 **문서로 작성해 GitHub에 기록했다면 1~2일 차 API 명세 작성은 완료**다. 실제 API 구현은 별도 작업이다.

## 8. 아직 확정되지 않은 내용

- `contracts/`의 실제 Pydantic 스키마 (2026-11-06 현재 저장소 루트에 미생성)
- 시나리오 ID·상태 enum의 최종 명칭
- B 검증기 결과에서 운영자 API까지의 실제 데이터 경로
- 검색 엔진(OTP 또는 r5py)에 따른 상세 경로·지리 정보 필드
- 외부 라이브러리 의존성은 팀 규칙상 승인 없이 추가하지 않는다.

## 참고

- GTFS Schedule: https://gtfs.org/documentation/schedule/reference/
- FastAPI 응답 모델: https://fastapi.tiangolo.com/tutorial/response-model/
