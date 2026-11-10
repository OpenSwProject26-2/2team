# D → A 정상 교통 도달 가능 조회: 첫 전달물과 남은 입력

> 상태: **합성 버스 정류장 간 개발 미리보기 · 팀 검토 대기**. [Issue #20](https://github.com/OpenSwProject26-2/2team/issues/20), [ADR-002](ADR-002-normal-reachability-preview.md)를 참고한다. 이 응답은 M08 정식 API 계약이 아니다.

## 요청받은 D-1~D-6과 현재 구현

| 작업 | 현재 D 산출물 | 실제 통합 전에 필요한 것 |
| --- | --- | --- |
| D-1 경로 엔진 실행 | 작은 정상·셔틀 GTFS의 날짜·정차시각을 읽어 직행/동일 정류장 1회 환승 검색. `normal_reachability.py`는 정상 feed만 읽음 | 선정 지역의 정상 GTFS, 보행망, 실제 엔진 규모·기능 검증 |
| D-2 ID 연결 확인 | 입력은 이미 매핑된 GTFS `stop_id`; 이름·좌표·교통카드 ID에서 자동 변환하지 않음 | A의 원천 namespace↔GTFS ID 대응표, 미매핑 수·중복 방문/방향·출처 |
| D-3 정상 교통 조회 | 합성 정상 버스만으로 정류장 A→C 직행 조회와 A용 `reachable`/`unreachable`/`indeterminate` 요약 | 지하철·보행·환승 최소시간·최대 대기 정책, 실제 시간표 버전 |
| D-4 상태 관리 | 기존 시나리오 미리보기와 새 정상 조회를 분리. 셔틀은 `not_independently_verified` 표시 | 파업 영향 노선 의미, B 검증 결과, `validated`·`selected` 소유자와 피드 선택 규칙 |
| D-5 API·화면 | 로컬 `/__demo__/normal-reachability` 예제 응답과 시민 합성 화면 1종 | M08 API 합의, 운영자 비교 화면, 지표·지도 자료 |
| D-6 통합 검증 | 합성 직행·환승·누락·범위 밖·자정 이후 테스트 | A/B 실제 산출물 연계, M07 검증, 실제 배포·운영 점검 |

따라서 첫 전달물은 **합성 정상 버스용 조회 함수와 API 예제 응답**까지 충족한다. 이 결과로 서울의 전체 정상 교통 도달 가능성이나 잔여 수요를 확정할 수 없다.

## A가 전달할 입력과 D가 돌려줄 값

입력 `query_id`는 비식별·집계 수요 묶음 또는 조회 식별자다. 한 사람의 교통카드 원천 ID를 넣지 않는다. `origin_stop_id`·`destination_stop_id`는 현재 GTFS 정류장 ID이며, A가 교통카드/원천 정류장 ID를 먼저 대응시켜야 한다. `departure_at`은 `+09:00`처럼 offset이 있는 ISO 8601이다. 시간표 버전·서비스일·망 버전은 입력 feed/manifest에 고정한다.

| 출력 필드 | 현재 의미 |
| --- | --- |
| `query_id`, `origin_stop_id`, `destination_stop_id`, `departure_at` | 어떤 조회인지 추적 |
| `status`, `reachable` | `reachable/true`, `unreachable/false`, `indeterminate/null` 중 하나 |
| `arrival_at`, `transfer_count` | 정상 버스 경로가 있을 때 예상 도착·환승 횟수 |
| `walking_time_s` | 현재 항상 `null`: 보행망 미제공·미계산. 0초가 아님 |
| `normal_timetable_version`, `network_snapshot_id`, `service_date` | 판정에 사용한 합성 입력 참조. 시간표 로드 실패 시 버전은 `null` |
| `scope`, `data_kind` | `synthetic_normal_bus_stop_to_stop`, `synthetic`으로 적용 범위를 표시 |
| `failure_code`, `warnings`, `legs` | 경로 없음·판정 불가 원인과 확인 가능한 운행편 |

`unreachable`은 **현재 합성 버스 정류장 간 범위에서 `NO_PATH`**라는 의미다. 지하철역까지 걸어가거나 다른 수단으로 이동할 수 있는지의 판정은 `indeterminate`로 남아 있다. A는 이 값만으로 실제 잔여 수요를 빼거나 더하지 않아야 한다. `normal_timetable_version=d-normal-demo-v1`은 D fixture의 수동 버전 라벨이며 실제 A 시간표의 체크섬 또는 채택된 공용 버전이 아니다.

## 실행과 API 예제

저장소 루트에서 정상 버스만 조회한다. 셔틀 폴더를 읽지 않는다.

```powershell
py -3 D.Part/normal_reachability.py synthetic-demand-1 A C 2026-11-07T00:00:00+09:00
py -3 D.Part/normal_reachability.py synthetic-demand-2 D C 2026-11-07T00:00:00+09:00
```

첫 조회의 핵심 반환값은 다음과 같다. 실제 함수는 요청 정류장·이동 단계·경고도 포함한다.

```json
{
  "query_id": "synthetic-demand-1",
  "status": "reachable",
  "reachable": true,
  "arrival_at": "2026-11-07T00:03:00+09:00",
  "walking_time_s": null,
  "transfer_count": 0,
  "normal_timetable_version": "d-normal-demo-v1",
  "network_snapshot_id": "synthetic-v1",
  "scope": "synthetic_normal_bus_stop_to_stop",
  "data_kind": "synthetic",
  "failure_code": null
}
```

로컬 개발용 HTTP 응답은 `py -3 D.Part/preview_server.py` 실행 후 아래 명령으로 확인한다.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/__demo__/normal-reachability?query_id=synthetic-demand-1&origin_stop_id=A&destination_stop_id=C&departure_at=2026-11-07T00%3A00%3A00%2B09%3A00' | ConvertTo-Json -Depth 8
```

HTTP 최상위는 `{"preview_only":true,"result":{...}}`이다. `D`→`C`는 `unreachable`과 `NO_PATH`; 지원하지 않는 정류장·날짜 또는 시간표 누락은 `indeterminate`와 각각 `OUT_OF_SCOPE`·`DATA_INCOMPLETE`다. 이 로컬 경로와 HTTP 200 래퍼는 팀이 채택한 M08 계약이 아니다.

## A/B/C·팀에 확인할 구체적 자료

1. **A:** 교통카드 집계 정류장 ID, 원천 namespace, GTFS stop_id, 망/시간표 버전, 매칭 근거·미매칭 개수를 가진 대응표. 정류장 이름이나 좌표만으로 유일 ID를 만들지 않음.
2. **A:** 정상 버스·지하철 GTFS의 서비스일, 정차시각, 모드별 운행 여부, 품질 보고. 출발 정류장/위치에서 역 출입구까지 보행 가능한 연결망과 소요시간 자료.
3. **B/C:** 선택된 `(plan_id, revision)`의 실제 셔틀 정차시각과 독립 검증 참조. 정상 baseline에는 이 셔틀을 섞지 않음.
4. **전원:** 최대 도보·대기·환승 횟수/최소 환승시간, `strike_route_ids` 해석, 전체 여정 대 단일 버스 구간 평가 단위, M08 외부 응답·HTTP 상태를 ADR로 결정.
5. **운영자 화면:** B/M07의 수요 충족률·대기·탑승 실패 사유·차량거리와 동일 입력 묶음 참조, A의 노선 형상·정류장 좌표/CRS가 필요. 현재 D 합성 경로 응답은 이 지표를 생산하지 않는다.
