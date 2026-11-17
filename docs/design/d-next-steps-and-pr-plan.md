# D팀 구현 현황·후속 개발 및 PR 계획

> 작성 기준: 2026-11-16 로컬 `feat/d-dashboard-mvp`. 이 문서는 팀 검토용 실행 계획이다. [상세 대시보드 설계](d-operator-dashboard-plan.md), [실제 구현 인수인계](d-operator-dashboard-handoff.md), [M00 공통 계약](../contracts/M00_DATA_CONTRACT.md)을 함께 읽는다. 승인되지 않은 API·스키마·평가 정책은 제안으로 남긴다.

> Draft PR 준비 상태: D API 18개·routing 28개 통과, 운영자/시민 localhost HTTP 응답 확인. Git 원본 LF의 임시 환경에서는 `main` 228개와 D 포함 274개 전체 테스트가 각각 통과했다(건너뜀 6개). 현재 Windows checkout은 `core.autocrlf=true`로 평가 fixture의 SHA-256이 달라져 `PYTHONUTF8=1`을 적용해도 26개가 실패한다. 브라우저 시각 수락은 URL 확인 실패로 미완료다. 실행 결과와 재현 조건은 [인수인계](d-operator-dashboard-handoff.md)에 기록한다.

## 1. 이번에 검토받을 범위와 완료 상태

| 영역 | 현재 구현 | 검증 근거 | 아직 아님 |
| --- | --- | --- | --- |
| 시민 경로 미리보기 | `src/busstrike/routing/gtfs_preview.py`, `preview_server.py`, `web/citizen/index.html`: 합성 정상·셔틀 GTFS의 직행·동일 정류장 최대 1회 환승, `plan_id`/revision·시뮬레이션 경고 | `tests/routing/` | A 실제 GTFS 경로, 보행·철도 환승, M08 정식 API |
| A 전달용 정상 교통 조회 | `src/busstrike/routing/normal_reachability.py`: 합성 정상 GTFS에 대한 도달 가능·도착 시각·환승 수·버전 예제 | `tests/routing/test_normal_reachability.py`, [경로 모듈 안내](../../src/busstrike/routing/README.md) | 실제 A OD ID 매핑, 잔여 수요 확정 |
| 운영자 로컬 API | `src/busstrike/api/`, `web/operator/serve.py`: 합성/실제 출처, 요약·노선·지도·수요 조회, 합성 시나리오 계산, A ZIP 새로고침 실패 시 이전 스냅샷 유지 | 자료 테스트 15개와 HTTP 경계 테스트 3개 | 공용 M08 계약, B/C/M07 산출물 연결 |
| 운영자 지도 | `web/operator/app.js`, `index.html`: 지도, 노선·정차점·역, 검색·레이어, 합성 수요 원의 크기·진하기, 합성 정상 노선 ON/OFF에 따른 가상 미충족 변화 | 로컬 HTML/API 응답, 합성 7노선·17 지도 feature·80 수요 셀, A GTFS 707노선·13,151정차점 확인 | 최신 화면 브라우저 육안 수락, 실제 OD·검증 평가 표시 |
| 선형 | 합성 버스 6개 노선의 저장된 OSM/OSRM 자동차도로 추정 선, A 철도 인접 역·선택 버스 정차 관계 점선 | 출처·좌표·정차점 대응 검사 | 실제 방향별 버스/철도 선형, 실제 공통 구간·운행거리 판정 |

수요 충족 계산은 **합성 OD와 가정한 시간당 정원에 대한 화면용 결정론적 배분**이다. B 시뮬레이션, M07 독립 검증 또는 실제 운행 성능으로 보고하지 않는다. A의 배포 GTFS에 `shapes.txt`가 없어 A 모드의 점선은 정차 관계 개략도다.

## 2. 이번 PR의 경계와 준비

현재 worktree는 최신 `main`에서 만들어졌으며 D 관련 코드·fixture·문서가 미커밋 상태로 이관돼 있다. 포함 파일 경계는 운영자 API·화면, 시민 합성 경로 미리보기, 관련 테스트 및 안내 문서다. 기존 원본 폴더와 `web/reference/` UI 자산은 복사·수정하지 않았다. 이관 작업에서 Git add/commit/push, 브랜치 변경, Issue/PR 생성은 수행하지 않았다. 로컬 A 배포 ZIP, 원시 교통카드, 개인 식별 자료는 PR에 넣지 않는다.

권장 검토 단위는 다음과 같다. Issue 번호와 PR 링크는 생성 후 채운다.

| 순서 | Issue/PR 단위 | 포함할 현재 파일 | 수락 조건·리뷰 대상 |
| --- | --- | --- | --- |
| 1 | D 운영자 로컬 API·합성 입력 | `src/busstrike/api/`, `tests/api/`, `tests/fixtures/d_dashboard/`, 관련 설계 | 출처·스냅샷·오류 분리, 결정론적 합성 결과, A ZIP 없음/실패 처리; A/B/C 계약 소비자는 참고 리뷰 |
| 2 | 지도·운영자 화면 | `web/operator/`, `web/README.md`, UI·오픈소스 문서 | DEMO/A 전환, 707노선·13,151점·509 철도 연결, 수요 원 크기·진하기, 실제/개략 선형 표시, 브라우저 캡처·수동 수락; D 화면 담당 리뷰 |
| 3 | 시민 경로 합성 미리보기 | `src/busstrike/routing/`, `tests/routing/`, `tests/fixtures/routing/`, `web/citizen/` | 직행·환승·서비스일·오류 응답을 합성 fixture로 확인; A/B 실제 시간표와 보행/철도 통합은 제외 |

