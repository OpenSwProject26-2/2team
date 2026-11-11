# D 파트 — C 프로토타입을 고려한 서비스 인수 테스트 사례

> 기준: 2026-11-08, 팀 `main` C 코드와 `tests/opt` 22개 테스트 메서드 확인. **후속 구현 테스트 계획**이며 D 테스트 코드를 실행했거나 실제 B 연동을 확인했다는 뜻이 아니다.
> M00 v0.1 공통 계약은 팀 검토 대기 중이므로 필드 및 정책 확정은 Issue/ADR 이후 진행한다.

## 1. C 코드가 현재 보장하는 것과 보장하지 않는 것

| C 쪽 근거 | 검증된 C 프로토타입의 의도 | D가 인수하지 말아야 할 주장 |
| --- | --- | --- |
| `opt/models.py`의 `Plan.to_service_plan()` 및 `test_versions_and_export` | 특정 `plan_id`, `revision`, `status=draft`, `schedule_status=pending_b`, `trips=[]`가 추적 가능 | 실제 셔틀 운행시간표가 완성됐다는 주장 |
| `opt/evaluator.py` 및 수요·정원 관련 테스트 | 합성 수요 대상 구간별 정원 기반 근사 평가 | 승객 대기열을 재현한 B 검증 결과 또는 시민 좌석 보장 |
| `opt/integration.py`, `test_b_*` | B 구현이 연결되었을 때 결과를 확인할 포트·검사 | B의 시뮬레이터 및 독립 검증기가 이미 실제 연결됐다는 주장 |
| `opt/search.py`, 결정론·예산 테스트 | 고정 후보·고정 근사 평가 내 탐욕/차량 이동 탐색 | 실제 운행망의 전역 최적해 또는 실시간 경로 엔진 |

## 2. D가 구현할 때 추가해야 할 테스트 (모두 현재는 미구현)

| ID | 입력/상태 | 예상 처리 | 승인·입력 의존성 |
| --- | --- | --- | --- |
| D-T01 | C `ServicePlan`의 `schedule_status=pending_b`, `trips=[]` | 셔틀 운행편 미준비로 처리, `JourneyResult`를 조작해서 반환하지 않음 | B 일정 생성 정의 |
| D-T02 | 동일 `plan_id`에 다른 `revision` | 경로 응답·캐시·시간표 참조를 구분 | M00 / M08 revision 필드명 |
| D-T03 | `plan_id` 불일치 또는 `input_bundle_id`/checksum 불일치 | `DATA_VERSION_MISMATCH`로 거부 | B/M00 버전 연결 |
| D-T04 | B 시간표가 존재하지만 일정 불가능·독립 검증 실패·violations 있음 | 시민에게 검증 완료/실제 운행으로 표현하지 않음; 정책에 따라 안내 제한 | B·M06 정책 |
| D-T05 | GTFS 직행 경로가 없음 + 운행일·ID·시간표 완전 | 지원 범위에서 `NO_PATH`로 구분 | A 시간표 및 M08 오류 매핑 |
| D-T06 | 시간표가 누락되거나 정류장 대응 누락 | `DATA_INCOMPLETE`로 구분, 빈 경로로 성공 처리하지 않음 | A 품질보고 |
| D-T07 | 전면 파업 중단 노선과 검증된 B 셔틀 운행편 | 합의한 제외 정책에 따라 중단 노선을 처리하고 셔틀을 후보에 추가 | `strike_route_ids` 의미 합의 |
| D-T08 | M00 `JourneyResult` 반환 | `legs`, `arrival_at`, `plan_mode=simulation`, `warnings`가 누락되지 않음 | M08 래퍼 계약 합의 |
| D-T09 | C CLI 결과 `b_integration=not_connected` / `estimated=true` | 운영자 UI에 'C 근사·B 미연결'로 표시, 독립 검증 성과로 표시하지 않음 | C·B 지표 매핑 |
| D-T10 | 24:10:00 형태의 GTFS 시각과 정확한 service date | `time_s>86400`을 보존하고 날짜·UTC offset 표시 검증 | A/GTFS 입력 |
| D-T11 | C의 단일 버스 구간 수요 성과와 D의 전체 여정 검색 | 서로 다른 평가 범위를 명시, 하나의 충족률로 합산하지 않음 | D02, D06 |
| D-T12 | 동일한 정류장 재방문이 있는 노선 | 방문 occurrence를 유지하고 방향·순번을 이용 | A `occurrence_id` 매핑 |
| D-T13 | B Draft PR #6의 내부 `Trip`, `schedule_status=compiled`, `independently_verified=false` | 시민 경로 성공·독립 검증·실제 운행으로 표시하지 않음; 정식 시간표 전달 계약 전에는 개발 검토 상태로만 취급 | B 병합·M07·M06 전달 계약 |

## 3. 의존성·담당

- **A:** 정상 교통 GTFS·정류장 ID 매핑·운행일·품질·누락 정보.
- **B:** `(plan_id, revision)`에 속한 실제 셔틀 출발/도착시각·차량 일정·검증 결과 및 변환 규칙.
- **C:** 현재 draft ServicePlan과 향후 B 연동 포트, `estimated`와 검증 결과 구분.
- **D:** M00/M08 계약을 따르는 소비 로직·경로·API·웹 및 위 테스트 작성.

## 4. 1~2일 차와 3~5일 차의 경계

**1~2일 차:** 본 인수 조건을 문서로 팀에 공유하고 확인되지 않은 것을 Issue에 남긴다.
**3~5일 차 이후:** 공통 합성 fixture·B 일정·A 시간표를 확보한 다음 D 테스트를 코드로 작성한다. 공통 `docs/contracts/M00_DATA_CONTRACT.md`를 D 단독으로 수정하지 않는다.

## 확인한 GitHub 자료

- [C 합성 구현 `opt/README.md`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/README.md)
- [C 모델 `opt/models.py`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/models.py)
- [C B 평가 포트 `opt/integration.py`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/integration.py)
- [C 테스트 `tests/opt/test_planning.py`](https://github.com/OpenSwProject26-2/2team/blob/main/tests/opt/test_planning.py)
- [M00 공통 계약](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md)
