# D 파트 — A·B·C 구현 현황과 M00 정합성 검토

> 기준: 2026-11-08 팀 GitHub `main` 및 D 작업 브랜치, C `opt/`와 `tests/opt` 확인. **코드 읽기 기반 현황**이며 A/B 팀원 개인 PC·미푸시 작업까지 확인한 것은 아니다.
> 이 문서는 논의 자료이며 팀 승인이나 M00 변경을 대신하지 않는다.

## 1. GitHub에서 확인한 현황

| 역할 | 저장소에서 확인한 사실 | D의 후속 작업 |
| --- | --- | --- |
| A | `A.Part/README.md`는 빈 파일. `main`에서 A의 실제 GTFS 생성·정제 코드 또는 출력을 확인하지 못함 | 정상 교통 GTFS/시간표, 데이터 버전, 정류장 ID 매핑을 A에게 문의 |
| B | `main`의 `B.Part/README.md`는 빈 파일. Draft PR #6에는 합성 M05 일정 생성·시뮬레이션 코드가 있으나 병합 전이며 `independently_verified=false` | 정식 일정/검증 산출물, GTFS 변환 및 API 소비 방식 문의 |
| C | Issue #3, PR #4가 병합됨. `opt/`에서 합성 입력, 2개 기준안, 후보·근사 평가, 탐색, B 평가 포트 및 **22개 `tests/opt` 테스트 메서드**를 구현 | `ServicePlan`과 `Assessment`/`SimulationResult` 매핑 논의 |

**C의 공개 예제:** 정류장 A·B·C·D, 차량 2대(정원 각 2명), `single_bus_leg`, 균등 7/9·수요 비례 6/9·탐색 7/9의 **근사 평가**. 실제 서울 수요, B의 독립 검증 및 전체 여정 최적화 결과가 아니다.

**B Draft PR #6의 공개 검토 범위:** C 배분안에서 차량별 내부 `Trip`과 `schedule_status=compiled`를 만들고, `Assessment`의 plan/revision·입력 checksum·분모를 확인한다. 그러나 PR은 Draft·미병합이고 반환값의 `independently_verified=false`이며, D가 소비할 GTFS 또는 M00 `SimulationResult` 산출물 전달 형식은 제공하지 않는다. 따라서 D는 이를 B 구현 완료나 시민 경로 연동 완료의 근거로 삼지 않는다.

## 2. 실제 C 연동 포인트

- `opt/models.py`는 C 내부 dataclass 모델이며 **공용 M00 DTO 확정본이 아니다**.
- C의 `Plan.to_service_plan()`이 내보내는 draft는 `plan_id`, `revision`, `status=draft` 등이 있지만 `vehicle_duties=[]`, `trips=[]`, `schedule_status=pending_b`다. D는 이 결과만으로 셔틀의 정류장별 출발시각을 표시할 수 없다.
- `opt/integration.py`의 `EvaluationPort`는 B가 구현할 포트다. Draft PR #6은 이 포트에 연결할 합성 M05 adapter를 제안하지만 `independently_verified=false`다. `Assessment`는 `plan_id`, `plan_revision`, `input_bundle_id`, `input_checksum`, `schedule_feasible`, `independently_verified`, `violations`, `served`, `total`, `p90_wait_s`, `distance_m`를 담는다.
- D는 B가 만든 실제 일정과 검증 상태를 확인한 뒤, M00 `SimulationResult` 및 `JourneyResult`의 공통 ID·버전과 매핑할 필요가 있다.

## 3. `tests/opt` 전체 검토 결과