현재 로컬 변경을 실제로 나누는 방법은 Git 작업 전에 `git status`, 각 파일의 범위, 기존 Draft PR과 `main`의 차이를 사람이 확인해 정한다. 공용 `contracts/`나 다른 팀 모듈은 이 PR에서 변경하지 않는다. 공용 `.gitattributes`에 `tests/evaluation/fixtures/** text eol=lf`를 추가하는 방안은 평가 fixture 소유자와 별도 PR에서 검토한다. 기존 checkout의 줄바꿈은 이 문서 작업에서 변경하지 않았다.

## 3. 작업 순서와 데이터 게이트

| 단계 | 할 일 | 완료 증거 | 필요한 결정·자료 |
| --- | --- | --- | --- |
| A. 현재 구현 수락 | ① 최신 브라우저에서 지도 타일·선택·줌·레이어·수요 원 확인 ② 화면 캡처와 API 예제 저장 ③ `tests/routing`·`tests/api`를 현재 HEAD에서 실행 | 브라우저 캡처, 검사 명령·결과, 실패 사례 | 브라우저/네트워크 접근; 사용자 UI 검토 |
| B. A 정상 경로 연동 | 실제 정상 GTFS를 시민 검색용으로 로드하고 서비스일·stop ID·trip 시각 검사. 철도 시간 역전은 임의 보정하지 않고 해당 경로를 판정 불가로 처리할 기준 합의 | 정상·무경로·지원 범위 밖·자료 부족 테스트, 사용한 피드 버전 | A의 정정/품질 보고, OD↔GTFS ID 대응, 보행 연결, 파업 `route_id` |
| C. 실제 수요 표시 | A 비식별·집계 OD를 합의된 공간·시간 단위 그대로 받아 지도와 표에 연결. 원래 인원 합계·중복 방지·미매칭률 검사 | 합계 보존과 미매칭 사례 계약 테스트 | A의 집계 OD·분모·시간 bin·좌표/정류장 매핑·공개 범위 |
| D. 후보·평가 연결 | C 후보, B 일정/시뮬레이션, M07 보고서를 `(plan_id, revision, RunInputs)`로 결합하고 비교 근거를 표시 | 버전/입력 불일치 거부, 기준안·개선안 같은 입력 비교, 평가 출처 배지 | B/C 산출물 manifest, 지표 정의와 실패 사유, M07 `fully_verified` 의미 |
| E. 선택·시민 안내 | 검증 게이트를 통과한 실험 계획만 선택 가능하게 하고 시민 검색은 고정된 `(plan_id, revision)`을 사용 | 미검증 안 선택 차단, revision 변경 시 재선택, 시뮬레이션 경고 | 선택 상태 소유자·권한, M08/D08/D09 ADR, 보행·환승 범위 |

선행 데이터가 없으면 해당 단계는 `DATA_INCOMPLETE`/미연결 등으로 표시하고, 합성 입력으로 독립 검증 가능한 단계만 진행한다. `selected`는 실험 선택이며 실제 운행 승인이 아니다.

## 4. 다른 팀에 보낼 요청

| 대상 | 전달받을 것 | D가 확인할 것 |
| --- | --- | --- |
| A | 비식별 집계 OD의 service date·시간 bin·`origin_stop_id`/`destination_stop_id`·인원·namespace·품질 보고; 파업 route ID; 정류장 매핑; 철도 시간 역전 3건의 정정/설명; 검증된 shape/도로 구간 제공 가능 여부 | GTFS ID와 OD ID 매칭, 합계·누락률, 근사 운행시각 여부, shape 출처 |
| B | 셔틀 GTFS·차량 일정, `(plan_id, revision)` manifest, service date, `RunInputs` 참조·체크섬, 시뮬레이션 결과·탑승 실패 원인·거리와 검증 상태 | A 정류장/시간표와 조인, 같은 입력 비교, C 후보와 revision 일치 |
| C | 기준안·개선안의 ID/revision·상태·차량 배분·노선/정차점·근사 `Assessment`와 입력 버전 | B 결과와 같은 계획인지, 근사 평가와 검증 평가 구별 |
| M07·전원 | `pass`와 `fully_verified`의 의미, 선택 가능 기준, 실패 시 표시·권한, M08 API/오류와 D06 보행·환승 범위 | 미검증 안이 시민 경로에 들어가지 않는지 |

## 5. PR 작성 전 사람이 확인할 항목

- [ ] Issue를 연결하고 브랜치/PR 범위를 위 표처럼 검토 가능한 크기로 나눴다.
- [ ] 로컬 변경과 기존 파일을 보존하고, 원본 ZIP·개인 자료·불필요한 참고 자산을 포함하지 않았다.
- [ ] 실제 실행한 테스트·브라우저 캡처와 실행하지 못한 검사 이유를 PR에 적었다.
- [ ] 합성 가정, A 근사 시간표, 실제 GTFS 정차 관계, B/M07 검증 결과를 서로 다른 상태로 표시했다.
- [ ] 계약·데이터·API·기술 선택의 미승인 항목을 명시하고 영향받는 팀 리뷰를 요청했다.
- [ ] 작성자 본인이 코드·자료·문서·오픈소스 출처를 확인했다. AGENTS.md가 요구하는 **AI 사용 도구·활용 범위·사람이 검토한 부분**을 PR에 사실대로 적었다.
- [ ] 작성자 외 팀원 리뷰, 최신 HEAD CI, 필수 대화 해결을 거친 뒤 병합한다.

PR 작성자는 실제 수행자 본인의 GitHub 계정과 이메일을 사용한다. AI가 작성한 초안·코드·문서의 사용 사실은 프로젝트 규약에 따라 공개하며, 기여자·검토자 또는 작성 이력을 사실과 다르게 표시하지 않는다. 이 문서 작성 과정에서 Git Commit/Push/Merge와 GitHub Issue/PR 생성·수정은 수행하지 않는다.