파일: [`tests/opt/test_planning.py`](https://github.com/OpenSwProject26-2/2team/blob/main/tests/opt/test_planning.py). 확인한 `test_*` 메서드는 **22개**이고 `unittest` 형식이다. **테스트가 코드에 작성되었다는 사실과 실제 실행 통과는 구분한다.** 이번 검토는 GitHub의 소스 코드 열람이며, 로컬에서 C 테스트를 실행하거나 CI 통과 기록을 검증하지 않았다.

| 검증 범주 | 테스트에서 확인한 내용 | D가 유의할 점 |
| --- | --- | --- |
| 기준안·경계 입력 | 균등·비례 정수 배분, 차량 0대·수요 0명, 작은 차량 대수 | 0수요 비율은 null; 0인 운행안에 가짜 성과 부여 금지 |
| 정원·수송 | 3명 중 2명 탑승 손계산, 구간별 정원·하차 후 재사용, OD 중복 방지 | C의 `served`는 **근사 승객 배분 계산**이며 D의 좌석 예약 보장 아님 |
| 시간 제약 | 최대 대기 240초/239초 경계, 이동 제한 120초/119초, 차량 가용시간·종료시간, 정차·회차·복귀 | D 조회에도 service date 기준 초와 정확한 운행편 시각이 필요 |
| 경로 방향·물리 제약 | 역방향 이동 수요 제외, 끊긴/미등록 구간, 회전·회차 불가능 후보 제외 | C의 검증된 **구간**을 곧바로 GTFS `trip`으로 오인하지 않기 |
| 입력·버전 | 음수·비정상 정수, 중복 정류장 ID, `input_checksum`·수요 버전 불일치 | D도 버전 불일치 시 `DATA_VERSION_MISMATCH` 처리 |
| 탐색·재현성 | 동일 입력 결정론, 평가 예산, 소규모 전수 비교 | 최선은 합성 고정 후보·C 근사 평가기 내의 성적일 뿐 |
| B 포트 테스트 대역 | 최대 상위 3안, 일정 불가능·미검증·위반 후보 배제, 분모·checksum·NaN/P90 검사, 충족인원→P90 우선순위 | **테스트 함수의 가짜 B 결과**다. 실 B 서비스와 연결됐다는 의미 아님 |
| holdout/범위 | 최종 평가셋으로 재탐색 금지, 다른 평가셋 고정안 평가, full_journey 입력 거절 | 시민 전체 경로 수요 충족률로 확대 해석 금지 |

**현재 `opt/__main__.py` 출력의 `b_integration`은 `not_connected`다.** 이 프로그램은 C 운행안 평가 프로토타입이지 정류장별 `stop_times.txt`를 생산하는 스케줄러가 아니다. `tests/opt`에서 B 결과를 반환하는 `assessment()` 역시 테스트 대역이다.

### C 합성 입력을 D가 그대로 사용할 수 없는 이유

C의 [`opt/examples/synthetic.json`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/examples/synthetic.json)은 `stops`, `segments`, `allowed_turns`, `routes`, `demands`, `fleet`, `policy`로 되어 있다. 이 JSON은 GTFS 파일(`agency.txt`, `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, 운행일) 묶음이 아니다.

- `route.outbound`는 C의 수송 **구간 ID** 목록이고 GTFS의 운행편별 정류장 방문/시각이 아니다.
- `segment.travel_s`는 C 근사 평가 입력의 **고정 구간 주행시간**이다. B가 정차·회차·차량 일정까지 실제 생성하지 않으면 셔틀 출발시각을 확정할 수 없다.
- `plan.to_service_plan()`은 `status=draft`, `schedule_status=pending_b`, `vehicle_duties=[]`, `trips=[]`를 반환한다.
- `scope_mode=single_bus_leg`는 **셔틀 단일 구간 평가 범위**이며 버스+지하철+도보 전체 여정 검색이 아니다.

D는 C 출력을 **운행안 후보 표시 데이터**로만 취급하고, 검증된 실제 일정·GTFS를 B에게 받는 별도 연계가 필요하다.

## 4. D 문서 수정 사항

| 이슈 | 이전 내용 | 문서 수정 |
| --- | --- | --- |
| 공통 `Scenario` | `suspended_route_ids` 단독 | `strike_route_ids`와 적용 의미 및 정책 참조 명시 |
| 검색 기준 | `scenario_id`만으로 검색 | M00 `JourneyRequest.plan_id`, 정확한 `revision`, `origin`, `destination`, `departure_at` 반영 |
| 검색 응답 | 이동 시간 중심 | M00 필수 `legs`, `arrival_at`, `plan_mode`, `warnings` 반영 |
| 오류 | 중첩 `error`, 임의 `STOP_NOT_FOUND` | M00 `code`/`message`/`details`/`request_id`, `NO_PATH`/`DATA_INCOMPLETE`/`OUT_OF_SCOPE` 구분 |
| 운영자 평가 | 시나리오별 수치만 | `plan_id`, `plan_revision`, `input_bundle_id`, 검증 상태, 평가 분모 추적 |
| GTFS | 필수 파일 중심 | 배차 `frequencies.txt`, 방향·재방문·원천 ID 매핑·출처 품질 점검 추가 |

**위 사항 중 API 경로, 외부 `plan_revision` 필드명, D 전용 확장 필드는 M08 팀 승인 전에는 계속 제안으로 유지한다.**

## 5. 팀 회의에서 결정할 질문

| 우선 | 질문 | 결정 주체/연결 작업 |
| --- | --- | --- |
| 1 | A의 정상 교통 GTFS를 어떤 출처·운행일·버전으로 받을 것인가? 서울 외곽 미매칭 정류장은 어떻게 처리할 것인가? | A → M06 |
| 2 | `Scenario.strike_route_ids`가 영향 노선인지 완전 운행중단 노선인지, 어떻게 필터할 것인가? | A·B·D / M00·ADR |
| 3 | C draft 및 B Draft PR #6의 내부 `Trip`에서 D가 소비할 정식 셔틀 GTFS/정차시각·검증 참조까지 무엇을 B가 내보내는가? | C·B → M06 |
| 4 | M08 검색 요청의 정확한 revision 필드명, 기본 운행안 선택·응답 버전 표시는? | D·전원 / M00·M08 |
| 5 | `JourneyResult` 외부 API 래퍼(`journeys[]`)와 추가 필드 소유권은? | D·전원 / M08·M10 |
| 6 | B가 검증하지 않은 C 추정 점수를 화면에서 어떤 상태로 표시할 것인가? `RunInputs` 일치를 어떻게 확인할 것인가? | B·C·D / M07·M08 |
| 7 | D01(지역/관측일), D02(평가 단위), D06(도보·환승), D07(대기/총 시간) 정책을 언제 결정할 것인가? | 팀 공통 / ADR |

## 6. D의 1~2일 차 완료·후속 업무 구분

**1~2일 차:** `01`·`02` 명세 초안과 본 정합성 리뷰를 검토, 팀 공통 계약과 다른 부분 명시, 팀 회의 질문 및 GitHub Issue/PR 생성.

**3~5일 차 이후:** A/B의 합성 입력으로 직행/환승·파업·셔틀 경로 검색; M00와 호환되는 API contract test; D08 기술 스택 합의 후 API·화면 개발. 현재 GTFS 헤더 검사가 전체 GTFS 유효성이나 실제 서비스 통합을 보증하지 않는다.

## 7. 공용 규칙 관련 주의

- D 폴더에 있던 오래된 `AGENTS.md`·`CONTRIBUTING.md` 사본은 루트 공용 규칙과 달라 제거했다. 작업 규칙은 저장소 루트 문서를 참조한다.
- 공용 `docs/contracts/M00_DATA_CONTRACT.md`, A/B/C 파일, `main`은 이 작업에서 수정하지 않는다.
- 최신 팀 규칙의 브랜치명은 `<type>/<issue-number>-<short-description>`, 커밋 제목은 **영어**, Issue 참조 필수이다. 기존 `docs/d-service-day1-2`는 이전 규칙으로 만들어진 작업 브랜치이므로 팀과 Issue를 연결한 PR 계획을 먼저 확인한다.

## 참고 경로

- [`docs/contracts/M00_DATA_CONTRACT.md`](https://github.com/OpenSwProject26-2/2team/blob/main/docs/contracts/M00_DATA_CONTRACT.md)
- [`opt/README.md`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/README.md)
- [`opt/ADR-001.md`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/ADR-001.md)
- [`opt/integration.py`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/integration.py)
- [`tests/opt/test_planning.py`](https://github.com/OpenSwProject26-2/2team/blob/main/tests/opt/test_planning.py)
- [`opt/examples/synthetic.json`](https://github.com/OpenSwProject26-2/2team/blob/main/opt/examples/synthetic.json)
- [`AGENTS.md`](https://github.com/OpenSwProject26-2/2team/blob/main/AGENTS.md) / [`CONTRIBUTING.md`](https://github.com/OpenSwProject26-2/2team/blob/main/CONTRIBUTING.md)
